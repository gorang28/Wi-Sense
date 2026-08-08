#!/usr/bin/env python3
"""Regenerate only empty CSI features after correcting live-capture timing.

The July 25 ``live_empty_*`` captures are 120 seconds long, but older
preprocessing treated their unnamed duration as 60 seconds.  Presence, motion,
and fall captures already use their correct class recording durations.  This
utility keeps those features from the current NPZ files and rebuilds the empty
features from their raw CSVs with the corrected timestamp-derived duration.

It produces compact feature-only NPZ files for ``train_cascade.py``.  A full
preprocess run remains the canonical way to recreate all sequence tensors, but
this targeted rebuild makes the corrected occupancy model available without
re-parsing every occupied raw capture.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from preprocess.preprocess_csi import process_file


TARGETS = ('y_occupied', 'y_motion', 'y_fall')


def load_nonempty_features(path):
    """Load only fields the cascade trainer needs, excluding stale empties."""
    with np.load(path) as data:
        nonempty = data['class_id'] != 0
        result = {
            'X_feat': data['X_feat'][nonempty].astype(np.float32),
            'class_id': data['class_id'][nonempty].astype(np.int8),
        }
        for target in TARGETS:
            result[target] = data[target][nonempty].astype(np.int8)
    return result


def empty_entries(manifest_path, split):
    """Return empty capture entries assigned to the requested existing split."""
    with Path(manifest_path).open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f'empty manifest: {manifest_path}')

    split_ids = set()
    source_path = Path(split).resolve()
    with np.load(source_path) as data:
        split_ids.update(int(value) for value in np.unique(data['file_id']))

    entries = []
    for row in rows:
        if row.get('label') != 'empty' or int(row['file_id']) not in split_ids:
            continue
        entries.append({
            'path': Path(row['path']),
            'name': row['file'],
            'label': 'empty',
            'file_id': int(row['file_id']),
        })
    return entries


def rebuild_empty_features(entries, dataset_root, baseline):
    """Convert assigned empty recordings to timing-corrected feature rows."""
    features = []
    durations = {}
    for index, entry in enumerate(entries, start=1):
        source = dict(entry)
        source['path'] = dataset_root / source['path']
        windows, _packets, sample_rate, stats = process_file(
            source, reference_baseline=baseline,
        )
        if not windows:
            raise RuntimeError(
                f'no usable windows from {entry["name"]}: '
                f'{stats.get("skipped_reason", "unknown")}',
            )
        features.extend(window['X_feat'] for window in windows)
        durations[entry['name']] = {
            'windows': len(windows),
            'sample_rate_hz': round(float(sample_rate), 3),
            'capture_duration_sec': round(float(stats.get('capture_duration_sec', 0.0)), 3),
        }
        print(
            f'  [{index}/{len(entries)}] {entry["name"]}: '
            f'{len(windows)} windows at {sample_rate:.2f} Hz',
            flush=True,
        )
    if not features:
        raise RuntimeError('no corrected empty features were produced')
    return np.stack(features).astype(np.float32), durations


def combine(nonempty, empty_features):
    """Append corrected empty examples while retaining all occupied examples."""
    count = len(empty_features)
    result = {
        'X_feat': np.concatenate((nonempty['X_feat'], empty_features), axis=0),
        'class_id': np.concatenate((
            nonempty['class_id'], np.zeros(count, dtype=np.int8),
        )),
    }
    result['y_occupied'] = np.concatenate((
        nonempty['y_occupied'], np.zeros(count, dtype=np.int8),
    ))
    result['y_motion'] = np.concatenate((
        nonempty['y_motion'], np.zeros(count, dtype=np.int8),
    ))
    result['y_fall'] = np.concatenate((
        nonempty['y_fall'], np.zeros(count, dtype=np.int8),
    ))
    return result


def save_feature_npz(path, arrays):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **arrays)


def main():
    parser = argparse.ArgumentParser(
        description='Build timing-corrected feature-only cascade training NPZ files',
    )
    parser.add_argument('--dataset-root', default='data/dataset')
    parser.add_argument('--config', default='data/dataset/processed/preprocess_config.json')
    parser.add_argument('--manifest', default='data/dataset/processed/preprocess_manifest.csv')
    parser.add_argument('--train-source', default='data/dataset/processed/train.npz')
    parser.add_argument('--val-source', default='data/dataset/processed/val.npz')
    parser.add_argument('--train-output', default='data/dataset/processed/train_timing_fixed.npz')
    parser.add_argument('--val-output', default='data/dataset/processed/val_timing_fixed.npz')
    parser.add_argument('--metadata-output',
                        default='data/dataset/processed/timing_fixed_training_metadata.json')
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    if 'reference_baseline' not in config:
        raise ValueError('preprocessing config has no saved empty-room reference baseline')
    baseline = np.asarray(config['reference_baseline'], dtype=np.float32)
    dataset_root = Path(args.dataset_root).resolve()

    print('Loading retained occupied features...', flush=True)
    train_nonempty = load_nonempty_features(args.train_source)
    val_nonempty = load_nonempty_features(args.val_source)
    train_entries = empty_entries(args.manifest, args.train_source)
    val_entries = empty_entries(args.manifest, args.val_source)
    if not train_entries or not val_entries:
        raise RuntimeError('existing split has no empty files; cannot validate occupancy')

    print(f'Rebuilding {len(train_entries)} train empty recordings...', flush=True)
    train_empty, train_meta = rebuild_empty_features(train_entries, dataset_root, baseline)
    print(f'Rebuilding {len(val_entries)} validation empty recordings...', flush=True)
    val_empty, val_meta = rebuild_empty_features(val_entries, dataset_root, baseline)

    train = combine(train_nonempty, train_empty)
    val = combine(val_nonempty, val_empty)
    save_feature_npz(args.train_output, train)
    save_feature_npz(args.val_output, val)

    metadata = {
        'purpose': 'Replace stale 60-second live-empty timing with CSI timestamp timing.',
        'feature_dim': int(train['X_feat'].shape[1]),
        'train': {
            'windows': int(len(train['X_feat'])),
            'empty_windows': int(len(train_empty)),
            'empty_files': train_meta,
        },
        'val': {
            'windows': int(len(val['X_feat'])),
            'empty_windows': int(len(val_empty)),
            'empty_files': val_meta,
        },
    }
    metadata_path = Path(args.metadata_output)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')

    print(f'Saved {args.train_output}')
    print(f'Saved {args.val_output}')
    print(f'Saved {args.metadata_output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
