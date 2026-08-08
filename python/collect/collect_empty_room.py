#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""Collect empty-room CSI windows using capture_csi_binary.py."""

import argparse
import csv
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from paths import CAPTURE_SCRIPT, PYTHON_ROOT, RAW_ROOT, VALIDATE_SCRIPT
from collect.validate_csi_captures import audit_file

DATASET_DIR = RAW_ROOT / 'empty'
DATASET_DIR_FAN_ON = RAW_ROOT / 'empty_fan_on'

DEFAULT_WINDOW_SEC = 120
DEFAULT_BREAK_SEC = 30
DEFAULT_SESSION_SEC = 3600
DEFAULT_EXIT_GRACE_SEC = 20
DEFAULT_MIN_CAPTURE_PACKETS = 2000


def beep(times=2):
    """Audible cue that a capture window is active."""
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


def next_serial(dataset_dir):
    dataset_dir.mkdir(parents=True, exist_ok=True)
    numbers = []
    for path in dataset_dir.glob('s*_empty.csv'):
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


def audit_capture(csv_path, min_raw_packets=DEFAULT_MIN_CAPTURE_PACKETS):
    """Quick post-capture quality check."""
    return audit_file(csv_path, min_raw=min_raw_packets)


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


def run_capture_window(port, baud, csv_path, log_path, window_sec, expected_len, expected_mac):
    stats_path = stats_path_for(csv_path)
    cmd = [
        sys.executable,
        str(CAPTURE_SCRIPT),
        '-p', port,
        '-b', str(baud),
        '-s', str(csv_path),
        '-l', str(log_path),
        '--expected-len', str(expected_len),
        '--expected-mac', expected_mac,
        '--report-interval', '30',
        '--duration', str(window_sec),
        '--stats-out', str(stats_path),
    ]
    print(f'  running capture for {window_sec}s')
    result = subprocess.run(cmd, cwd=str(PYTHON_ROOT), check=False)
    return result.returncode, read_stats_json(stats_path)


def append_manifest(manifest_path, row):
    write_header = not manifest_path.exists()
    with manifest_path.open('a', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=row.keys())
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def format_duration(seconds):
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:02d}:{minutes:02d}:{secs:02d}'


def wait_exit_grace(exit_grace_sec, log_handle=None):
    """Count down so the operator can leave before empty-room capture starts."""
    if exit_grace_sec <= 0:
        return
    print(f'>>> EXIT ROOM NOW — {int(exit_grace_sec)}s until capture starts <<<')
    if log_handle is not None:
        log_handle.write(f'exit_grace_sec={exit_grace_sec}\n')
        log_handle.flush()
    remaining = int(exit_grace_sec)
    while remaining > 0:
        print(f'>>> EXIT ROOM NOW — {remaining}s until capture starts <<<')
        time.sleep(1)
        remaining -= 1
    print('>>> Room should be empty — starting capture <<<')


