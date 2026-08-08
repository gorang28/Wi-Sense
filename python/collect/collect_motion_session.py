#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""Guided motion collection for all 11 room activities in one session."""

import argparse
import datetime as dt
import sys
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from collect.collect_motion import (
    ACTIVITY_INSTRUCTIONS,
    DEFAULT_BREAK_SEC,
    DEFAULT_READY_SEC,
    DEFAULT_WINDOW_SEC,
    DEFAULT_WINDOWS,
    DATASET_ROOT,
    VALID_ACTIVITIES,
    run_activity_session,
    wait_for_enter,
)

# Order tuned for collection flow: easy paths first, bed_motion near the end.
MOTION_ACTIVITIES = (
    ('walk_tx_to_rx', 'Activity 1/11 — Walk TX to RX', ACTIVITY_INSTRUCTIONS['walk_tx_to_rx']),
    ('pace_door_center', 'Activity 2/11 — Pace door to center', ACTIVITY_INSTRUCTIONS['pace_door_center']),
    ('pace_center_fridge', 'Activity 3/11 — Pace center to fridge', ACTIVITY_INSTRUCTIONS['pace_center_fridge']),
    ('pace_bed_door', 'Activity 4/11 — Pace beside bed', ACTIVITY_INSTRUCTIONS['pace_bed_door']),
    ('enter_from_door', 'Activity 5/11 — Enter from door', ACTIVITY_INSTRUCTIONS['enter_from_door']),
    ('march_in_place_center', 'Activity 6/11 — March in place', ACTIVITY_INSTRUCTIONS['march_in_place_center']),
    ('perimeter_walk', 'Activity 7/11 — Perimeter walk', ACTIVITY_INSTRUCTIONS['perimeter_walk']),
    ('arm_wave_center', 'Activity 8/11 — Arm waves at center', ACTIVITY_INSTRUCTIONS['arm_wave_center']),
    ('desk_motion_chair', 'Activity 9/11 — Desk motion seated', ACTIVITY_INSTRUCTIONS['desk_motion_chair']),
    ('walk_top_left_tx', 'Activity 10/11 — Walk top-left to center', ACTIVITY_INSTRUCTIONS['walk_top_left_tx']),
    ('bed_motion', 'Activity 11/11 — Bed motion', ACTIVITY_INSTRUCTIONS['bed_motion']),
)

ACTIVITY_BY_SLUG = {slug: idx + 1 for idx, (slug, _, _) in enumerate(MOTION_ACTIVITIES)}


def ensure_activity_folders():
    DATASET_ROOT.mkdir(parents=True, exist_ok=True)
    for activity in VALID_ACTIVITIES:
        (DATASET_ROOT / activity).mkdir(parents=True, exist_ok=True)


def count_activity_files(activity):
    folder = DATASET_ROOT / activity
    if not folder.exists():
        return 0
    return len(list(folder.glob(f's*_{activity}.csv')))


def detect_resume_activity(windows_per_activity):
    for idx, (activity, _, _) in enumerate(MOTION_ACTIVITIES, start=1):
        if count_activity_files(activity) < windows_per_activity:
            return idx
    return len(MOTION_ACTIVITIES) + 1


def resolve_start_activity(args):
    if args.from_activity:
        slug = args.from_activity.strip().lower()
        if slug not in ACTIVITY_BY_SLUG:
            print('Unknown activity:', slug)
            print('Valid:', ', '.join(ACTIVITY_BY_SLUG))
            return None
        return ACTIVITY_BY_SLUG[slug]
    if args.resume:
        activity_idx = detect_resume_activity(args.windows)
        if activity_idx > len(MOTION_ACTIVITIES):
            print('All activities already have', args.windows, 'files each. Nothing to resume.')
            return None
        return activity_idx
    return args.start_activity


