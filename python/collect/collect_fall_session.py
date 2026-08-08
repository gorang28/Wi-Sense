#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""Guided solo fall collection — 3 scenarios, Method B fixed cue at t=10s."""

import argparse
import datetime as dt
import sys
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from collect.collect_fall import (
    DEFAULT_DURATION_SEC,
    DEFAULT_FALL_OFFSET_SEC,
    DEFAULT_FALL_DATASET_ROOT,
    DEFAULT_READY_SEC,
    DEFAULT_REST_SEC,
    SCENARIO_META,
    VALID_SCENARIOS,
    run_scenario_session,
    wait_for_enter,
)

# Order: center first (most reps), then door, then bed edge.
FALL_SCENARIOS = (
    ('fall_center', 'Scenario 1/3 — Fall at room center', 10),
    ('fall_near_door', 'Scenario 2/3 — Fall at door corner', 8),
    ('fall_bed_edge', 'Scenario 3/3 — Fall from bed edge', 8),
)

SCENARIO_BY_SLUG = {slug: idx + 1 for idx, (slug, _, _) in enumerate(FALL_SCENARIOS)}


def ensure_scenario_folders(dataset_root):
    dataset_root.mkdir(parents=True, exist_ok=True)
    for scenario in VALID_SCENARIOS:
        (dataset_root / scenario).mkdir(parents=True, exist_ok=True)


def count_scenario_files(scenario, dataset_root):
    folder = dataset_root / scenario
    if not folder.exists():
        return 0
    return len(list(folder.glob(f's*_{scenario}.csv')))


def target_reps_for(scenario):
    for slug, _, reps in FALL_SCENARIOS:
        if slug == scenario:
            return reps
    return 8


def detect_resume_scenario(dataset_root):
    for idx, (scenario, _, reps) in enumerate(FALL_SCENARIOS, start=1):
        if count_scenario_files(scenario, dataset_root) < reps:
            return idx
    return len(FALL_SCENARIOS) + 1


def resolve_start_scenario(args, dataset_root):
    if args.from_scenario:
        slug = args.from_scenario.strip().lower()
        if slug not in SCENARIO_BY_SLUG:
            print('Unknown scenario:', slug)
            print('Valid:', ', '.join(SCENARIO_BY_SLUG))
            return None
        return SCENARIO_BY_SLUG[slug]
    if args.resume:
        scenario_idx = detect_resume_scenario(dataset_root)
        if scenario_idx > len(FALL_SCENARIOS):
            print('All scenarios complete. Nothing to resume.')
            return None
        return scenario_idx
    return args.start_scenario


