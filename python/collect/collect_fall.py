#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""Collect fall CSI reps with solo Method B — fixed fall cue at t=10s."""

import argparse
import csv
import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from paths import CAPTURE_SCRIPT, PYTHON_ROOT, RAW_ROOT
from collect.validate_csi_captures import audit_file

DEFAULT_FALL_DATASET_ROOT = RAW_ROOT / 'fall_v2'
DATASET_ROOT = DEFAULT_FALL_DATASET_ROOT  # overridden by --dataset-root in main()
# Fall reps are ~30s (1/4 of presence/motion's 120s window); scale the
# minimum raw-packet floor proportionally rather than reusing the 120s value.
DEFAULT_MIN_CAPTURE_PACKETS = 500

VALID_SCENARIOS = (
    'fall_center',
    'fall_near_door',
    'fall_bed_edge',
)

SCENARIO_META = {
    'fall_center': {
        'mark': 'room_center',
        'instruction': 'Stand on mat at room_center → controlled sit-drop when you hear 3 beeps.',
    },
    'fall_near_door': {
        'mark': 'door_corner',
        'instruction': 'Stand on mat at door_corner → controlled sit-drop when you hear 3 beeps.',
    },
    'fall_bed_edge': {
        'mark': 'bed_sit',
        'instruction': 'Seated on bed edge, feet down → slide/lean to mat when you hear 3 beeps.',
    },
}

DEFAULT_DURATION_SEC = 30
DEFAULT_REPS = 8
DEFAULT_READY_SEC = 20
DEFAULT_REST_SEC = 10
DEFAULT_FALL_OFFSET_SEC = 10.0
DEFAULT_FALL_WINDOW_SEC = 3.0
DEFAULT_FALL_COUNTDOWN_SEC = 5


def beep(times=2):
    """Play terminal/system beep."""
    for _ in range(times):
        print('\a', end='', flush=True)
        played = False
        for cmd in (
            ['paplay', '/usr/share/sounds/freedesktop/stereo/message-new-instant.ogg'],
            ['aplay', '/usr/share/sounds/alsa/Front_Center.wav'],
            ['speaker-test', '-t', 'sine', '-f', '880', '-l', '1'],
        ):
            try:
                subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                    check=False,
                )
                played = True
                break
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                continue
        if not played:
            time.sleep(0.15)
        time.sleep(0.2)


def beep_record_start():
    print('  [BEEP x2] Recording started — stand still')
    beep(times=2)


def beep_fall_cue():
    print('  [BEEP x3] FALL NOW')
    beep(times=3)


def beep_record_end():
    print('  [BEEP x1] Recording done — rest')
    beep(times=1)


def normalize_scenario(name):
    slug = re.sub(r'[^a-z0-9]+', '_', name.strip().lower()).strip('_')
    if not slug:
        raise ValueError('scenario name is empty')
    return slug


def next_serial(dataset_dir, scenario):
    dataset_dir.mkdir(parents=True, exist_ok=True)
    numbers = []
    pattern = f's*_{scenario}.csv'
    for path in dataset_dir.glob(pattern):
        try:
            numbers.append(int(path.stem.split('_')[0][1:]))
        except (IndexError, ValueError):
            continue
    return (max(numbers) + 1) if numbers else 1


def count_data_rows(csv_path):
    if not csv_path.exists():
        return 0
    with csv_path.open(newline='') as handle:
        rows = sum(1 for _ in csv.reader(handle))
    return max(0, rows - 1)


def wait_until(start_time, target_elapsed):
    while time.time() - start_time < target_elapsed:
        time.sleep(0.05)


def append_manifest(manifest_path, row):
    write_header = not manifest_path.exists()
    with manifest_path.open('a', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=row.keys())
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def wait_for_enter(prompt):
    try:
        input(prompt)
    except EOFError:
        print('(non-interactive mode, continuing)')


def countdown(seconds, message):
    print(message)
    for remaining in range(seconds, 0, -1):
        print(f'  starting in {remaining:2d}s — get on your mark', end='\r', flush=True)
        time.sleep(1)
    print(' ' * 50, end='\r', flush=True)


