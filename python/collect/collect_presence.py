#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""Collect presence CSI windows (still person) at a marked room position."""

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

DEFAULT_MIN_CAPTURE_PACKETS = 2000

DATASET_ROOT = RAW_ROOT / 'presence'

VALID_POSITIONS = (
    'window_tx',
    'top_left_tx',
    'near_rx',
    'room_center',
    'door_corner',
    'chair_table',
    'bed_sit',
    'fridge_corner',
    'almirah',
    'bed_corner',
)

DEFAULT_WINDOW_SEC = 120
DEFAULT_BREAK_SEC = 30
DEFAULT_WINDOWS = 8
DEFAULT_MOVE_SEC = 20


def beep_rest():
    """One beep: 2-minute recording finished, rest period."""
    print('  [BEEP x1] Recording done — rest / adjust posture')
    beep(times=1)


def beep_record_start():
    """Two beeps: recording is starting, stay still."""
    print('  [BEEP x2] Recording started — stay still')
    beep(times=2)
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


def normalize_position(name):
    slug = re.sub(r'[^a-z0-9]+', '_', name.strip().lower()).strip('_')
    if not slug:
        raise ValueError('position name is empty')
    return slug


def next_serial(dataset_dir, position):
    dataset_dir.mkdir(parents=True, exist_ok=True)
    numbers = []
    pattern = f's*_{position}.csv'
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
    exit_code = subprocess.run(cmd, cwd=str(PYTHON_ROOT), check=False).returncode
    return exit_code, read_stats_json(stats_path)


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