def main():
    parser = argparse.ArgumentParser(
        description='Collect empty-room CSI dataset in timed 2-minute windows')
    parser.add_argument('-p', '--port', required=True, help='Serial port, e.g. /dev/ttyACM0')
    parser.add_argument('-b', '--baud', type=int, default=921600, help='Serial baud rate')
    parser.add_argument('--dataset-dir', default='', help='Output dataset directory')
    parser.add_argument('--fan-on', action='store_true',
                        help=f'Save to {DATASET_DIR_FAN_ON.name}/ (fan on, matches presence/motion/fall)')
    parser.add_argument('--window-sec', type=int, default=DEFAULT_WINDOW_SEC,
                        help='Capture duration per window (default 120s = 2 min)')
    parser.add_argument('--break-sec', type=int, default=DEFAULT_BREAK_SEC,
                        help='Break between windows (default 30s)')
    parser.add_argument('--session-sec', type=int, default=DEFAULT_SESSION_SEC,
                        help='Total session duration (default 3600s = 1 hour)')
    parser.add_argument('--expected-len', type=int, default=384, help='Expected CSI len (HT40=384)')
    parser.add_argument('--expected-mac', default='1a:00:00:00:00:00', help='TX MAC address')
    parser.add_argument('--start-serial', type=int, default=0,
                        help='Force first serial number (0 = auto from existing files)')
    parser.add_argument('--exit-grace-sec', type=int, default=DEFAULT_EXIT_GRACE_SEC,
                        help='Seconds to leave the room before the first capture window (default 20)')
    parser.add_argument('--min-packets', type=int, default=DEFAULT_MIN_CAPTURE_PACKETS,
                        help='Reject window if fewer raw CSI packets (moved to quarantine/)')
    parser.add_argument('--quarantine-bad', action='store_true', default=True,
                        help='Move failed quality checks to quarantine/ (default on)')
    parser.add_argument('--keep-bad', action='store_true',
                        help='Keep failed captures in dataset dir (disable quarantine)')
    parser.add_argument('--environment-notes', default='',
                        help='Free-text note on physical room state for this session '
                             '(e.g. "bag near TX removed") — recorded in the manifest '
                             'and session log since it cannot be reconstructed later.')
    args = parser.parse_args()
    if args.keep_bad:
        args.quarantine_bad = False

    if args.fan_on and args.dataset_dir:
        print('Use only one of --fan-on or --dataset-dir')
        return 1

    if args.fan_on:
        dataset_dir = DATASET_DIR_FAN_ON
        environment = 'fan_on'
    else:
        dataset_dir = Path(args.dataset_dir) if args.dataset_dir else DATASET_DIR
        environment = 'default'
    dataset_dir.mkdir(parents=True, exist_ok=True)

    cycle_sec = args.window_sec + args.break_sec
    total_windows = args.session_sec // cycle_sec
    if total_windows <= 0:
        print('session too short for one full window')
        return 1

    serial_no = args.start_serial if args.start_serial > 0 else next_serial(dataset_dir)
    session_stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    manifest_path = dataset_dir / f'empty_session_{session_stamp}.csv'
    session_log = dataset_dir / f'empty_session_{session_stamp}.log'

    print('=== Empty room dataset collection ===')
    print(f'dataset dir : {dataset_dir}')
    if environment == 'fan_on':
        print('environment : fan ON (use this set with presence/motion/fall for training)')
    print(f'session time: {format_duration(args.session_sec)}')
    print(f'window      : {args.window_sec}s capture + {args.break_sec}s break')
    print(f'windows     : {total_windows}')
    print(f'first file  : s{serial_no:03d}_empty.csv')
    print('Close idf.py monitor before starting.')
    print(f'exit grace : {args.exit_grace_sec}s to leave room after CSI stream begins')
    print('Two beeps = capture window started.')
    print()

    session_start = time.time()
    with session_log.open('w', encoding='utf-8') as log_handle:
        log_handle.write(f'started={dt.datetime.now().isoformat()}\n')
        log_handle.write(f'environment={environment}\n')
        if args.environment_notes:
            log_handle.write(f'environment_notes={args.environment_notes}\n')
        log_handle.write(f'port={args.port}\n')
        log_handle.write(f'windows={total_windows}\n')
        wait_exit_grace(args.exit_grace_sec, log_handle)

        for window_idx in range(1, total_windows + 1):
            elapsed = time.time() - session_start
            if elapsed >= args.session_sec:
                break

            csv_name = f's{serial_no:03d}_empty.csv'
            csv_path = dataset_dir / csv_name
            log_path = dataset_dir / f's{serial_no:03d}_empty.log'

            print(f'[{window_idx}/{total_windows}] WINDOW {csv_name} — CAPTURING {args.window_sec}s')
            log_handle.write(f'window={window_idx} file={csv_name} start={dt.datetime.now().isoformat()}\n')
            log_handle.flush()

            beep(times=2)

            window_start = time.time()
            exit_code, capture_stats = run_capture_window(
                args.port,
                args.baud,
                csv_path,
                log_path,
                args.window_sec,
                args.expected_len,
                args.expected_mac,
            )
            window_elapsed = time.time() - window_start
            packet_count = count_data_rows(csv_path)
            quality = audit_capture(csv_path, min_raw_packets=args.min_packets)
            quality_status = quality['status']
            quality_reason = quality.get('reason', '')

            if quality_status == 'fail' and args.quarantine_bad:
                qdir = dataset_dir / 'quarantine'
                qdir.mkdir(parents=True, exist_ok=True)
                for src in (csv_path, log_path, stats_path_for(csv_path)):
                    if src.exists():
                        dst = qdir / src.name
                        if dst.exists():
                            dst.unlink()
                        src.rename(dst)
                print(
                    f'  QUARANTINE ({quality_reason}) — '
                    f'raw={quality["raw_packets"]} drop={quality["drop_pct"]:.1f}%'
                )
                log_handle.write(
                    f'window={window_idx} QUARANTINE reason={quality_reason} '
                    f'packets={packet_count}\n'
                )
                log_handle.flush()
                serial_no += 1
                continue

            append_manifest(manifest_path, {
                'window': window_idx,
                'file': csv_name,
                'serial': serial_no,
                'label': 'empty',
                'environment': environment,
                'environment_notes': args.environment_notes,
                'started_at': dt.datetime.fromtimestamp(window_start).isoformat(timespec='seconds'),
                'duration_sec': round(window_elapsed, 1),
                'packets': packet_count,
                'capture_exit_code': exit_code,
                'quality': quality_status,
                'drop_pct': quality.get('drop_pct', 0.0),
                'kept_packets': quality.get('kept_packets', packet_count),
                'resync_bytes': capture_stats.get('resync_bytes', ''),
                'tx_missing_pct': capture_stats.get('tx_missing_pct', ''),
                'tx_resets': capture_stats.get('tx_resets', ''),
                'uart_missing_pct': capture_stats.get('uart_missing_pct', ''),
                'uart_resets': capture_stats.get('uart_resets', ''),
            })

            qual_note = ''
            if quality_status == 'warn':
                qual_note = f'  [warn: {quality_reason}]'
            print(f'  saved {packet_count} packets -> {csv_path.name}{qual_note}')
            log_handle.write(
                f'window={window_idx} file={csv_name} packets={packet_count} '
                f'exit={exit_code} end={dt.datetime.now().isoformat()}\n'
            )
            log_handle.flush()
            serial_no += 1

            if window_idx >= total_windows:
                break

            print(f'  BREAK {args.break_sec}s — room should stay empty and still')
            for remaining in range(args.break_sec, 0, -1):
                print(f'    next window in {remaining:2d}s', end='\r', flush=True)
                time.sleep(1)
            print(' ' * 40, end='\r', flush=True)

    print()
    print('=== Session complete ===')
    print(f'manifest: {manifest_path}')
    print(f'session log: {session_log}')
    print()
    print('Next steps:')
    print(f'  python3 {VALIDATE_SCRIPT} --dir {dataset_dir} --with-windows')
    print(f'  python3 {PYTHON_ROOT / "preprocess" / "preprocess_csi.py"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
