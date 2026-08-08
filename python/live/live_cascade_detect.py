#!/usr/bin/env python3
"""Run the trained CSI cascade on the binary serial stream in real time."""

import argparse
import json
import sys
import time
from collections import deque
from pathlib import Path

import joblib
import numpy as np
import serial

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from capture.capture_csi_binary import BinaryStreamParser
from preprocess.preprocess_csi import (EXPECTED_LEN, MAX_NORM_PKT_ABS,
                                       extract_features, is_corrupt_iq_packet,
                                       parse_csi_amplitude, parse_csi_iq)


CSI_TIMESTAMP_WRAP_US = 1 << 32
MAX_SEQUENCE_GAP = 2


def timestamp_from_row(row):
    """Read ESP-IDF's wrapping uint32 CSI timestamp, if the frame has one."""
    try:
        return int(row['local_timestamp']) & (CSI_TIMESTAMP_WRAP_US - 1)
    except (KeyError, TypeError, ValueError):
        return None


class TimestampUnwrapper:
    """Turn wrapping CSI timestamps into a monotonic microsecond clock."""

    def __init__(self):
        self._raw = None
        self._elapsed = 0

    def push(self, timestamp):
        if timestamp is None:
            return None
        if self._raw is None:
            self._raw = timestamp
            return self._elapsed
        delta = (timestamp - self._raw) & (CSI_TIMESTAMP_WRAP_US - 1)
        self._raw = timestamp
        # A backwards jump greater than half the uint32 range is a receiver
        # reset/out-of-order packet, not a normal timestamp wrap.  Start a new
        # feature window rather than mixing unrelated CSI samples.
        if delta > CSI_TIMESTAMP_WRAP_US // 2:
            self._elapsed = 0
            return None
        self._elapsed += delta
        return self._elapsed


def starts_new_capture_segment(previous_id, current_id):
    """Match preprocessing's rule for genuine transmitter packet-loss gaps."""
    if previous_id is None or current_id is None:
        return False
    delta = (current_id - previous_id) & 0xFFFFFFFF
    # A delta of 3 means two packets were lost and is still allowed by the
    # preprocessing window rule.  Split only when more than two are missing.
    return delta == 0 or delta > MAX_SEQUENCE_GAP + 1


def load_runtime(model_path, config_path):
    bundle = joblib.load(model_path)
    config = json.loads(Path(config_path).read_text(encoding='utf-8'))
    if bundle.get('feature_dim') != config.get('feature_dim'):
        raise ValueError('model and preprocessing config use different feature dimensions')
    baseline = np.asarray(config['reference_baseline'], dtype=np.float32)
    return bundle, config, baseline


def raw_amplitude_from_row(row):
    """Convert one decoded serial frame into the pre-baseline amplitude."""
    try:
        if int(row.get('len', 0)) != EXPECTED_LEN:
            return None
        i_vals, q_vals = parse_csi_iq(row['data'], expected_len=EXPECTED_LEN)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if is_corrupt_iq_packet(i_vals, q_vals):
        return None
    amplitude = parse_csi_amplitude(row['data'], expected_len=EXPECTED_LEN)
    if amplitude is None:
        return None
    return amplitude.astype(np.float32)


def amplitude_from_row(row, baseline):
    """Apply the selected empty-room baseline to one valid CSI packet."""
    amplitude = raw_amplitude_from_row(row)
    if amplitude is None:
        return None
    normalized = amplitude - baseline
    if float(np.max(np.abs(normalized))) > MAX_NORM_PKT_ABS:
        return None
    return normalized.astype(np.float32)


def new_decision_state():
    """Smoothing, debounce and fall state carried across prediction windows."""
    return {'fall_streak': 0, 'occupied': False, 'motion': False,
            'occupied_pending': 0, 'motion_pending': 0,
            'smooth': {}, 'motion_hist': deque(), 'fall_until_us': None,
            'still_run': 0}


