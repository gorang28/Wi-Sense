#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""Guided presence collection for all 10 room spots in one session."""

import argparse
import datetime as dt
import sys
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from collect.collect_presence import (
    DEFAULT_BREAK_SEC,
    DEFAULT_MOVE_SEC,
    DEFAULT_WINDOW_SEC,
    DEFAULT_WINDOWS,
    DATASET_ROOT,
    countdown,
    run_position_session,
    wait_for_enter,
)

# Order matches the user's 10 floor marks.
PRESENCE_SPOTS = (
    ('window_tx', 'Spot 1/10 — Window, near TX', 'Stand still'),
    ('top_left_tx', 'Spot 2/10 — Top-left corner, near TX', 'Stand still'),
    ('near_rx', 'Spot 3/10 — Near RX (ESP32-S3)', 'Stand still'),
    ('room_center', 'Spot 4/10 — Center of room', 'Stand still'),
    ('door_corner', 'Spot 5/10 — Door corner', 'Stand still'),
    ('chair_table', 'Spot 6/10 — Table/chair', 'Sit still'),
    ('bed_sit', 'Spot 7/10 — Bed edge', 'Sit still'),
    ('fridge_corner', 'Spot 8/10 — Near refrigerator', 'Stand still'),
    ('almirah', 'Spot 9/10 — Near almirah', 'Stand still'),
    ('bed_corner', 'Spot 10/10 — Bed corner', 'Stand still'),
)

POSITION_BY_SLUG = {slug: idx + 1 for idx, (slug, _, _) in enumerate(PRESENCE_SPOTS)}


def count_spot_files(position):
    folder = DATASET_ROOT / position
    if not folder.exists():
        return 0
    return len(list(folder.glob(f's*_{position}.csv')))


def detect_resume_spot(windows_per_spot):
    for idx, (position, _, _) in enumerate(PRESENCE_SPOTS, start=1):
        if count_spot_files(position) < windows_per_spot:
            return idx
    return len(PRESENCE_SPOTS) + 1


def resolve_start_spot(args):
    if args.from_position:
        slug = args.from_position.strip().lower()
        if slug not in POSITION_BY_SLUG:
            print('Unknown position:', slug)
            print('Valid:', ', '.join(POSITION_BY_SLUG))
            return None
        return POSITION_BY_SLUG[slug]
    if args.resume:
        spot = detect_resume_spot(args.windows)
        if spot > len(PRESENCE_SPOTS):
            print('All spots already have', args.windows, 'files each. Nothing to resume.')
            return None
        return spot
    return args.start_spot


def main():
    parser = argparse.ArgumentParser(
        description='Collect presence CSI at all 10 room spots with guided breaks')
    parser.add_argument('-p', '--port', required=True, help='Serial port, e.g. /dev/ttyACM0')
    parser.add_argument('-b', '--baud', type=int, default=921600, help='Serial baud rate')
    parser.add_argument('--window-sec', type=int, default=DEFAULT_WINDOW_SEC,
                        help='Capture per window (default 120s)')
    parser.add_argument('--break-sec', type=int, default=DEFAULT_BREAK_SEC,
                        help='Break between windows at same spot (default 30s)')
    parser.add_argument('--windows', type=int, default=DEFAULT_WINDOWS,
                        help='Windows per spot (default 8)')
    parser.add_argument('--move-sec', type=int, default=DEFAULT_MOVE_SEC,
                        help='Seconds to walk to next spot after Enter (default 20)')
    parser.add_argument('--expected-len', type=int, default=384, help='Expected CSI len')
    parser.add_argument('--expected-mac', default='1a:00:00:00:00:00', help='TX MAC')
    parser.add_argument('--start-spot', type=int, default=5,
                        help='Start at spot number 1-10 (default 5 = door_corner)')
    parser.add_argument('--from-position', default='',
                        help='Start at position slug, e.g. door_corner')
    parser.add_argument('--resume', action='store_true',
                        help='Auto-start at first spot with fewer than 8 CSV files')
    parser.add_argument('--environment-notes', default='',
                        help='Free-text note on physical room state for this session '
                             '(e.g. "bag near TX removed") — recorded in every spot\'s '
                             'manifest and the master session log.')
    args = parser.parse_args()

    start_spot = resolve_start_spot(args)
    if start_spot is None:
        return 1

    if start_spot < 1 or start_spot > len(PRESENCE_SPOTS):
        print('start spot must be between 1 and', len(PRESENCE_SPOTS))
        return 1

    spots = len(PRESENCE_SPOTS) - start_spot + 1
    DATASET_ROOT.mkdir(parents=True, exist_ok=True)
    session_stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    master_log = DATASET_ROOT / f'presence_full_session_{session_stamp}.log'

    per_spot_min = (args.windows * (args.window_sec + args.break_sec) - args.break_sec) / 60
    total_min = per_spot_min * spots

    print('=== Presence full session (10 spots) ===')
    print(f'spots       : {spots} (starting at spot {start_spot}: {PRESENCE_SPOTS[start_spot - 1][0]})')
    print(f'per spot    : {args.windows} windows x {args.window_sec}s')
    print(f'est. total  : {total_min:.0f} min')
    print('Close idf.py monitor before starting.')
    print()
    print('Flow for each spot:')
    print('  1) Press Enter when on the mark')
    print('  2) 20s countdown to settle')
    print('  3) Two beeps = recording ON (stay still 2 min)')
    print('  4) One beep = recording OFF (rest 30s, adjust posture)')
    print('  5) Repeat 8 rounds, then walk to next spot')
    print()

    wait_for_enter(
        f'Press Enter when the subject is on spot {start_spot} '
        f'({PRESENCE_SPOTS[start_spot - 1][1]}) and ready... '
    )

    with master_log.open('w') as master_handle:
        master_handle.write(f'started={dt.datetime.now().isoformat()}\n')
        master_handle.write(f'start_spot={start_spot}\n')
        if args.environment_notes:
            master_handle.write(f'environment_notes={args.environment_notes}\n')

        for spot_idx in range(start_spot - 1, len(PRESENCE_SPOTS)):
            position, label, posture = PRESENCE_SPOTS[spot_idx]

            print()
            print('=' * 60)
            print(label)
            print(f'Posture: {posture}')
            print('=' * 60)

            master_handle.write(
                f'spot={spot_idx + 1} position={position} start={dt.datetime.now().isoformat()}\n'
            )
            master_handle.flush()

            run_position_session(
                args.port,
                position,
                baud=args.baud,
                window_sec=args.window_sec,
                break_sec=args.break_sec,
                total_windows=args.windows,
                expected_len=args.expected_len,
                expected_mac=args.expected_mac,
                spot_label=label,
                ready_sec=args.move_sec,
                environment_notes=args.environment_notes,
            )

            master_handle.write(
                f'spot={spot_idx + 1} position={position} end={dt.datetime.now().isoformat()}\n'
            )
            master_handle.flush()

            if spot_idx >= len(PRESENCE_SPOTS) - 1:
                break

            _, next_label, next_posture = PRESENCE_SPOTS[spot_idx + 1]
            print()
            print(f'Finished {label}.')
            print(f'NEXT -> {next_label}')
            print(f'        Posture: {next_posture}')
            wait_for_enter('Walk to the next mark, then press Enter when ready... ')

    print()
    print('=== All spots complete ===')
    print(f'session log: {master_log}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