def rest_countdown(seconds):
    print(f'  REST {seconds}s — get up slowly, return to mark')
    for remaining in range(seconds, 0, -1):
        print(f'    next rep in {remaining:2d}s', end='\r', flush=True)
        time.sleep(1)
    print(' ' * 40, end='\r', flush=True)


def stats_path_for(csv_path):
    return csv_path.with_suffix('.stats.json')


def read_stats_json(stats_path):
    """Read the transport-quality stats capture_csi_binary.py writes via
    --stats-out (resync/tx-missing/uart-missing counters). Returns {} if the
    capture failed before writing it or used an older script version."""
    if not stats_path.exists():
        return {}
    try:
        return json.loads(stats_path.read_text())
    except (OSError, ValueError):
        return {}


def audit_capture(csv_path, min_raw_packets=DEFAULT_MIN_CAPTURE_PACKETS):
    """Preprocessing-level quality check (drop_pct/kept_packets/status) —
    same logic collect_empty_room.py already uses. Recorded in the manifest
    only, not acted on: no auto-quarantine here."""
    return audit_file(csv_path, min_raw=min_raw_packets)


def start_capture_process(port, baud, csv_path, log_path, duration_sec, expected_len, expected_mac):
    cmd = [
        sys.executable,
        str(CAPTURE_SCRIPT),
        '-p', port,
        '-b', str(baud),
        '-s', str(csv_path),
        '-l', str(log_path),
        '--expected-len', str(expected_len),
        '--expected-mac', expected_mac,
        '--report-interval', '15',
        '--duration', str(duration_sec),
        '--stats-out', str(stats_path_for(csv_path)),
    ]
    print(f'  running capture for {duration_sec}s')
    return subprocess.Popen(cmd, cwd=str(PYTHON_ROOT))


def run_capture_with_fall_cue(
    port,
    baud,
    csv_path,
    log_path,
    *,
    duration_sec,
    expected_len,
    expected_mac,
    fall_offset_sec,
    fall_countdown_sec,
):
    """Start capture, cue fall at fall_offset_sec with countdown + 3 beeps."""
    proc = start_capture_process(
        port, baud, csv_path, log_path, duration_sec, expected_len, expected_mac,
    )
    window_start = time.time()

    beep_record_start()

    countdown_start = fall_offset_sec - fall_countdown_sec
    if countdown_start < 0:
        raise ValueError('fall countdown must fit before fall_offset_sec')

    wait_until(window_start, countdown_start)
    for remaining in range(fall_countdown_sec, 0, -1):
        wait_until(window_start, fall_offset_sec - remaining)
        print(f'  Fall in {remaining}...')

    wait_until(window_start, fall_offset_sec)
    beep_fall_cue()

    exit_code = proc.wait()
    beep_record_end()
    capture_stats = read_stats_json(stats_path_for(csv_path))
    return exit_code, window_start, capture_stats