def _smooth(state, key, value, alpha):
    """Exponential moving average of a stage probability.

    Single-window probabilities swing hard when a person is near the decision
    boundary (standing still reads 0.29 one window and 1.00 the next).
    Averaging first, then thresholding, is what actually removes the flicker —
    debouncing the boolean alone just delays it.
    """
    if alpha >= 1.0:
        return value
    previous = state['smooth'].get(key)
    smoothed = value if previous is None else alpha * value + (1.0 - alpha) * previous
    state['smooth'][key] = smoothed
    return smoothed


def _debounce(state, key, raw, on_required, off_required=None):
    """Flip a latched boolean only after enough consecutive disagreements.

    Asymmetric on purpose: turning a state ON should be quick, turning it OFF
    should need more evidence.  A person sitting perfectly still produces a
    weak, wandering signal, and a symmetric rule keeps dropping them to EMPTY
    between windows.
    """
    if off_required is None:
        off_required = on_required
    pending = f'{key}_pending'
    if raw == state[key]:
        state[pending] = 0
    else:
        state[pending] += 1
        if state[pending] >= (on_required if raw else off_required):
            state[key] = raw
            state[pending] = 0
    return state[key]


def _rule_fall(state, timestamp_us, motion_p, occupied, args):
    """Heuristic fall: a burst of motion that stops abruptly while someone is
    still in the room.

    The trained fall stage does not generalise — it has ~127 positive windows
    from ten recordings of one fall at one spot, and scores held-out falls at
    0.55 mean.  This rule instead keys off the occupied/motion stages, which
    are backed by orders of magnitude more data.  It is a heuristic, not a
    classifier: abruptly stopping a brisk walk produces the same signature.
    """
    history = state['motion_hist']
    history.append((timestamp_us, motion_p))
    lookback_us = int(args.fall_lookback_sec * 1_000_000)
    while history and timestamp_us - history[0][0] > lookback_us:
        history.popleft()

    if state['fall_until_us'] is not None:
        if timestamp_us < state['fall_until_us'] and occupied:
            return True
        state['fall_until_us'] = None

    if not occupied or len(history) < 3:
        state['still_run'] = 0
        return False

    # Require stillness to persist, not just touch the threshold for one
    # window, so a momentary dip mid-movement cannot trigger an alarm.
    if motion_p <= args.fall_motion_low:
        state['still_run'] = state.get('still_run', 0) + 1
    else:
        state['still_run'] = 0

    peak = max(value for _, value in history)
    if peak >= args.fall_motion_high and state['still_run'] >= args.fall_still_consecutive:
        state['fall_until_us'] = timestamp_us + int(args.fall_hold_sec * 1_000_000)
        state['still_run'] = 0
        history.clear()
        return True
    return False


def predict_label(bundle, feature, state, args, timestamp_us):
    raw = {
        name: float(model.predict_proba(feature.reshape(1, -1))[0, 1])
        for name, model in bundle['models'].items()
    }
    thresholds = bundle['thresholds']
    occ_thr = (args.occupied_threshold if args.occupied_threshold is not None
               else thresholds['occupied'])
    mot_thr = (args.motion_threshold if args.motion_threshold is not None
               else thresholds['motion'])
    p_occ = _smooth(state, 'occupied', raw['occupied'], args.smoothing)
    p_mot = _smooth(state, 'motion', raw['motion'], args.smoothing)
    p_fall = _smooth(state, 'fall', raw['fall'], args.smoothing)

    occupied = _debounce(
        state, 'occupied', p_occ >= occ_thr,
        args.occupied_consecutive, args.occupied_off_consecutive,
    )
    motion = _debounce(
        state, 'motion', p_mot >= mot_thr, args.motion_consecutive,
    )

    state['fall_streak'] = (
        state['fall_streak'] + 1 if p_fall >= thresholds['fall'] else 0
    )
    if args.fall_mode == 'ml':
        fall = state['fall_streak'] >= args.fall_consecutive
    elif args.fall_mode == 'rule':
        fall = _rule_fall(state, timestamp_us, p_mot, occupied, args)
    else:
        fall = False

    if fall:
        label = 'FALL'
    elif not occupied:
        label = 'EMPTY'
    elif motion:
        label = 'MOTION'
    else:
        label = 'PRESENCE'
    return label, {'occupied': p_occ, 'motion': p_mot, 'fall': p_fall}, state