def run_position_session(
    port,
    position,
    *,
    baud=921600,
    dataset_dir=None,
    window_sec=DEFAULT_WINDOW_SEC,
    break_sec=DEFAULT_BREAK_SEC,
    total_windows=DEFAULT_WINDOWS,
    expected_len=384,
    expected_mac='1a:00:00:00:00:00',
    start_serial=0,
    spot_label='',
    ready_sec=0,
    environment_notes='',
):
    position = normalize_position(position)
    dataset_dir = Path(dataset_dir) if dataset_dir else DATASET_ROOT / position
    dataset_dir.mkdir(parents=True, exist_ok=True)

    if total_windows <= 0:
        raise ValueError('windows must be > 0')

    serial_no = start_serial if start_serial > 0 else next_serial(dataset_dir, position)
    session_stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    manifest_path = dataset_dir / f'presence_{position}_{session_stamp}.csv'
    session_log = dataset_dir / f'presence_{position}_{session_stamp}.log'

    if spot_label:
        print(f'\n>>> SPOT: {spot_label}')
    print(f'position    : {position}')
    print(f'dataset dir : {dataset_dir}')
    print(f'windows     : {total_windows} x {window_sec}s (+ {break_sec}s break)')
    print(f'first file  : s{serial_no:03d}_{position}.csv')
    print('Beeps: 2 beeps = recording ON (stay still) | 1 beep = recording OFF (rest 30s)')
    print()

    if ready_sec > 0:
        countdown(ready_sec, f'Get on your mark — {ready_sec}s until first recording.')

    with session_log.open('w', encoding='utf-8') as log_handle:
        log_handle.write(f'label=presence\nposition={position}\n')
        if spot_label:
            log_handle.write(f'spot_label={spot_label}\n')
        if environment_notes:
            log_handle.write(f'environment_notes={environment_notes}\n')
        log_handle.write(f'started={dt.datetime.now().isoformat()}\n')
        log_handle.write(f'windows={total_windows}\n')

        for window_idx in range(1, total_windows + 1):
            csv_name = f's{serial_no:03d}_{position}.csv'
            csv_path = dataset_dir / csv_name
            log_path = dataset_dir / f's{serial_no:03d}_{position}.log'

            print(f'[{window_idx}/{total_windows}] WINDOW {csv_name} — CAPTURING {window_sec}s')
            log_handle.write(f'window={window_idx} file={csv_name} start={dt.datetime.now().isoformat()}\n')
            log_handle.flush()

            beep_record_start()

            window_start = time.time()
            exit_code, capture_stats = run_capture_window(
                port,
                baud,
                csv_path,
                log_path,
                window_sec,
                expected_len,
                expected_mac,
            )
            packet_count = count_data_rows(csv_path)
            quality = audit_capture(csv_path)

            append_manifest(manifest_path, {
                'window': window_idx,
                'file': csv_name,
                'serial': serial_no,
                'label': 'presence',
                'position': position,
                'spot_label': spot_label,
                'environment_notes': environment_notes,
                'started_at': dt.datetime.fromtimestamp(window_start).isoformat(timespec='seconds'),
                'duration_sec': round(time.time() - window_start, 1),
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
                f'window={window_idx} file={csv_name} packets={packet_count} '
                f'exit={exit_code} end={dt.datetime.now().isoformat()}\n'
            )
            log_handle.flush()
            serial_no += 1

            if window_idx >= total_windows:
                break

            beep_rest()
            print(f'  REST {break_sec}s — you may adjust posture')
            for remaining in range(break_sec, 0, -1):
                print(f'    next recording in {remaining:2d}s', end='\r', flush=True)
                time.sleep(1)
            print(' ' * 40, end='\r', flush=True)

    print(f'=== Spot complete: {position} ===')
    print(f'manifest: {manifest_path}')
    return manifest_path, session_log


def main():
    parser = argparse.ArgumentParser(
        description='Collect presence CSI dataset at one room position')
    parser.add_argument('-p', '--port', required=True, help='Serial port, e.g. /dev/ttyACM0')
    parser.add_argument('--position', required=True,
                        help=f'Position slug. Suggested: {", ".join(VALID_POSITIONS)}')
    parser.add_argument('-b', '--baud', type=int, default=921600, help='Serial baud rate')
    parser.add_argument('--dataset-dir', default='', help='Override output directory')
    parser.add_argument('--window-sec', type=int, default=DEFAULT_WINDOW_SEC,
                        help='Capture duration per window (default 120s)')
    parser.add_argument('--break-sec', type=int, default=DEFAULT_BREAK_SEC,
                        help='Break between windows (default 30s)')
    parser.add_argument('--windows', type=int, default=DEFAULT_WINDOWS,
                        help='Number of capture windows for this position (default 8)')
    parser.add_argument('--expected-len', type=int, default=384, help='Expected CSI len (HT40=384)')
    parser.add_argument('--expected-mac', default='1a:00:00:00:00:00', help='TX MAC address')
    parser.add_argument('--start-serial', type=int, default=0,
                        help='Force first serial number (0 = auto)')
    parser.add_argument('--ready-sec', type=int, default=DEFAULT_MOVE_SEC,
                        help='Countdown before first window at this spot (default 20s)')
    parser.add_argument('--environment-notes', default='',
                        help='Free-text note on physical room state for this session '
                             '(e.g. "bag near TX removed") — recorded in the manifest '
                             'and session log since it cannot be reconstructed later.')
    args = parser.parse_args()

    position = normalize_position(args.position)
    session_minutes = (args.windows * (args.window_sec + args.break_sec) - args.break_sec) / 60

    print('=== Presence dataset collection (single spot) ===')
    print(f'est. time   : {session_minutes:.0f} min')
    print('Close idf.py monitor before starting.')
    print()

    run_position_session(
        args.port,
        position,
        baud=args.baud,
        dataset_dir=Path(args.dataset_dir) if args.dataset_dir else None,
        window_sec=args.window_sec,
        break_sec=args.break_sec,
        total_windows=args.windows,
        expected_len=args.expected_len,
        expected_mac=args.expected_mac,
        start_serial=args.start_serial,
        ready_sec=args.ready_sec,
        environment_notes=args.environment_notes,
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