def run_scenario_session(
    port,
    scenario,
    *,
    baud=921600,
    dataset_dir=None,
    duration_sec=DEFAULT_DURATION_SEC,
    total_reps=DEFAULT_REPS,
    ready_sec=DEFAULT_READY_SEC,
    rest_sec=DEFAULT_REST_SEC,
    fall_offset_sec=DEFAULT_FALL_OFFSET_SEC,
    fall_window_sec=DEFAULT_FALL_WINDOW_SEC,
    fall_countdown_sec=DEFAULT_FALL_COUNTDOWN_SEC,
    expected_len=384,
    expected_mac='1a:00:00:00:00:00',
    start_serial=0,
    scenario_label='',
    prompt_each_rep=False,
    environment_notes='',
):
    scenario = normalize_scenario(scenario)
    if scenario not in SCENARIO_META:
        raise ValueError(f'unknown scenario: {scenario}')

    meta = SCENARIO_META[scenario]
    mark = meta['mark']
    instruction = meta['instruction']
    dataset_dir = Path(dataset_dir) if dataset_dir else DEFAULT_FALL_DATASET_ROOT / scenario
    dataset_dir.mkdir(parents=True, exist_ok=True)

    if total_reps <= 0:
        raise ValueError('reps must be > 0')

    serial_no = start_serial if start_serial > 0 else next_serial(dataset_dir, scenario)
    session_stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    manifest_path = dataset_dir / f'fall_{scenario}_{session_stamp}.csv'
    session_log = dataset_dir / f'fall_{scenario}_{session_stamp}.log'

    if scenario_label:
        print(f'\n>>> SCENARIO: {scenario_label}')
    print(f'scenario    : {scenario}')
    print(f'mark        : {mark}')
    print(f'script      : {instruction}')
    print(f'dataset dir : {dataset_dir}')
    print(f'reps        : {total_reps} x {duration_sec}s (+ {rest_sec}s rest)')
    print(f'fall cue    : t={fall_offset_sec}s (window ±{fall_window_sec / 2}s)')
    print(f'first file  : s{serial_no:03d}_{scenario}.csv')
    print('Beeps: 2 = recording ON | 3 = FALL NOW | 1 = recording OFF')
    print()

    with session_log.open('w', encoding='utf-8') as log_handle:
        log_handle.write(f'label=fall\nscenario={scenario}\nmark={mark}\n')
        log_handle.write(f'instruction={instruction}\n')
        log_handle.write(f'fall_offset_sec={fall_offset_sec}\n')
        log_handle.write(f'fall_window_sec={fall_window_sec}\n')
        if scenario_label:
            log_handle.write(f'scenario_label={scenario_label}\n')
        if environment_notes:
            log_handle.write(f'environment_notes={environment_notes}\n')
        log_handle.write(f'started={dt.datetime.now().isoformat()}\n')
        log_handle.write(f'reps={total_reps}\n')

        for rep_idx in range(1, total_reps + 1):
            if prompt_each_rep:
                wait_for_enter(
                    f'[{rep_idx}/{total_reps}] Press Enter when on mark ({mark}) and ready... '
                )

            if rep_idx == 1 and ready_sec > 0:
                countdown(ready_sec, f'Get in position — {ready_sec}s until first recording.')
            elif rep_idx > 1:
                print(f'[{rep_idx}/{total_reps}] Next rep — get back on mark during rest')

            csv_name = f's{serial_no:03d}_{scenario}.csv'
            csv_path = dataset_dir / csv_name
            log_path = dataset_dir / f's{serial_no:03d}_{scenario}.log'

            print(f'[{rep_idx}/{total_reps}] REP {csv_name} — CAPTURING {duration_sec}s')
            print(f'  >> {instruction}')
            log_handle.write(f'rep={rep_idx} file={csv_name} start={dt.datetime.now().isoformat()}\n')
            log_handle.flush()

            exit_code, window_start, capture_stats = run_capture_with_fall_cue(
                port,
                baud,
                csv_path,
                log_path,
                duration_sec=duration_sec,
                expected_len=expected_len,
                expected_mac=expected_mac,
                fall_offset_sec=fall_offset_sec,
                fall_countdown_sec=fall_countdown_sec,
            )
            packet_count = count_data_rows(csv_path)
            quality = audit_capture(csv_path)

            append_manifest(manifest_path, {
                'rep': rep_idx,
                'file': csv_name,
                'serial': serial_no,
                'label': 'fall',
                'scenario': scenario,
                'mark': mark,
                'scenario_label': scenario_label,
                'instruction': instruction,
                'fall_offset_sec': fall_offset_sec,
                'fall_window_sec': fall_window_sec,
                'environment_notes': environment_notes,
                'started_at': dt.datetime.fromtimestamp(window_start).isoformat(timespec='seconds'),
                'duration_sec': duration_sec,
                'packets': packet_count,
                'capture_exit_code': exit_code,
                'quality': quality['status'],
                'drop_pct': quality.get('drop_pct', 0.0),
                'kept_packets': quality.get('kept_packets', packet_count),
                'resync_bytes': capture_stats.get('resync_bytes', ''),
                'tx_missing_pct': capture_stats.get('tx_missing_pct', ''),
                'tx_resets': capture_stats.get('tx_resets', ''),
                'uart_missing_pct': capture_stats.get('uart_missing_pct', ''),
                'uart_resets': capture_stats.get('uart_resets', ''),
            })

            print(f'  saved {packet_count} packets -> {csv_path.name}')
            log_handle.write(
                f'rep={rep_idx} file={csv_name} packets={packet_count} '
                f'exit={exit_code} end={dt.datetime.now().isoformat()}\n'
            )
            log_handle.flush()
            serial_no += 1

            if rep_idx >= total_reps:
                break

            rest_countdown(rest_sec)

    print(f'=== Scenario complete: {scenario} ===')
    print(f'manifest: {manifest_path}')
    return manifest_path, session_log