def main():
    parser = argparse.ArgumentParser(description='Live CSI cascade detection')
    parser.add_argument('-p', '--port', required=True, help='Receiver serial port, e.g. COM13')
    parser.add_argument('-b', '--baud', type=int, default=921600)
    parser.add_argument('--model', default='models/csi_cascade.joblib')
    parser.add_argument('--config', default='data/dataset/processed/preprocess_config.json')
    parser.add_argument('--window-seconds', type=float, default=None,
                        help='CSI duration per feature window (default: preprocessing config)')
    parser.add_argument('--hop-seconds', type=float, default=0.25,
                        help='Minimum CSI time between predictions (default: 0.25)')
    parser.add_argument('--min-window-coverage', type=float, default=0.95,
                        help='Required fraction of the time window before prediction (default: 0.95)')
    parser.add_argument('--packet-window', action='store_true',
                        help='Use the old fixed-packet window for comparison only; not model-compatible')
    parser.add_argument('--hop-packets', type=int, default=22,
                        help='Prediction hop for --packet-window only (default: 22)')
    parser.add_argument('--calibration-packets', type=int, default=0,
                        help='Recalibrate the baseline from N packets of the CURRENT empty room '
                             'instead of the saved training baseline. Use this when the saved '
                             'baseline is stale (room never reads EMPTY). ~300 is about 3.5s.')
    parser.add_argument('--calibration-grace-sec', type=int, default=15,
                        help='Seconds to leave the room before calibration starts '
                             '(only used with --calibration-packets). Calibrating while you '
                             'stand at the laptop bakes YOU into the baseline, after which the '
                             'room never reads EMPTY.')
    parser.add_argument('--log-file', default='',
                        help='Also write output here (UTF-8). Use this instead of piping '
                             'through PowerShell Tee-Object, which buffers, writes UTF-16, '
                             'and swallows Ctrl+C.')
    parser.add_argument('--smoothing', type=float, default=0.4,
                        help='EMA factor for stage probabilities: 1.0 = no smoothing, '
                             'lower = steadier but slower to react (default 0.4)')
    parser.add_argument('--fall-mode', choices=('rule', 'ml', 'off'), default='rule',
                        help="'rule' (default): motion burst that stops abruptly while "
                             "someone is present — uses the well-supported occupied/motion "
                             "stages. 'ml': the trained fall stage, which does not "
                             "generalise beyond its ten training recordings. 'off': never "
                             'report FALL.')
    parser.add_argument('--fall-motion-high', type=float, default=0.45,
                        help='Motion probability that counts as the burst before a fall')
    parser.add_argument('--fall-motion-low', type=float, default=0.08,
                        help='Motion probability that counts as stillness after a fall')
    parser.add_argument('--fall-still-consecutive', type=int, default=2,
                        help='Consecutive still windows required after the motion burst '
                             'before FALL is declared')
    parser.add_argument('--fall-lookback-sec', type=float, default=3.0,
                        help='How recently the motion burst must have happened')
    parser.add_argument('--fall-hold-sec', type=float, default=6.0,
                        help='Seconds to hold the FALL state once triggered')
    parser.add_argument('--occupied-threshold', type=float, default=None,
                        help='Override the occupied threshold stored in the model')
    parser.add_argument('--motion-threshold', type=float, default=None,
                        help='Override the motion threshold stored in the model')
    parser.add_argument('--occupied-consecutive', type=int, default=2,
                        help='Consecutive windows required to enter occupied (debounce)')
    parser.add_argument('--occupied-off-consecutive', type=int, default=2,
                        help='Consecutive windows required to drop back to EMPTY. Higher '
                             'than the ON count so a very still person is not lost '
                             'between windows.')
    parser.add_argument('--motion-consecutive', type=int, default=2,
                        help='Consecutive windows required to flip PRESENCE<->MOTION '
                             '(debounce; 1 disables)')
    parser.add_argument('--fall-consecutive', type=int, default=3,
                        help='Consecutive fall-positive predictions required for FALL. '
                             'Was raised to 6 to suppress false alarms from the old '
                             'model; the 2026-07-30 retrain separates falls (median '
                             '0.99) from lying/empty (p99 0.002/0.068) well enough that '
                             '3 is both responsive and safe. Raise it if false alarms '
                             'appear during walking.')
    args = parser.parse_args()
    if not 0 < args.smoothing <= 1:
        parser.error('--smoothing must be in (0, 1]')

    log_handle = open(args.log_file, 'w', encoding='utf-8') if args.log_file else None

    def emit(line):
        print(line, flush=True)
        if log_handle is not None:
            log_handle.write(line + '\n')
            log_handle.flush()

    bundle, config, baseline = load_runtime(args.model, args.config)
    packet_count = int(config['target_seq_2s'])
    window_seconds = (
        float(config.get('window_2s_sec', 2.0))
        if args.window_seconds is None else args.window_seconds
    )
    if window_seconds <= 0 or args.hop_seconds <= 0:
        parser.error('--window-seconds and --hop-seconds must be positive')
    if not 0 < args.min_window_coverage <= 1:
        parser.error('--min-window-coverage must be in (0, 1]')
    window_us = int(round(window_seconds * 1_000_000))
    min_window_us = int(round(window_us * args.min_window_coverage))
    hop_us = int(round(args.hop_seconds * 1_000_000))
    expected_mac = config['expected_mac']
    buffer = deque(maxlen=packet_count) if args.packet_window else deque()
    stream = BinaryStreamParser(expected_mac=expected_mac, expected_len=EXPECTED_LEN)
    timestamp_clock = TimestampUnwrapper()
    accepted = rejected = since_prediction = 0
    detect_start = None
    decision_state = new_decision_state()
    previous_packet_id = None
    segment = 0
    last_prediction_us = None
    calibration = []
    calibrating = args.calibration_packets > 0

    emit(f'Opening {args.port} at {args.baud} baud')
    if calibrating:
        emit(f'Keep the room empty: calibrating from {args.calibration_packets} valid CSI packets...')
    else:
        emit('Using the saved training baseline (the model-compatible default).')
    if args.packet_window:
        emit(f'Legacy packet mode: waiting for {packet_count} valid CSI packets before first prediction...')
    else:
        emit(
            f'Using timestamped {window_seconds:.2f}s CSI windows; '
            f'first prediction follows one continuous window.'
        )
    emit('Close idf.py monitor or any other serial program first. Press Ctrl+C to stop.')

    try:
        with serial.Serial(args.port, args.baud, timeout=0.1) as ser:
            if calibrating and args.calibration_grace_sec > 0:
                # Calibrating the instant the script starts bakes the operator
                # (standing at the laptop) into the "empty" baseline, after
                # which leaving the room reads as a large change, i.e. occupied.
                # Mirrors wait_exit_grace() in collect_empty_room.py.
                for remaining in range(args.calibration_grace_sec, 0, -1):
                    emit(f'>>> LEAVE THE ROOM NOW - calibration starts in {remaining}s <<<')
                    time.sleep(1)
                ser.reset_input_buffer()
                stream = BinaryStreamParser(expected_mac=expected_mac, expected_len=EXPECTED_LEN)
                emit('>>> Calibrating now - keep the room empty <<<')
            while True:
                chunk = ser.read(4096)
                if not chunk:
                    continue
                stream.feed(chunk)
                while True:
                    frame, _discarded = stream.pop_frame()
                    if frame is None:
                        break
                    columns, values, _seq = frame
                    row = dict(zip(columns, values))

                    timestamp_us = timestamp_clock.push(timestamp_from_row(row))
                    try:
                        packet_id = int(row['id'])
                    except (KeyError, TypeError, ValueError):
                        packet_id = None
                    if starts_new_capture_segment(previous_packet_id, packet_id):
                        segment += 1
                    previous_packet_id = packet_id
                    if not args.packet_window and timestamp_us is None:
                        # The time window must never bridge a receiver reset or
                        # an unparseable timestamp.  This mirrors the training
                        # path's exclusion of real capture gaps.
                        if buffer:
                            # Rebuilding a full window takes ~2 s of silence,
                            # which reads as the program having hung.
                            emit('... stream gap — rebuilding window (~2s)')
                        buffer.clear()
                        last_prediction_us = None
                        rejected += 1
                        continue

                    raw_amplitude = raw_amplitude_from_row(row)
                    if raw_amplitude is None:
                        rejected += 1
                        continue
                    if calibrating:
                        calibration.append(raw_amplitude)
                        if len(calibration) < args.calibration_packets:
                            continue
                        baseline = np.median(np.asarray(calibration), axis=0).astype(np.float32)
                        calibration.clear()
                        calibrating = False
                        buffer.clear()
                        last_prediction_us = None
                        # Latched state was built against the pre-calibration
                        # baseline; it means nothing once the baseline changes.
                        decision_state = new_decision_state()
                        emit('Empty-room calibration complete. Detections active.')
                        continue

                    amplitude = raw_amplitude - baseline
                    if float(np.max(np.abs(amplitude))) > MAX_NORM_PKT_ABS:
                        rejected += 1
                        continue
                    accepted += 1
                    if args.packet_window:
                        buffer.append(amplitude)
                        since_prediction += 1
                        if len(buffer) < packet_count or since_prediction < args.hop_packets:
                            continue
                        since_prediction = 0
                        feature_window = np.asarray(buffer, dtype=np.float32)
                        window_span = None
                    else:
                        buffer.append((timestamp_us, segment, amplitude))
                        while buffer and timestamp_us - buffer[0][0] > window_us:
                            buffer.popleft()
                        # Do not create a feature from samples separated by a
                        # true TX packet-loss gap.  Content-filtered samples do
                        # not change ``segment``, matching preprocessing.
                        while buffer and buffer[0][1] != segment:
                            buffer.popleft()
                        if len(buffer) < 2:
                            continue
                        window_span = timestamp_us - buffer[0][0]
                        # With a rolling buffer capped at ``window_us``, the
                        # newest sample normally lands one CSI period short of
                        # the exact boundary because the old sample is evicted
                        # first.  Accept a nearly full window instead of
                        # waiting forever for an impossible exact span.
                        if window_span < min_window_us:
                            continue
                        if (last_prediction_us is not None
                                and timestamp_us - last_prediction_us < hop_us):
                            continue
                        last_prediction_us = timestamp_us
                        feature_window = np.asarray(
                            [sample[2] for sample in buffer], dtype=np.float32,
                        )

                    feature = extract_features(feature_window)
                    label, prob, decision_state = predict_label(
                        bundle, feature, decision_state, args,
                        timestamp_us if timestamp_us is not None else accepted * 10_000,
                    )
                    window_info = (
                        f'samples={feature_window.shape[0]} '
                        f'span={window_span / 1_000_000:.2f}s '
                        if window_span is not None else
                        f'samples={feature_window.shape[0]} legacy-packet-window '
                    )
                    if detect_start is None:
                        detect_start = time.time()
                    emit(
                        f't={time.time() - detect_start:6.1f}s {label:8} | '
                        f'occ={prob["occupied"]:.2f} '
                        f'mot={prob["motion"]:.2f} fall={prob["fall"]:.2f} '
                        f'{window_info}packets={accepted} rejected={rejected}'
                    )
    except serial.SerialException as exc:
        print(f'serial error: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        emit('stopped')
    finally:
        if log_handle is not None:
            log_handle.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