def main():
    parser = argparse.ArgumentParser(
        description='Collect motion CSI for all 11 room activities with guided breaks')
    parser.add_argument('-p', '--port', required=True, help='Serial port, e.g. /dev/ttyACM0')
    parser.add_argument('-b', '--baud', type=int, default=921600, help='Serial baud rate')
    parser.add_argument('--window-sec', type=int, default=DEFAULT_WINDOW_SEC,
                        help='Capture per window (default 120s)')
    parser.add_argument('--break-sec', type=int, default=DEFAULT_BREAK_SEC,
                        help='Break between windows at same activity (default 30s)')
    parser.add_argument('--windows', type=int, default=DEFAULT_WINDOWS,
                        help='Windows per activity (default 8)')
    parser.add_argument('--ready-sec', type=int, default=DEFAULT_READY_SEC,
                        help='Seconds to get ready after Enter before first window (default 20)')
    parser.add_argument('--expected-len', type=int, default=384, help='Expected CSI len')
    parser.add_argument('--expected-mac', default='1a:00:00:00:00:00', help='TX MAC')
    parser.add_argument('--start-activity', type=int, default=1,
                        help='Start at activity number 1-11 (default 1)')
    parser.add_argument('--from-activity', default='',
                        help='Start at activity slug, e.g. pace_bed_door')
    parser.add_argument('--resume', action='store_true',
                        help='Auto-start at first activity with fewer than 8 CSV files')
    parser.add_argument('--environment-notes', default='',
                        help='Free-text note on physical room state for this session '
                             '(e.g. "bag near TX removed") — recorded in every activity\'s '
                             'manifest and the master session log.')
    args = parser.parse_args()

    start_activity = resolve_start_activity(args)
    if start_activity is None:
        return 1

    if start_activity < 1 or start_activity > len(MOTION_ACTIVITIES):
        print('start activity must be between 1 and', len(MOTION_ACTIVITIES))
        return 1

    ensure_activity_folders()

    activities_remaining = len(MOTION_ACTIVITIES) - start_activity + 1
    session_stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    master_log = DATASET_ROOT / f'motion_full_session_{session_stamp}.log'

    per_activity_min = (args.windows * (args.window_sec + args.break_sec) - args.break_sec) / 60
    total_min = per_activity_min * activities_remaining

    print('=== Motion full session (11 activities) ===')
    print(
        f'activities  : {activities_remaining} '
        f'(starting at {start_activity}: {MOTION_ACTIVITIES[start_activity - 1][0]})'
    )
    print(f'per activity: {args.windows} windows x {args.window_sec}s')
    print(f'est. total  : {total_min:.0f} min')
    print('Close idf.py monitor before starting.')
    print()
    print('Flow for each activity:')
    print('  1) Press Enter when at the starting position')
    print('  2) 20s countdown to get ready')
    print('  3) Two beeps = recording ON (keep moving 2 min)')
    print('  4) One beep = recording OFF (rest 30s, stop moving)')
    print('  5) Repeat 8 rounds, then move to next activity')
    print()

    wait_for_enter(
        f'Press Enter when the subject is ready for activity {start_activity} '
        f'({MOTION_ACTIVITIES[start_activity - 1][1]})... '
    )

    with master_log.open('w') as master_handle:
        master_handle.write(f'started={dt.datetime.now().isoformat()}\n')
        master_handle.write(f'start_activity={start_activity}\n')
        if args.environment_notes:
            master_handle.write(f'environment_notes={args.environment_notes}\n')

        for activity_idx in range(start_activity - 1, len(MOTION_ACTIVITIES)):
            activity, label, instruction = MOTION_ACTIVITIES[activity_idx]

            print()
            print('=' * 60)
            print(label)
            print(f'Script: {instruction}')
            print('=' * 60)

            master_handle.write(
                f'activity={activity_idx + 1} slug={activity} start={dt.datetime.now().isoformat()}\n'
            )
            master_handle.flush()

            run_activity_session(
                args.port,
                activity,
                baud=args.baud,
                window_sec=args.window_sec,
                break_sec=args.break_sec,
                total_windows=args.windows,
                expected_len=args.expected_len,
                expected_mac=args.expected_mac,
                activity_label=label,
                ready_sec=args.ready_sec,
                environment_notes=args.environment_notes,
            )

            master_handle.write(
                f'activity={activity_idx + 1} slug={activity} end={dt.datetime.now().isoformat()}\n'
            )
            master_handle.flush()

            if activity_idx >= len(MOTION_ACTIVITIES) - 1:
                break

            _, next_label, next_instruction = MOTION_ACTIVITIES[activity_idx + 1]
            print()
            print(f'Finished {label}.')
            print(f'NEXT -> {next_label}')
            print(f'        Script: {next_instruction}')
            wait_for_enter('Move to the next starting position, then press Enter when ready... ')

    print()
    print('=== All activities complete ===')
    print(f'session log: {master_log}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
