#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""Audit CSI CSV captures for packet count and outlier rate; quarantine bad files."""

import argparse
import csv
import shutil
import sys
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from paths import RAW_ROOT
from preprocess.preprocess_csi import (
    MIN_FILE_PACKETS,
    MIN_KEEP_PACKET_RATIO,
    apply_baseline,
    compute_reference_baseline,
    filter_normalized_matrix,
    load_csv_packets,
    process_file,
)

DEFAULT_DATASET = RAW_ROOT / 'empty_fan_on'

# 2 min @ ~76 Hz ≈ 9000 pkts; require at least ~30 s of clean data.
DEFAULT_MIN_RAW_PACKETS = 2000
DEFAULT_MAX_DROP_PCT = 65.0
DEFAULT_MIN_KEEP_RATIO = MIN_KEEP_PACKET_RATIO


def audit_file(path, reference_baseline=None, min_raw=DEFAULT_MIN_RAW_PACKETS,
               max_drop_pct=DEFAULT_MAX_DROP_PCT, min_keep_ratio=DEFAULT_MIN_KEEP_RATIO):
    """Return quality dict for one capture CSV."""
    path = Path(path)
    row = {
        'file': path.name,
        'path': str(path),
        'status': 'good',
        'reason': '',
        'raw_packets': 0,
        'kept_packets': 0,
        'drop_pct': 0.0,
        'drop_spike': 0,
        'drop_mad': 0,
        'drop_jump': 0,
        'drop_norm_spike': 0,
        'keep_ratio': 0.0,
        'windows': 0,
        'windows_rejected': 0,
    }

    raw, stats = load_csv_packets(path, return_stats=True)
    row.update({
        'raw_packets': stats['raw_packets'],
        'kept_packets': stats['kept_packets'],
        'drop_pct': stats['drop_pct'],
        'drop_spike': stats['drop_spike'],
        'drop_mad': stats['drop_mad'],
        'drop_jump': stats['drop_jump'],
    })
    keep_ratio = stats['kept_packets'] / max(stats['raw_packets'], 1)
    row['keep_ratio'] = round(keep_ratio, 4)

    if stats['raw_packets'] < min_raw:
        row['status'] = 'fail'
        row['reason'] = f'too_few_raw_packets (<{min_raw})'
        return row

    if stats['kept_packets'] < MIN_FILE_PACKETS:
        row['status'] = 'fail'
        row['reason'] = 'too_few_kept_after_filter'
        return row

    if keep_ratio < min_keep_ratio:
        row['status'] = 'fail'
        row['reason'] = f'keep_ratio<{min_keep_ratio:.0%}'
        return row

    if stats['drop_pct'] > max_drop_pct:
        row['status'] = 'fail'
        row['reason'] = f'drop_pct>{max_drop_pct}%'
        return row

    if reference_baseline is not None:
        norm, norm_stats = filter_normalized_matrix(
            apply_baseline(raw, reference_baseline),
        )
        row['drop_norm_spike'] = norm_stats['drop_norm_spike']
        entry = {
            'path': path,
            'label': 'empty',
            'subgroup': 'empty_fan_on',
            'name': path.name,
        }
        windows, _, _, file_stats = process_file(entry, reference_baseline=reference_baseline)
        row['windows'] = len(windows)
        row['windows_rejected'] = file_stats.get('windows_rejected', 0)
        if len(windows) == 0:
            row['status'] = 'fail'
            row['reason'] = 'zero_windows_after_preprocess'
            return row

    if stats['drop_pct'] > 45.0:
        row['status'] = 'warn'
        row['reason'] = 'high_drop_but_usable'

    return row


def write_report(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def quarantine_file(src, quarantine_dir):
    quarantine_dir = Path(quarantine_dir)
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    dst = quarantine_dir / Path(src).name
    if dst.exists():
        dst.unlink()
    shutil.move(str(src), str(dst))
    log_src = Path(str(src).replace('.csv', '.log'))
    if log_src.exists():
        shutil.move(str(log_src), str(quarantine_dir / log_src.name))
    return dst


def main():
    parser = argparse.ArgumentParser(
        description='Audit CSI CSV quality and optionally quarantine bad captures',
    )
    parser.add_argument(
        '--dir', default=str(DEFAULT_DATASET),
        help='Directory of s*_empty.csv files (default: data/raw/empty_fan_on)',
    )
    parser.add_argument(
        '--glob', default='s*_empty.csv',
        help='Glob for capture files inside --dir',
    )
    parser.add_argument(
        '--min-raw-packets', type=int, default=DEFAULT_MIN_RAW_PACKETS,
        help='Minimum raw CSI packets required (default 2000)',
    )
    parser.add_argument(
        '--max-drop-pct', type=float, default=DEFAULT_MAX_DROP_PCT,
        help='Fail if outlier drop exceeds this %% (default 65)',
    )
    parser.add_argument(
        '--quarantine', action='store_true',
        help='Move fail-status files to <dir>/quarantine/',
    )
    parser.add_argument(
        '--report', default='',
        help='Write CSV report (default: <dir>/quality_report.csv)',
    )
    parser.add_argument(
        '--with-windows', action='store_true',
        help='Run full process_file check (slower, needs empty reference)',
    )
    args = parser.parse_args()

    data_dir = Path(args.dir)
    if not data_dir.is_dir():
        print(f'Not a directory: {data_dir}', file=sys.stderr)
        return 1

    paths = sorted(data_dir.glob(args.glob))
    if not paths:
        print(f'No files matching {args.glob} in {data_dir}')
        return 1

    reference_baseline = None
    if args.with_windows:
        try:
            reference_baseline = compute_reference_baseline(data_dir)
        except RuntimeError as exc:
            print(f'Warning: {exc} — skipping window check', file=sys.stderr)

    rows = []
    for path in paths:
        if 'quarantine' in path.parts:
            continue
        rows.append(audit_file(
            path,
            reference_baseline=reference_baseline if args.with_windows else None,
            min_raw=args.min_raw_packets,
            max_drop_pct=args.max_drop_pct,
        ))

    report_path = Path(args.report) if args.report else data_dir / 'quality_report.csv'
    write_report(report_path, rows)

    good = sum(1 for r in rows if r['status'] == 'good')
    warn = sum(1 for r in rows if r['status'] == 'warn')
    fail = sum(1 for r in rows if r['status'] == 'fail')

    print(f'=== CSI capture quality audit: {data_dir.name} ===')
    print(f'files  : {len(rows)}  good={good}  warn={warn}  fail={fail}')
    print(f'report : {report_path}')
    print()

    for row in rows:
        flag = row['status'].upper()
        extra = f"  windows={row['windows']}" if args.with_windows else ''
        print(
            f'  [{flag:4s}] {row["file"]:22s} '
            f'raw={row["raw_packets"]:5d} kept={row["kept_packets"]:5d} '
            f'drop={row["drop_pct"]:5.1f}%{extra}'
            + (f'  ({row["reason"]})' if row['reason'] else ''),
        )

    if args.quarantine and fail:
        qdir = data_dir / 'quarantine'
        print()
        print(f'Quarantining {fail} failed file(s) -> {qdir}/')
        for row in rows:
            if row['status'] != 'fail':
                continue
            src = Path(row['path'])
            if not src.exists():
                continue
            dst = quarantine_file(src, qdir)
            print(f'  moved {src.name} -> {dst}')

    return 1 if fail else 0


if __name__ == '__main__':
    raise SystemExit(main())