def main():
    parser = argparse.ArgumentParser(
        description='Collect fall CSI — solo Method B, 3 scenarios, fixed fall at 10s')
    parser.add_argument('-p', '--port', required=True, help='Serial port, e.g. /dev/ttyACM0')
    parser.add_argument('-b', '--baud', type=int, default=921600, help='Serial baud rate')
    parser.add_argument('--duration-sec', type=int, default=DEFAULT_DURATION_SEC,
                        help='Capture per rep (default 30s)')
    parser.add_argument('--ready-sec', type=int, default=DEFAULT_READY_SEC,
                        help='Countdown before first rep at each position (default 20s)')
    parser.add_argument('--rest-sec', type=int, default=DEFAULT_REST_SEC,
                        help='Rest between reps (default 10s)')
    parser.add_argument('--fall-offset-sec', type=float, default=DEFAULT_FALL_OFFSET_SEC,
                        help='Fall cue time after recording start (default 10s)')
    parser.add_argument('--expected-len', type=int, default=384, help='Expected CSI len')
    parser.add_argument('--expected-mac', default='1a:00:00:00:00:00', help='TX MAC')
    parser.add_argument('--start-scenario', type=int, default=1,
                        help='Start at scenario 1-3 (default 1)')
    parser.add_argument('--from-scenario', default='',
                        help='Start at scenario slug, e.g. fall_near_door')
    parser.add_argument('--resume', action='store_true',
                        help='Auto-start at first scenario with incomplete reps')
    parser.add_argument(
        '--dataset-root',
        default='',
        help='Fall dataset root (default: data/raw/fall_v2).',
    )
    parser.add_argument('--environment-notes', default='',
                        help='Free-text note on physical room state for this session '
                             '(e.g. "bag near TX removed") — recorded in every scenario\'s '
                             'manifest and the master session log.')
    args = parser.parse_args()

    dataset_root = Path(args.dataset_root) if args.dataset_root else DEFAULT_FALL_DATASET_ROOT
    start_scenario = resolve_start_scenario(args, dataset_root)
    if start_scenario is None:
        return 1

    if start_scenario < 1 or start_scenario > len(FALL_SCENARIOS):
        print('start scenario must be between 1 and', len(FALL_SCENARIOS))
        return 1

    ensure_scenario_folders(dataset_root)

    scenarios_remaining = len(FALL_SCENARIOS) - start_scenario + 1
    session_stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    master_log = dataset_root / f'fall_full_session_{session_stamp}.log'

    total_reps = sum(
        reps for idx, (_, _, reps) in enumerate(FALL_SCENARIOS, start=1)
        if idx >= start_scenario
    )
    per_rep_sec = args.duration_sec + args.rest_sec
    total_min = (total_reps * per_rep_sec - args.rest_sec) / 60

    print('=== Fall full session (solo Method B) ===')
    print(f'dataset root: {dataset_root}')
    print(
        f'scenarios   : {scenarios_remaining} '
        f'(starting at {start_scenario}: {FALL_SCENARIOS[start_scenario - 1][0]})'
    )
    print(f'total reps  : {total_reps}')
    print(f'fall cue    : t={args.fall_offset_sec}s (3 beeps)')
    print(f'est. time   : {total_min:.0f} min')
    print('Close idf.py monitor before starting.')
    print('Place yoga mat at each mark. Door stays closed.')
    print()
    print('Flow per position (Enter once when mat is placed):')
    print('  1) Press Enter — then 20s countdown (first rep only)')
    print('  2) 2 beeps = recording ON — stand still')
    print('  3) "Fall in 5... 4... 3... 2... 1..." → 3 beeps at 10s = FALL NOW')
    print('  4) 1 beep = done; 10s rest → next rep starts automatically')
    print('  5) New position: move mat, Press Enter, repeat from step 1')
    print()

    slug, label, _ = FALL_SCENARIOS[start_scenario - 1]
    mark = SCENARIO_META[slug]['mark']
    wait_for_enter(
        f'Place mat at {mark}. Press Enter when ready for {label}... '
    )

    with master_log.open('w') as master_handle:
        master_handle.write(f'started={dt.datetime.now().isoformat()}\n')
        master_handle.write(f'start_scenario={start_scenario}\n')
        master_handle.write(f'fall_offset_sec={args.fall_offset_sec}\n')
        if args.environment_notes:
            master_handle.write(f'environment_notes={args.environment_notes}\n')

        for scenario_idx in range(start_scenario - 1, len(FALL_SCENARIOS)):
            scenario, scenario_label, reps = FALL_SCENARIOS[scenario_idx]
            instruction = SCENARIO_META[scenario]['instruction']
            mark = SCENARIO_META[scenario]['mark']

            if scenario_idx > start_scenario - 1:
                print()
                print(f'NEXT -> {scenario_label}')
                print(f'        Move mat to {mark}')
                print(f'        Script: {instruction}')
                wait_for_enter('Move mat to the next mark, then press Enter when ready... ')

            print()
            print('=' * 60)
            print(scenario_label)
            print(f'Mark: {mark}')
            print(f'Script: {instruction}')
            print(f'Reps: {reps}')
            print('=' * 60)

            master_handle.write(
                f'scenario={scenario_idx + 1} slug={scenario} start={dt.datetime.now().isoformat()}\n'
            )
            master_handle.flush()

            run_scenario_session(
                args.port,
                scenario,
                baud=args.baud,
                dataset_dir=dataset_root / scenario,
                duration_sec=args.duration_sec,
                total_reps=reps,
                ready_sec=args.ready_sec,
                rest_sec=args.rest_sec,
                fall_offset_sec=args.fall_offset_sec,
                expected_len=args.expected_len,
                expected_mac=args.expected_mac,
                scenario_label=scenario_label,
                environment_notes=args.environment_notes,
            )

            master_handle.write(
                f'scenario={scenario_idx + 1} slug={scenario} end={dt.datetime.now().isoformat()}\n'
            )
            master_handle.flush()

            if scenario_idx >= len(FALL_SCENARIOS) - 1:
                break

            print()
            print(f'Finished {scenario_label}.')

    print()
    print('=== All fall scenarios complete ===')
    print(f'session log: {master_log}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
