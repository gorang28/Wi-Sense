#!/usr/bin/env python3
"""Train the CSI occupied/motion/fall cascade from preprocessed NPZ windows.

The three models share the same 75-dimensional CSI feature vector but use the
separate labels emitted by preprocess_csi.py.  This avoids treating all of a
fall recording as a fall: only windows with y_fall=1 are fall positives.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import (average_precision_score, confusion_matrix,
                             fbeta_score, precision_recall_fscore_support)


STAGES = (
    ('occupied', 'y_occupied', 'f1'),
    ('motion', 'y_motion', 'f1'),
    # Missing a fall is costlier than a false alert, so tune this threshold
    # using F2, which weights recall twice as heavily as precision.
    ('fall', 'y_fall', 'f2'),
)


def load_npz(path):
    with np.load(path) as data:
        return {
            'X_feat': data['X_feat'].astype(np.float32),
            'class_id': data['class_id'].astype(np.int8),
            'file_id': data['file_id'].astype(np.int32),
            **{target: data[target].astype(np.int8) for _, target, _ in STAGES},
        }


def fall_file_ids_matching(manifest_path, pattern):
    """file_ids of fall recordings matching every comma-separated token.

    Tokens are matched (case-insensitively) against the recording's filename
    and its baseline_source, so a scenario and a session can be combined —
    e.g. "fall_bed_edge,session:2026-07-30" selects only the bed-edge captures
    baselined against that day's empty-room reference. This keeps the stage
    from learning across recordings baselined against different references,
    which puts the same physical event in two different feature spaces.
    """
    tokens = [t.strip().lower() for t in pattern.split(',') if t.strip()]
    ids = set()
    with Path(manifest_path).open(newline='', encoding='utf-8', errors='replace') as handle:
        for row in csv.DictReader(handle):
            if row.get('label') != 'fall':
                continue
            haystack = f"{row.get('file', '')} {row.get('baseline_source', '')}".lower()
            if all(token in haystack for token in tokens):
                try:
                    ids.add(int(row['file_id']))
                except (KeyError, TypeError, ValueError):
                    continue
    return ids


def balanced_sample_weight(y):
    """Give positive and negative windows equal total training weight."""
    count = np.bincount(y.astype(np.int64), minlength=2)
    if not count[0] or not count[1]:
        raise ValueError(f'binary stage needs both classes; counts={count.tolist()}')
    return np.where(y == 1, len(y) / (2.0 * count[1]), len(y) / (2.0 * count[0]))


def best_threshold(y_true, probability, objective):
    """Choose a validation-only operating threshold."""
    best_score, best_value = -1.0, 0.5
    beta = 2.0 if objective == 'f2' else 1.0
    for threshold in np.linspace(0.05, 0.95, 91):
        prediction = probability >= threshold
        score = fbeta_score(y_true, prediction, beta=beta, zero_division=0)
        if score > best_score:
            best_score, best_value = float(score), float(threshold)
    return best_value, best_score


def stage_report(y_true, probability, threshold):
    prediction = probability >= threshold
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_true, prediction, average='binary', zero_division=0,
    )
    return {
        'threshold': round(float(threshold), 3),
        'positive_windows': int(y_true.sum()),
        'average_precision': round(float(average_precision_score(y_true, probability)), 4),
        'precision': round(float(precision), 4),
        'recall': round(float(recall), 4),
        'f1': round(float(f1), 4),
        'confusion_matrix': confusion_matrix(y_true, prediction).tolist(),
    }


def false_positive_rate(y_true, probability, threshold):
    negatives = y_true == 0
    if not np.any(negatives):
        return 0.0
    return float(np.mean(probability[negatives] >= threshold))


def main():
    parser = argparse.ArgumentParser(description='Train a grouped-split CSI cascade')
    parser.add_argument('--train', default='data/dataset/processed/train.npz')
    parser.add_argument('--val', default='data/dataset/processed/val.npz')
    parser.add_argument('--output', default='models/csi_cascade.joblib')
    parser.add_argument('--report', default='models/csi_cascade_report.json')
    parser.add_argument('--max-iter', type=int, default=200)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument(
        '--fall-training', choices=('fall-only', 'all'), default='fall-only',
        help='fall-only learns the timed event from fall recordings; all uses every non-fall window',
    )
    parser.add_argument(
        '--fall-file-filter', default='',
        help='Restrict the fall stage to fall recordings whose filename contains this '
             'substring (e.g. "fall_bed_edge"). Use when fall captures span sessions '
             'baselined against different empty-room references, which mixes feature '
             'spaces and degrades the stage. Requires --manifest.',
    )
    parser.add_argument(
        '--manifest', default='data/dataset/processed/preprocess_manifest.csv',
        help='Preprocess manifest, used to resolve --fall-file-filter to file_ids',
    )
    args = parser.parse_args()

    train_path, val_path = Path(args.train), Path(args.val)
    if not train_path.exists() or not val_path.exists():
        raise FileNotFoundError('Run preprocess_csi.py first to create train.npz and val.npz.')

    print(f'Loading train data: {train_path}', flush=True)
    train = load_npz(train_path)
    print(f'Loading validation data: {val_path}', flush=True)
    val = load_npz(val_path)
    print(f"Windows: train={len(train['X_feat'])}, val={len(val['X_feat'])}")

    fall_keep_ids = None
    if args.fall_file_filter:
        fall_keep_ids = fall_file_ids_matching(args.manifest, args.fall_file_filter)
        if not fall_keep_ids:
            raise ValueError(
                f'--fall-file-filter {args.fall_file_filter!r} matched no fall files '
                f'in {args.manifest}'
            )
        print(f'Fall stage restricted to {len(fall_keep_ids)} recordings '
              f'matching {args.fall_file_filter!r}')

    models, thresholds, report = {}, {}, {}
    for name, target, objective in STAGES:
        X_train, y_train = train['X_feat'], train[target]
        if name == 'fall':
            if args.fall_training == 'fall-only':
                # Fall manifests define the event at 8.5-11.5 s.  Training against
                # pre/post-event windows from the same capture removes background,
                # placement, and subject shortcuts while preserving the impact cue.
                keep = train['class_id'] == 3
                if fall_keep_ids is not None:
                    keep &= np.isin(train['file_id'], list(fall_keep_ids))
            else:
                # 'all': every non-fall window is a negative.  The cascade tests
                # fall FIRST, so this stage has to reject ordinary standing and
                # walking too — with fall-only negatives it never sees them and
                # scores them as falls.
                keep = np.ones(len(y_train), dtype=bool)
            if fall_keep_ids is not None:
                # Never let a fall recording outside the selection act as a
                # positive; drop those recordings entirely.
                keep &= ~((train['class_id'] == 3)
                          & ~np.isin(train['file_id'], list(fall_keep_ids)))
            X_train, y_train = X_train[keep], y_train[keep]
        # Keep every non-fall window as a negative, but drop fall recordings the
        # stage no longer trains on — otherwise the threshold is tuned against
        # events from a different baseline era than the model ever saw.
        val_mask = np.ones(len(val['X_feat']), dtype=bool)
        if name == 'fall' and fall_keep_ids is not None:
            val_mask = (val['class_id'] != 3) | np.isin(val['file_id'], list(fall_keep_ids))
        X_val = val['X_feat'][val_mask]
        y_val = val[target][val_mask]
        val_class_id = val['class_id'][val_mask]
        print(f'\nTraining {name}: train positives={int(y_train.sum())}, '
              f'val positives={int(y_val.sum())}, train windows={len(y_train)}', flush=True)
        model = HistGradientBoostingClassifier(
            learning_rate=0.06,
            max_iter=args.max_iter,
            max_leaf_nodes=15,
            l2_regularization=1.0,
            early_stopping=True,
            validation_fraction=0.1,
            n_iter_no_change=15,
            random_state=args.seed,
        )
        model.fit(X_train, y_train, sample_weight=balanced_sample_weight(y_train))
        probability = model.predict_proba(X_val)[:, 1]
        threshold, objective_score = best_threshold(y_val, probability, objective)
        models[name] = model
        thresholds[name] = threshold
        report[name] = stage_report(y_val, probability, threshold)
        report[name]['selection_objective'] = objective
        report[name]['selection_score'] = round(objective_score, 4)
        if name == 'fall':
            nonfall_files = val_class_id != 3
            report[name]['training_mode'] = args.fall_training
            report[name]['false_positive_rate_nonfall_files'] = round(
                false_positive_rate(
                    y_val[nonfall_files], probability[nonfall_files], threshold,
                ), 4,
            )
        print(f"  threshold={threshold:.2f} | precision={report[name]['precision']:.3f} "
              f"recall={report[name]['recall']:.3f} | f1={report[name]['f1']:.3f}")

    bundle = {
        'format_version': 1,
        'feature_dim': int(train['X_feat'].shape[1]),
        'models': models,
        'thresholds': thresholds,
        'decision_order': ['fall', 'empty_if_not_occupied', 'motion', 'presence'],
        'fall_training': args.fall_training,
    }
    output, report_path = Path(args.output), Path(args.report)
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, output)
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'\nSaved model: {output}')
    print(f'Saved validation report: {report_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