def main():
    parser = argparse.ArgumentParser(
        description='Collect fall CSI dataset for one scenario (solo Method B)')
    parser.add_argument('-p', '--port', required=True, help='Serial port, e.g. /dev/ttyACM0')
    parser.add_argument('--scenario', required=True,
                        help=f'Scenario slug: {", ".join(VALID_SCENARIOS)}')
    parser.add_argument('-b', '--baud', type=int, default=921600, help='Serial baud rate')
    parser.add_argument(
        '--dataset-root',
        default='',
        help='Fall dataset root (default: data/raw/fall_v2).',
    )
    parser.add_argument('--dataset-dir', default='', help='Override output directory for this scenario')
    parser.add_argument('--duration-sec', type=int, default=DEFAULT_DURATION_SEC,
                        help='Capture duration per rep (default 30s)')
    parser.add_argument('--reps', type=int, default=DEFAULT_REPS,
                        help='Number of fall repetitions (default 8)')
    parser.add_argument('--ready-sec', type=int, default=DEFAULT_READY_SEC,
                        help='Countdown before first rep at this position (default 20s)')
    parser.add_argument('--rest-sec', type=int, default=DEFAULT_REST_SEC,
                        help='Rest between reps (default 10s)')
    parser.add_argument('--fall-offset-sec', type=float, default=DEFAULT_FALL_OFFSET_SEC,
                        help='Seconds after recording start to cue fall (default 10)')
    parser.add_argument('--fall-window-sec', type=float, default=DEFAULT_FALL_WINDOW_SEC,
                        help='Fall label window width in manifest (default 3)')
    parser.add_argument('--expected-len', type=int, default=384, help='Expected CSI len (HT40=384)')
    parser.add_argument('--expected-mac', default='1a:00:00:00:00:00', help='TX MAC address')
    parser.add_argument('--start-serial', type=int, default=0,
                        help='Force first serial number (0 = auto)')
    parser.add_argument('--environment-notes', default='',
                        help='Free-text note on physical room state for this session '
                             '(e.g. "bag near TX removed") — recorded in the manifest '
                             'and session log since it cannot be reconstructed later.')
    args = parser.parse_args()

    scenario = normalize_scenario(args.scenario)
    reps_default = 10 if scenario == 'fall_center' else 8
    total_reps = args.reps if args.reps != DEFAULT_REPS else reps_default

    per_rep_min = (args.duration_sec + args.rest_sec) / 60
    session_min = per_rep_min * total_reps - args.rest_sec / 60

    dataset_root = Path(args.dataset_root) if args.dataset_root else DEFAULT_FALL_DATASET_ROOT
    if args.dataset_dir:
        dataset_dir = Path(args.dataset_dir)
    else:
        dataset_dir = dataset_root / scenario

    print('=== Fall dataset collection (single scenario, solo Method B) ===')
    print(f'est. time   : {session_min:.0f} min')
    print(f'dataset dir : {dataset_dir}')
    print('Close idf.py monitor before starting.')
    print('Use a mat. Start with slow controlled sit-drops.')
    print()

    mark = SCENARIO_META[scenario]['mark']
    wait_for_enter(f'Press Enter when on mark ({mark}) and ready... ')

    run_scenario_session(
        args.port,
        scenario,
        baud=args.baud,
        dataset_dir=dataset_dir,
        duration_sec=args.duration_sec,
        total_reps=total_reps,
        ready_sec=args.ready_sec,
        rest_sec=args.rest_sec,
        fall_offset_sec=args.fall_offset_sec,
        fall_window_sec=args.fall_window_sec,
        expected_len=args.expected_len,
        expected_mac=args.expected_mac,
        start_serial=args.start_serial,
        environment_notes=args.environment_notes,
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
