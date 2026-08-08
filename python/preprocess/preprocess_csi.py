#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""Preprocess captured CSI CSVs into windowed NPZ tensors for cascade training.

Uses on-disk memmap buffers and per-array NPZ compression so peak RAM stays
low enough for ~8 GB laptops (one file in memory at a time).
"""

import argparse
import csv
import datetime as dt
import gc
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

import numpy as np

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from paths import PROCESSED_ROOT, RAW_ROOT

DATASET_ROOT = RAW_ROOT
OUTPUT_ROOT = PROCESSED_ROOT

EMPTY_DIR = RAW_ROOT / 'empty_fan_on'
PRESENCE_DIR = RAW_ROOT / 'presence'
MOTION_DIR = RAW_ROOT / 'motion'
FALL_DIR = RAW_ROOT / 'fall_v2'

EXPECTED_LEN = 384
EXPECTED_MAC = '1a:00:00:00:00:00'
NUM_SUBCARRIERS = 32

# HT40 ESP32 CSI: 384 bytes = 192 interleaved I/Q pairs.
HT40_NUM_COMPLEX = 192
# Guard / null / DC tones (empirical on ch11 HT40 captures — not usable data SCs).
HT40_GUARD_SC = frozenset([
    0, 1, 2, 3, 4, 5, 32,
    59, 60, 61, 62, 63, 64, 65,
    123, 124, 125, 126, 127, 128, 129, 130, 131, 132, 133,
    191,
])
HT40_VALID_SC = np.array(
    [i for i in range(HT40_NUM_COMPLEX) if i not in HT40_GUARD_SC],
    dtype=np.int32,
)
# Downsample only over valid data subcarriers (never hit guard/null bins).
HT40_DOWNSAMPLE_IDX = np.linspace(
    0, len(HT40_VALID_SC) - 1, num=NUM_SUBCARRIERS, dtype=np.int32,
)
HT40_SELECTED_SC = HT40_VALID_SC[HT40_DOWNSAMPLE_IDX]

# Per-packet quality on valid subcarriers only (after mask).
# Small-scale captures (binary / web monitor) use tight bounds; raw int16 CSV uses RAW_*.
MAX_VALID_IQ_ABS = 300.0
MAX_VALID_AMP = 500.0
RAW_CSI_AMP_CAP = 32767.0
RAW_CSI_MEDIAN_SCALE = 500.0
RAW_CSI_TARGET_MEDIAN = 40.0
RAW_CSI_MIN_GOOD_FRAC = 0.50
MIN_VALID_SC_GOOD_FRAC = 0.85
ARTIFACT_PLATEAU_LO = 2560.0
ARTIFACT_PLATEAU_HI = 2600.0
ARTIFACT_MEAN_MIN = 400.0

# Durations used when estimating packet rate (seconds).
DURATION_BY_CLASS = {
    'empty': 120.0,
    'presence': 120.0,
    'motion': 120.0,
    'fall': 30.0,
}

# Window / hop in seconds.
WIN_2S = 2.0
WIN_1S = 1.0
HOP_2S = 0.5
HOP_1S = 0.25
HOP_FALL = 0.1

DEFAULT_FALL_OFFSET_SEC = 10.0
DEFAULT_FALL_WINDOW_SEC = 3.0

TARGET_SEQ_1S = 90
TARGET_SEQ_2S = 180
FEATURE_DIM = NUM_SUBCARRIERS * 2 + 5 + 6  # mean_sc + std_sc + stats + band stats

# ESP-IDF CSI local_timestamp is a wrapping uint32 microsecond counter.
CSI_TIMESTAMP_WRAP_US = 1 << 32
MIN_TIMESTAMP_DURATION_SEC = 1.0
MAX_TIMESTAMP_DURATION_SEC = 300.0

BASELINE_MODE_PER_FILE = 'per_file'
BASELINE_MODE_EMPTY_REF = 'empty_room_reference'
BASELINE_MODE_SESSION_MATCH = 'session_match'
DEFAULT_BASELINE_MODE = BASELINE_MODE_SESSION_MATCH

SESSION_CSV_RE = re.compile(r'_(session|full_session)_', re.I)
FILENAME_DATE_RE = re.compile(r'(20\d{6})_\d{6}')

ARRAY_SPECS = {
    'X_feat': ('float32', (FEATURE_DIM,)),
    'X_seq_1s': ('float32', (TARGET_SEQ_1S, NUM_SUBCARRIERS)),
    'X_seq_2s': ('float32', (TARGET_SEQ_2S, NUM_SUBCARRIERS)),
    'y_occupied': ('int8', ()),
    'y_motion': ('int8', ()),
    'y_fall': ('int8', ()),
    'file_id': ('int32', ()),
    'class_id': ('int8', ()),
    'center_time_sec': ('float32', ()),
}


def parse_csi_iq(data_field, expected_len=EXPECTED_LEN):
    """Parse data[] to (192,) I and Q arrays. Returns (None, None) on bad length."""
    raw = json.loads(data_field)
    if len(raw) != int(expected_len):
        return None, None
    i_vals = np.asarray(raw[0::2], dtype=np.float32)
    q_vals = np.asarray(raw[1::2], dtype=np.float32)
    if i_vals.shape[0] != HT40_NUM_COMPLEX:
        return None, None
    return i_vals, q_vals


def valid_subcarrier_amplitudes(i_vals, q_vals):
    """Amplitude on HT40 valid subcarriers only (166 tones)."""
    amp = np.hypot(i_vals, q_vals)
    return amp[HT40_VALID_SC]


def _csi_format_limits(vi, vq):
    """Detect small vs raw int16 CSI; return (amp_cap, min_good_frac)."""
    iq_peak = float(np.max(np.abs(np.concatenate([vi, vq]))))
    if iq_peak <= MAX_VALID_IQ_ABS:
        return MAX_VALID_AMP, MIN_VALID_SC_GOOD_FRAC
    return RAW_CSI_AMP_CAP, RAW_CSI_MIN_GOOD_FRAC


def normalize_raw_csi_amplitudes(valid_amp):
    """Scale raw int16 amplitudes to the same range as live binary captures."""
    amp = np.asarray(valid_amp, dtype=np.float32)
    med = float(np.median(amp))
    if med > RAW_CSI_MEDIAN_SCALE:
        amp = amp * (RAW_CSI_TARGET_MEDIAN / max(med, 1.0))
    return amp


def is_corrupt_iq_packet(i_vals, q_vals):
    """Reject interference / parse glitches before they enter training windows."""
    if i_vals is None or q_vals is None:
        return True
    vi = i_vals[HT40_VALID_SC]
    vq = q_vals[HT40_VALID_SC]
    amp = np.hypot(vi, vq)
    amp_cap, min_good = _csi_format_limits(vi, vq)
    if float(np.max(amp)) > amp_cap:
        return True
    if float(np.mean(amp >= (amp_cap - 1.0))) > 0.40:
        return True
    if np.any((amp > ARTIFACT_PLATEAU_LO) & (amp < ARTIFACT_PLATEAU_HI)):
        if float(np.mean(amp)) > ARTIFACT_MEAN_MIN:
            return True
    good = ((amp >= 1.0) & (amp <= amp_cap)).mean()
    return float(good) < min_good


def parse_csi_amplitude(data_field, expected_len=EXPECTED_LEN, num_sc=NUM_SUBCARRIERS):
    """Parse JSON data column to num_sc amplitude values (valid HT40 SCs only)."""
    i_vals, q_vals = parse_csi_iq(data_field, expected_len=expected_len)
    if is_corrupt_iq_packet(i_vals, q_vals):
        return None
    full_amp = np.hypot(i_vals, q_vals)
    valid_amp = normalize_raw_csi_amplitudes(full_amp[HT40_VALID_SC])
    if num_sc >= len(valid_amp):
        out = np.zeros(num_sc, dtype=np.float32)
        out[:len(valid_amp)] = valid_amp
        return out
    idx = np.linspace(0, len(valid_amp) - 1, num=num_sc, dtype=np.int32)
    return valid_amp[idx].astype(np.float32)


# Outlier / spike thresholds — shared with live_cascade_detect.py (keep in sync).
# After HT40 mask, clean fan-on amplitudes are typically 5–120 (not 20k bursts).
SPIKE_SC_AMP_CAP = 600.0
SPIKE_MEAN_AMP_CAP = 250.0
MAD_Z_THRESHOLD = 8.0
MAD_MEAN_AMP_CAP = 200.0
MAX_PKT_MEAN_JUMP = 8.0
MIN_KEEP_PACKET_RATIO = 0.25
LIVE_EMPTY_MIN_KEEP_RATIO = 0.25
MIN_FILE_PACKETS = 80
MAX_WINDOW_DELTA = 500.0
MAX_WINDOW_ABS = 800.0
MAX_NORM_PKT_ABS = 600.0
# Windows may bridge one or two lost packets, but larger ID gaps represent a
# real time discontinuity and are excluded from training.
MAX_MISSING_PACKETS_PER_WINDOW_GAP = 2


def has_parse_artifact(amplitude):
    """Detect corrupt I/Q parse glitches (plateau near 2573 on subcarriers)."""
    amp = np.asarray(amplitude, dtype=np.float32)
    if not np.any((amp > ARTIFACT_PLATEAU_LO) & (amp < ARTIFACT_PLATEAU_HI)):
        return False
    return float(np.mean(amp)) > ARTIFACT_MEAN_MIN


def is_spike_packet(amplitude):
    """Hard limits on per-packet amplitude (catches corrupt I/Q bursts)."""
    amp = np.asarray(amplitude, dtype=np.float32)
    if has_parse_artifact(amp):
        return True
    if float(np.max(amp)) > SPIKE_SC_AMP_CAP:
        return True
    mean_val = float(np.mean(amp))
    return mean_val > SPIKE_MEAN_AMP_CAP


def is_mad_outlier(amplitude, median_ref, mad_ref,
                   z_threshold=MAD_Z_THRESHOLD, abs_cap=MAD_MEAN_AMP_CAP):
    """Statistical outlier vs file/session median (after spike filter)."""
    mean_val = float(np.mean(amplitude))
    if mean_val > abs_cap:
        return True
    scale = max(float(mad_ref) * 1.4826, 1.0)
    return abs(mean_val - float(median_ref)) > z_threshold * scale


def is_outlier(amplitude, median_ref, mad_ref, z_threshold=MAD_Z_THRESHOLD, abs_cap=MAD_MEAN_AMP_CAP):
    """Combined spike + MAD check (backward compatible)."""
    if is_spike_packet(amplitude):
        return True
    return is_mad_outlier(amplitude, median_ref, mad_ref, z_threshold, abs_cap)


def filter_amplitude_matrix(matrix, return_indices=False):
    """Two-pass packet filter: spikes, then MAD; drop large packet-to-packet jumps.

    Returns (filtered_matrix, stats_dict).
    """
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.size == 0:
        stats = {
            'raw_packets': 0,
            'kept_packets': 0,
            'drop_pct': 0.0,
            'drop_corrupt': 0,
            'drop_spike': 0,
            'drop_mad': 0,
            'drop_jump': 0,
        }
        if return_indices:
            return matrix, stats, np.zeros(0, dtype=np.int32)
        return matrix, stats

    n_raw = matrix.shape[0]
    drop_spike = drop_mad = drop_jump = 0
    prev_mean = None
    pass1 = []
    pass1_indices = []
    for i in range(n_raw):
        pkt = matrix[i]
        if is_spike_packet(pkt):
            drop_spike += 1
            continue
        pass1.append(pkt)
        pass1_indices.append(i)
    if not pass1:
        filtered = np.zeros((0, NUM_SUBCARRIERS), dtype=np.float32)
        stats = {
            'raw_packets': n_raw,
            'kept_packets': 0,
            'drop_pct': 100.0,
            'drop_spike': drop_spike,
            'drop_mad': 0,
            'drop_jump': 0,
        }
        if return_indices:
            return filtered, stats, np.zeros(0, dtype=np.int32)
        return filtered, stats

    pass1 = np.stack(pass1, axis=0)
    pass1_indices = np.asarray(pass1_indices, dtype=np.int32)
    per_pkt_mean = pass1.mean(axis=1)
    median_ref = float(np.median(per_pkt_mean))
    mad_ref = float(np.median(np.abs(per_pkt_mean - median_ref)))

    kept = []
    kept_indices = []
    prev_mean = None
    for i in range(pass1.shape[0]):
        pkt = pass1[i]
        if is_mad_outlier(pkt, median_ref, mad_ref):
            drop_mad += 1
            continue
        cur_mean = float(np.mean(pkt))
        if prev_mean is not None:
            jump = abs(cur_mean - prev_mean) / max(prev_mean, 10.0)
            if jump > MAX_PKT_MEAN_JUMP:
                drop_jump += 1
                continue
        prev_mean = cur_mean
        kept.append(pkt)
        kept_indices.append(pass1_indices[i])

    if not kept:
        filtered = np.zeros((0, NUM_SUBCARRIERS), dtype=np.float32)
    else:
        filtered = np.stack(kept, axis=0).astype(np.float32)

    n_kept = filtered.shape[0]
    drop_total = n_raw - n_kept
    stats = {
        'raw_packets': n_raw,
        'kept_packets': n_kept,
        'drop_pct': round(100.0 * drop_total / max(n_raw, 1), 2),
        'drop_spike': drop_spike,
        'drop_mad': drop_mad,
        'drop_jump': drop_jump,
    }
    if return_indices:
        return filtered, stats, np.asarray(kept_indices, dtype=np.int32)
    return filtered, stats


def filter_normalized_matrix(matrix, max_abs=MAX_NORM_PKT_ABS, return_mask=False):
    """Drop baseline-subtracted packets with residual amplitude spikes."""
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.size == 0:
        stats = {'drop_norm_spike': 0}
        if return_mask:
            return matrix, stats, np.zeros(0, dtype=bool)
        return matrix, stats
    per_max = np.max(np.abs(matrix), axis=1)
    keep = per_max <= max_abs
    filtered = matrix[keep]
    stats = {'drop_norm_spike': int((~keep).sum())}
    if return_mask:
        return filtered, stats, keep
    return filtered, stats


def is_valid_window(window):
    """Reject 2 s windows still dominated by residual spikes after packet filtering."""
    if window.size == 0:
        return False
    win = np.asarray(window, dtype=np.float32)
    if float(np.max(np.abs(win))) > MAX_WINDOW_ABS:
        return False
    if win.shape[0] > 1:
        delta = float(np.max(np.abs(np.diff(win, axis=0))))
        if delta > MAX_WINDOW_DELTA:
            return False
    return True


def sequence_gap_stats(packet_ids):
    """Return capture-level loss statistics from consecutive transmitter IDs."""
    ids = np.asarray(packet_ids, dtype=np.int64)
    if ids.size < 2:
        return {
            'sequence_gaps': 0,
            'sequence_missing': 0,
            'sequence_large_gaps': 0,
        }
    deltas = np.diff(ids)
    return {
        'sequence_gaps': int(np.sum(deltas > 1)),
        'sequence_missing': int(np.sum(np.maximum(deltas - 1, 0))),
        'sequence_large_gaps': int(
            np.sum(deltas > MAX_MISSING_PACKETS_PER_WINDOW_GAP + 1)
        ),
    }


def load_csv_packets(csv_path, expected_mac=EXPECTED_MAC, return_stats=False,
                     return_packet_ids=False):
    """Load one capture CSV into raw (N, NUM_SUBCARRIERS) amplitudes with outlier rejection."""
    packets = []
    packet_ids = []
    raw_packet_timestamps = []
    packet_segments = []
    raw_packet_ids = []
    segment_id = 0
    previous_packet_id = None
    drop_corrupt = 0
    drop_parse = 0
    raw_packets = 0
    with Path(csv_path).open(newline='') as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get('type') != 'CSI_DATA':
                continue
            if row.get('mac', '').lower() != expected_mac.lower():
                continue
            try:
                length = int(row.get('len', 0))
            except ValueError:
                continue
            if length != EXPECTED_LEN:
                continue
            try:
                packet_id = int(row['id'])
            except (KeyError, TypeError, ValueError):
                # Keep older captures processable even if they lack a sequence ID.
                packet_id = raw_packets
            raw_packets += 1
            raw_packet_ids.append(packet_id)
            try:
                timestamp = int(row.get('local_timestamp', ''))
            except (TypeError, ValueError):
                timestamp = None
            raw_packet_timestamps.append(timestamp)
            if (previous_packet_id is not None
                    and packet_id - previous_packet_id
                    > MAX_MISSING_PACKETS_PER_WINDOW_GAP + 1):
                segment_id += 1
            previous_packet_id = packet_id
            i_vals, q_vals = parse_csi_iq(row['data'], expected_len=length)
            if i_vals is None:
                drop_parse += 1
                continue
            if is_corrupt_iq_packet(i_vals, q_vals):
                drop_corrupt += 1
                continue
            amp = parse_csi_amplitude(row['data'], expected_len=length)
            if amp is None:
                drop_corrupt += 1
                continue
            packets.append(amp)
            packet_ids.append(packet_id)
            packet_segments.append(segment_id)

    if not packets:
        empty = np.zeros((0, NUM_SUBCARRIERS), dtype=np.float32)
        stats = {
            'raw_packets': raw_packets,
            'kept_packets': 0,
            'drop_pct': 100.0 if raw_packets else 0.0,
            'drop_corrupt': drop_corrupt,
            'drop_parse': drop_parse,
            'drop_spike': 0,
            'drop_mad': 0,
            'drop_jump': 0,
        }
        stats.update(sequence_gap_stats(raw_packet_ids))
        if return_stats and return_packet_ids:
            return empty, stats, np.zeros(0, dtype=np.int64)
        if return_stats:
            return empty, stats
        return empty

    matrix = np.stack(packets, axis=0)
    capture_sequence_stats = sequence_gap_stats(raw_packet_ids)
    filtered, stats, kept_indices = filter_amplitude_matrix(matrix, return_indices=True)
    filtered_ids = np.asarray(packet_ids, dtype=np.int64)[kept_indices]
    # Segment IDs encode only genuine capture loss observed before filtering.
    # Content-based filtering must not manufacture a timing discontinuity.
    stats['_packet_segments'] = np.asarray(packet_segments, dtype=np.int32)[kept_indices]
    stats['_raw_packet_timestamps'] = np.asarray([
        -1 if timestamp is None else timestamp
        for timestamp in raw_packet_timestamps
    ], dtype=np.int64)
    stats['drop_corrupt'] = drop_corrupt
    stats['drop_parse'] = drop_parse
    stats['raw_packets'] = raw_packets
    drop_total = raw_packets - stats['kept_packets']
    stats['drop_pct'] = round(100.0 * drop_total / max(raw_packets, 1), 2)
    # These values are calculated before content-based filtering, so they
    # describe actual capture loss rather than packets intentionally dropped.
    stats.update(capture_sequence_stats)
    if return_stats and return_packet_ids:
        return filtered, stats, filtered_ids
    if return_stats:
        return filtered, stats
    return filtered


def apply_baseline(matrix, reference_baseline=None):
    """Subtract empty-room reference or per-file median baseline."""
    if matrix.size == 0:
        return matrix
    if reference_baseline is not None:
        return matrix - reference_baseline.reshape(1, -1)
    return matrix - np.median(matrix, axis=0, keepdims=True)


def iter_empty_reference_paths(empty_dir):
    """Empty-room CSVs used for reference baseline (excludes quarantine)."""
    empty_dir = Path(empty_dir)
    seen = set()
    for pattern in ('s*_empty.csv', 'live_empty_*.csv'):
        for path in sorted(empty_dir.glob(pattern)):
            if 'quarantine' in path.parts:
                continue
            if path in seen:
                continue
            seen.add(path)
            yield path


def compute_reference_baseline_for_paths(paths, expected_mac=EXPECTED_MAC, context_label=''):
    """Build a reference spectrum (median-of-medians) from an explicit path list."""
    file_medians = []
    for path in paths:
        matrix = load_csv_packets(path, expected_mac=expected_mac)
        if matrix.size == 0:
            continue
        file_medians.append(np.median(matrix, axis=0))
    if not file_medians:
        raise RuntimeError(f'no empty reference files found in {context_label or paths}')
    return np.median(np.stack(file_medians, axis=0), axis=0).astype(np.float32)


def compute_reference_baseline(empty_dir, expected_mac=EXPECTED_MAC, prefer_live=True):
    """Build room-empty reference spectrum from empty_fan_on captures.

    When prefer_live is True and live_empty_*.csv files exist, use only those
    so the deploy baseline matches the current TX/RX link.
    """
    paths = list(iter_empty_reference_paths(empty_dir))
    if prefer_live:
        live_paths = [p for p in paths if p.name.startswith('live_empty_')]
        if live_paths:
            paths = live_paths
    return compute_reference_baseline_for_paths(paths, expected_mac, context_label=str(empty_dir))


def _date_from_sibling_manifest(path):
    """Resolve a capture file's recording date via a sibling manifest CSV.

    Manifests are identified structurally (has 'file' and 'started_at'
    columns), not by filename, since naming varies by capture type
    (empty_session_*, presence_*, motion_*, fall_*).
    """
    for manifest in sorted(path.parent.glob('*.csv')):
        if manifest == path:
            continue
        try:
            with manifest.open(newline='', encoding='utf-8', errors='replace') as handle:
                reader = csv.DictReader(handle)
                fieldnames = reader.fieldnames or []
                if 'file' not in fieldnames or 'started_at' not in fieldnames:
                    continue
                for row in reader:
                    if row.get('file') != path.name:
                        continue
                    try:
                        return dt.datetime.fromisoformat(row.get('started_at', '')).date()
                    except ValueError:
                        return None
        except (OSError, csv.Error):
            continue
    return None


def capture_date_for_file(path):
    """Resolve a capture file's recording date.

    Order: date embedded in the filename (live_empty_*), else a sibling
    manifest CSV lookup (s*.csv files, which don't embed a date), else the
    file's mtime as a low-confidence last resort. Returns None if nothing
    resolves.
    """
    path = Path(path)
    match = FILENAME_DATE_RE.search(path.name)
    if match:
        try:
            return dt.datetime.strptime(match.group(1), '%Y%m%d').date()
        except ValueError:
            pass
    manifest_date = _date_from_sibling_manifest(path)
    if manifest_date is not None:
        return manifest_date
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime).date()
    except OSError:
        return None


def group_empty_paths_by_date(empty_dir):
    """Bucket empty-room reference paths (excludes quarantine) by recording date."""
    groups = {}
    for path in iter_empty_reference_paths(empty_dir):
        date = capture_date_for_file(path)
        if date is None:
            continue
        groups.setdefault(date, []).append(path)
    return groups


def resolve_session_baseline(file_path, global_baseline, empty_by_date, expected_mac=EXPECTED_MAC, cache=None):
    """Per-file baseline for session_match mode: same-day empty reference if
    one exists, else the global reference. Returns (baseline, source_label)."""
    if cache is None:
        cache = {}
    date = capture_date_for_file(file_path)
    same_day_paths = empty_by_date.get(date) if date is not None else None
    if not same_day_paths:
        return global_baseline, 'global'
    if date not in cache:
        cache[date] = compute_reference_baseline_for_paths(
            same_day_paths, expected_mac, context_label=f'session {date}',
        )
    return cache[date], f'session:{date.isoformat()}'


def load_csv_amplitudes(csv_path, expected_mac=EXPECTED_MAC, reference_baseline=None):
    """Load one capture CSV into baseline-subtracted amplitude matrix."""
    matrix = load_csv_packets(csv_path, expected_mac=expected_mac)
    if matrix.size == 0:
        return matrix
    return apply_baseline(matrix, reference_baseline).astype(np.float32)


def load_fall_metadata(fall_dir=FALL_DIR):
    """Map fall capture filename -> (fall_offset_sec, fall_window_sec)."""
    meta = {}
    fall_dir = Path(fall_dir)
    for manifest in fall_dir.glob('**/fall_*.csv'):
        if not manifest.is_file():
            continue
        with manifest.open(newline='', encoding='utf-8', errors='replace') as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                fname = row.get('file', '').strip()
                if not fname:
                    continue
                try:
                    offset = float(row.get('fall_offset_sec', DEFAULT_FALL_OFFSET_SEC))
                    window = float(row.get('fall_window_sec', DEFAULT_FALL_WINDOW_SEC))
                except ValueError:
                    offset = DEFAULT_FALL_OFFSET_SEC
                    window = DEFAULT_FALL_WINDOW_SEC
                meta[fname] = (offset, window)
    return meta


def discover_capture_files(empty_source='all'):
    """Return list of dicts describing each s*.csv capture file.

    empty_source: 'all' | 'live' (live_empty_*.csv only) | 'legacy' (s*_empty only)
    """
    files = []

    def add(path, label, subgroup=''):
        files.append({
            'path': path,
            'label': label,
            'subgroup': subgroup,
            'name': path.name,
        })

    if EMPTY_DIR.exists():
        empty_globs = []
        if empty_source in ('all', 'legacy'):
            empty_globs.append('s*_empty.csv')
        if empty_source in ('all', 'live'):
            empty_globs.append('live_empty_*.csv')
        seen = set()
        for pattern in empty_globs:
            for path in sorted(EMPTY_DIR.glob(pattern)):
                if 'quarantine' in path.parts:
                    continue
                if path in seen:
                    continue
                seen.add(path)
                subgroup = 'empty_fan_on_live' if path.name.startswith('live_empty_') else 'empty_fan_on'
                add(path, 'empty', subgroup)

    for label, root in (('presence', PRESENCE_DIR), ('motion', MOTION_DIR)):
        if not root.exists():
            continue
        for path in sorted(root.glob('**/s*.csv')):
            if 'quarantine' in path.parts:
                continue
            if SESSION_CSV_RE.search(path.name):
                continue
            if path.name.startswith('motion_') or path.name.startswith('presence_'):
                continue
            if path.name.startswith('fall_'):
                continue
            subgroup = path.parent.name
            add(path, label, subgroup)

    fall_meta = load_fall_metadata()
    if FALL_DIR.exists():
        for path in sorted(FALL_DIR.glob('**/s*.csv')):
            if 'quarantine' in path.parts:
                continue
            if SESSION_CSV_RE.search(path.name):
                continue
            if '_fall_' not in path.name:
                continue
            subgroup = path.parent.name
            entry = {
                'path': path,
                'label': 'fall',
                'subgroup': subgroup,
                'name': path.name,
            }
            offset, window = fall_meta.get(path.name, (DEFAULT_FALL_OFFSET_SEC, DEFAULT_FALL_WINDOW_SEC))
            entry['fall_offset_sec'] = offset
            entry['fall_window_sec'] = window
            files.append(entry)

    return files


def timestamp_duration_seconds(timestamps):
    """Return elapsed CSI time from a uint32 microsecond timestamp sequence.

    The capture writer stores ESP-IDF's ``local_timestamp`` in every CSV row.
    It is the only reliable duration source for a ``live_empty_*`` file: those
    files are currently two minutes long, while their filename has no duration.
    ``None`` is returned for missing or implausible timestamp data so older CSVs
    continue to use the class-duration fallback below.
    """
    values = np.asarray(timestamps, dtype=np.int64)
    values = values[values >= 0]
    if values.size < 2:
        return None
    # Prefer the ordinary non-wrapping span.  A receiver timer can reset near
    # the beginning or end of a capture; in that case first/last would look
    # like a multi-hour wrap even though most samples still cover 120 seconds.
    ordinary_span_us = int(values.max() - values.min())
    if MIN_TIMESTAMP_DURATION_SEC * 1_000_000 <= ordinary_span_us <= MAX_TIMESTAMP_DURATION_SEC * 1_000_000:
        return ordinary_span_us / 1_000_000.0
    elapsed_us = int((values[-1] - values[0]) % CSI_TIMESTAMP_WRAP_US)
    elapsed_sec = elapsed_us / 1_000_000.0
    if not MIN_TIMESTAMP_DURATION_SEC <= elapsed_sec <= MAX_TIMESTAMP_DURATION_SEC:
        return None
    return elapsed_sec


def estimate_sample_rate(n_packets, label, filename='', timestamp_duration_sec=None):
    """Estimate retained-packet Hz using CSI time when it is trustworthy."""
    name = Path(filename).name if filename else ''
    if timestamp_duration_sec is not None:
        duration = timestamp_duration_sec
    elif name.startswith('live_empty_'):
        if '30m' in name:
            duration = 1800.0
        elif '30s' in name:
            duration = 30.0
        else:
            # scripts/capture_empty_20_windows.ps1 defaults to 120 seconds.
            # Historical files omit that duration from their filename.
            duration = 120.0
    else:
        duration = DURATION_BY_CLASS.get(label, 120.0)
    if n_packets <= 0:
        return 89.0
    return min(max(n_packets / duration, 20.0), 120.0)


def window_indices(n_packets, sample_rate, win_sec, hop_sec):
    win_len = max(int(round(win_sec * sample_rate)), 1)
    hop_len = max(int(round(hop_sec * sample_rate)), 1)
    if n_packets < win_len:
        return []
    starts = list(range(0, n_packets - win_len + 1, hop_len))
    return [(s, s + win_len) for s in starts]


def has_large_sequence_gap(packet_ids, start, end,
                           max_missing=MAX_MISSING_PACKETS_PER_WINDOW_GAP):
    """Return whether a window crosses an ID gap too large to be continuous."""
    ids = np.asarray(packet_ids[start:end], dtype=np.int64)
    return bool(ids.size > 1 and np.any(np.diff(ids) > max_missing + 1))


def crosses_capture_gap(packet_segments, start, end):
    """Return whether a window crosses a genuine, pre-filter capture gap."""
    segments = packet_segments[start:end]
    return bool(segments.size > 1 and segments[0] != segments[-1])


def window_center_time(start, end, sample_rate):
    return ((start + end) / 2.0) / sample_rate


def extract_features(window_2s):
    """Aggregate 2 s window into a fixed-length feature vector for MLP."""
    # window_2s: (T, 32)
    mean_sc = window_2s.mean(axis=0)
    std_sc = window_2s.std(axis=0)
    if window_2s.shape[0] > 1:
        delta = np.abs(np.diff(window_2s, axis=0))
        delta_mean = float(delta.mean())
        delta_max = float(delta.max())
    else:
        delta_mean = 0.0
        delta_max = 0.0

    total_mean = float(window_2s.mean())
    total_std = float(window_2s.std())
    total_energy = float(np.mean(window_2s ** 2))

    n = NUM_SUBCARRIERS
    thirds = [n // 3, 2 * (n // 3)]
    bands = [
        window_2s[:, :thirds[0]],
        window_2s[:, thirds[0]:thirds[1]],
        window_2s[:, thirds[1]:],
    ]
    band_means = [float(b.mean()) for b in bands]
    band_stds = [float(b.std()) for b in bands]

    feat = np.concatenate([
        mean_sc,
        std_sc,
        np.asarray([total_mean, total_std, total_energy, delta_mean, delta_max], dtype=np.float32),
        np.asarray(band_means + band_stds, dtype=np.float32),
    ]).astype(np.float32)
    return feat


def downsample_sequence(window, target_len):
    """Resize (T, 32) to (target_len, 32) via linear index sampling."""
    if window.shape[0] == target_len:
        return window.astype(np.float32)
    if window.shape[0] == 0:
        return np.zeros((target_len, NUM_SUBCARRIERS), dtype=np.float32)
    idx = np.linspace(0, window.shape[0] - 1, num=target_len).astype(int)
    return window[idx].astype(np.float32)


def labels_for_window(label, center_time, fall_offset=None, fall_window=None):
    """Return y_occupied, y_motion, y_fall for a window."""
    if label == 'empty':
        return 0, 0, 0
    if label == 'presence':
        return 1, 0, 0
    if label == 'motion':
        return 1, 1, 0
    if label == 'fall':
        offset = fall_offset if fall_offset is not None else DEFAULT_FALL_OFFSET_SEC
        window = fall_window if fall_window is not None else DEFAULT_FALL_WINDOW_SEC
        half = window / 2.0
        is_fall = (center_time >= offset - half) and (center_time <= offset + half)
        return 1, 0, int(is_fall)
    return 0, 0, 0


def process_file(entry, target_1s=TARGET_SEQ_1S, target_2s=TARGET_SEQ_2S,
                 reference_baseline=None):
    """Process one CSV into window lists."""
    raw_matrix, filter_stats, packet_ids = load_csv_packets(
        entry['path'], return_stats=True, return_packet_ids=True,
    )
    packet_segments = filter_stats.pop('_packet_segments', np.zeros(
        len(packet_ids), dtype=np.int32,
    ))
    raw_packet_timestamps = filter_stats.pop(
        '_raw_packet_timestamps', np.zeros(0, dtype=np.int64),
    )
    file_stats = dict(filter_stats)
    file_stats['skipped_reason'] = ''
    file_stats['windows_rejected'] = 0
    file_stats['windows_gap_rejected'] = 0
    file_stats['bad_file'] = 0

    if filter_stats['raw_packets'] < MIN_FILE_PACKETS:
        file_stats['skipped_reason'] = 'too_few_raw_packets'
        return [], 0, 0.0, file_stats

    keep_ratio = filter_stats['kept_packets'] / max(filter_stats['raw_packets'], 1)
    path_name = Path(entry['path']).name
    min_keep = LIVE_EMPTY_MIN_KEEP_RATIO if path_name.startswith('live_empty_') else MIN_KEEP_PACKET_RATIO
    if filter_stats['kept_packets'] < MIN_FILE_PACKETS:
        file_stats['skipped_reason'] = 'too_few_kept_packets'
        file_stats['bad_file'] = 1
        return [], 0, 0.0, file_stats

    if keep_ratio < min_keep:
        file_stats['skipped_reason'] = 'high_drop_rate'
        file_stats['bad_file'] = 1
        return [], 0, 0.0, file_stats

    matrix = apply_baseline(raw_matrix, reference_baseline)
    matrix, norm_stats, norm_keep = filter_normalized_matrix(matrix, return_mask=True)
    packet_ids = packet_ids[norm_keep]
    packet_segments = packet_segments[norm_keep]
    file_stats['drop_norm_spike'] = norm_stats['drop_norm_spike']
    if matrix.size == 0:
        file_stats['skipped_reason'] = 'no_packets_after_norm_filter'
        return [], 0, 0.0, file_stats

    n_packets = matrix.shape[0]
    file_stats['post_filter_sequence_large_gaps'] = int(
        np.sum(np.diff(packet_segments) > 0)
    ) if packet_segments.size > 1 else 0
    capture_duration_sec = timestamp_duration_seconds(raw_packet_timestamps)
    # Only live empty captures depend on a timestamp duration.  Other legacy
    # scenarios have known recording lengths, and a few contain invalid timer
    # jumps from a receiver restart.
    if not path_name.startswith('live_empty_'):
        capture_duration_sec = None
    file_stats['capture_duration_sec'] = capture_duration_sec or 0.0
    sample_rate = estimate_sample_rate(
        n_packets, entry['label'], entry['path'].name,
        timestamp_duration_sec=capture_duration_sec,
    )

    fall_offset = entry.get('fall_offset_sec', DEFAULT_FALL_OFFSET_SEC)
    fall_window = entry.get('fall_window_sec', DEFAULT_FALL_WINDOW_SEC)

    results = []
    hop_fall = HOP_FALL if entry['label'] == 'fall' else HOP_2S

    for start, end in window_indices(n_packets, sample_rate, WIN_2S, hop_fall):
        if crosses_capture_gap(packet_segments, start, end):
            file_stats['windows_gap_rejected'] += 1
            continue
        win2 = matrix[start:end]
        if not is_valid_window(win2):
            file_stats['windows_rejected'] += 1
            continue
        center_t = window_center_time(start, end, sample_rate)
        y_occ, y_mot, y_fall = labels_for_window(
            entry['label'], center_t, fall_offset, fall_window,
        )

        feat = extract_features(win2)
        seq2 = downsample_sequence(win2, target_2s)

        one_sec_len = max(int(round(WIN_1S * sample_rate)), 1)
        one_start = max(end - one_sec_len, 0)
        win1 = matrix[one_start:end]
        seq1 = downsample_sequence(win1, target_1s)

        results.append({
            'X_feat': feat,
            'X_seq_1s': seq1,
            'X_seq_2s': seq2,
            'y_occupied': y_occ,
            'y_motion': y_mot,
            'y_fall': y_fall,
            'center_time_sec': center_t,
        })

    if entry['label'] in ('presence', 'motion'):
        for start, end in window_indices(n_packets, sample_rate, WIN_1S, HOP_1S):
            context_start = max(start - (end - start), 0)
            if (crosses_capture_gap(packet_segments, start, end)
                    or crosses_capture_gap(packet_segments, context_start, end)):
                file_stats['windows_gap_rejected'] += 1
                continue
            win1 = matrix[start:end]
            win2_ctx = matrix[context_start:end]
            if not is_valid_window(win2_ctx):
                file_stats['windows_rejected'] += 1
                continue
            center_t = window_center_time(start, end, sample_rate)
            y_occ, y_mot, y_fall = labels_for_window(entry['label'], center_t)
            results.append({
                'X_feat': extract_features(downsample_sequence(win1, target_2s)),
                'X_seq_1s': downsample_sequence(win1, target_1s),
                'X_seq_2s': downsample_sequence(win2_ctx, target_2s),
                'y_occupied': y_occ,
                'y_motion': y_mot,
                'y_fall': y_fall,
                'center_time_sec': center_t,
            })

    return results, n_packets, sample_rate, file_stats


def split_files(file_entries, val_ratio=0.2, seed=42):
    """Stratified split by class + subgroup (scenario)."""
    rng = np.random.default_rng(seed)
    train_files = []
    val_files = []

    by_key = {}
    for entry in file_entries:
        subgroup = entry.get('subgroup') or entry['label']
        key = (entry['label'], subgroup)
        by_key.setdefault(key, []).append(entry)

    for (_label, _subgroup), items in sorted(by_key.items()):
        items = list(items)
        rng.shuffle(items)
        if len(items) == 1:
            train_files.extend(items)
            continue
        n_val = max(1, int(round(len(items) * val_ratio)))
        val_files.extend(items[:n_val])
        train_files.extend(items[n_val:])

    return train_files, val_files


def stack_file_windows(windows, file_id, class_id):
    """Stack one file's window dicts into batched numpy arrays."""
    return {
        'X_feat': np.stack([w['X_feat'] for w in windows]).astype(np.float32),
        'X_seq_1s': np.stack([w['X_seq_1s'] for w in windows]).astype(np.float32),
        'X_seq_2s': np.stack([w['X_seq_2s'] for w in windows]).astype(np.float32),
        'y_occupied': np.asarray([w['y_occupied'] for w in windows], dtype=np.int8),
        'y_motion': np.asarray([w['y_motion'] for w in windows], dtype=np.int8),
        'y_fall': np.asarray([w['y_fall'] for w in windows], dtype=np.int8),
        'file_id': np.full(len(windows), file_id, dtype=np.int32),
        'class_id': np.full(len(windows), class_id, dtype=np.int8),
        'center_time_sec': np.asarray(
            [w['center_time_sec'] for w in windows], dtype=np.float32,
        ),
    }


class MemmapSplitWriter:
    """Pre-allocate on-disk memmap arrays and write windows at fixed offsets."""

    def __init__(self, tmp_dir, total_windows):
        self.tmp_dir = Path(tmp_dir)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.total_windows = total_windows
        self.offset = 0
        self.maps = {}
        for key, (dtype, tail_shape) in ARRAY_SPECS.items():
            path = self.tmp_dir / f'{key}.mmap'
            shape = (total_windows,) + tail_shape
            self.maps[key] = np.memmap(path, dtype=dtype, mode='w+', shape=shape)

    def append_batch(self, batch):
        n_new = batch['X_feat'].shape[0]
        if n_new == 0:
            return
        end = self.offset + n_new
        if end > self.total_windows:
            raise RuntimeError(
                f'window count mismatch: wrote past allocated size '
                f'({end} > {self.total_windows})',
            )
        for key in ARRAY_SPECS:
            self.maps[key][self.offset:end] = batch[key]
        self.offset = end

    def close(self):
        for mmap in self.maps.values():
            mmap.flush()

    def cleanup(self):
        self.close()
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir)


def merge_shards_into_memmap(shard_paths, tmp_dir, total_windows):
    """Merge per-file NPZ shards into pre-allocated memmap arrays."""
    writer = MemmapSplitWriter(tmp_dir, total_windows)
    for shard_path in shard_paths:
        with np.load(shard_path) as data:
            batch = {key: data[key] for key in ARRAY_SPECS}
            writer.append_batch(batch)
        shard_path.unlink()
        gc.collect()
    writer.close()
    return writer.maps


def build_split_memmap(file_entries, tmp_dir, split_name, file_id_offset=0,
                       baseline_mode=BASELINE_MODE_EMPTY_REF, global_baseline=None,
                       empty_by_date=None, expected_mac=EXPECTED_MAC):
    """Process files incrementally via per-file shards, then merge to memmap."""
    tmp_dir = Path(tmp_dir)
    shard_dir = tmp_dir / 'shards'
    shard_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows = []
    class_to_id = {'empty': 0, 'presence': 1, 'motion': 2, 'fall': 3}
    n_files = len(file_entries)
    shard_paths = []
    total_windows = 0
    session_baseline_cache = {}

    for local_id, entry in enumerate(file_entries):
        file_id = file_id_offset + local_id
        if baseline_mode == BASELINE_MODE_SESSION_MATCH:
            # Fall was briefly excluded here (its ~650 positive windows make a
            # retrain comparison hard to read). Re-included 2026-07-30: live
            # inference calibrates its baseline against the CURRENT empty room,
            # so fall captures must be baselined against their own same-day
            # empty reference too, or training and live see different feature
            # spaces — which is exactly what kept live fall detection from
            # firing.
            reference_baseline, baseline_source = resolve_session_baseline(
                entry['path'], global_baseline, empty_by_date or {},
                expected_mac, cache=session_baseline_cache,
            )
        elif baseline_mode in (BASELINE_MODE_EMPTY_REF, BASELINE_MODE_SESSION_MATCH):
            reference_baseline, baseline_source = global_baseline, 'global'
        else:
            reference_baseline, baseline_source = None, 'per_file'
        windows, n_packets, sample_rate, file_stats = process_file(
            entry, reference_baseline=reference_baseline,
        )
        n_file_windows = len(windows)
        if n_file_windows == 0:
            reason = file_stats.get('skipped_reason') or 'no_windows'
            print(
                f'  [{split_name} {local_id + 1}/{n_files}] '
                f'{entry["name"]} -> 0 windows ({reason}, '
                f'drop={file_stats.get("drop_pct", 0):.1f}%)',
                flush=True,
            )
            continue

        batch = stack_file_windows(
            windows, file_id, class_to_id[entry['label']],
        )
        shard_path = shard_dir / f'{local_id:05d}.npz'
        np.savez_compressed(shard_path, **batch)
        shard_paths.append(shard_path)
        total_windows += n_file_windows
        del windows, batch
        gc.collect()

        manifest_rows.append({
            'file_id': file_id,
            'file': entry['name'],
            'path': str(entry['path'].relative_to(DATASET_ROOT)),
            'label': entry['label'],
            'subgroup': entry['subgroup'],
            'raw_packets': file_stats.get('raw_packets', n_packets),
            'kept_packets': file_stats.get('kept_packets', n_packets),
            'drop_pct': file_stats.get('drop_pct', 0.0),
            'drop_spike': file_stats.get('drop_spike', 0),
            'drop_mad': file_stats.get('drop_mad', 0),
            'drop_jump': file_stats.get('drop_jump', 0),
            'drop_norm_spike': file_stats.get('drop_norm_spike', 0),
            'bad_file': file_stats.get('bad_file', 0),
            'windows_rejected': file_stats.get('windows_rejected', 0),
            'windows_gap_rejected': file_stats.get('windows_gap_rejected', 0),
            'sequence_gaps': file_stats.get('sequence_gaps', 0),
            'sequence_missing': file_stats.get('sequence_missing', 0),
            'sequence_large_gaps': file_stats.get('sequence_large_gaps', 0),
            'post_filter_sequence_large_gaps': file_stats.get(
                'post_filter_sequence_large_gaps', 0
            ),
            'capture_duration_sec': round(file_stats.get('capture_duration_sec', 0.0), 3),
            'packets': n_packets,
            'sample_rate_hz': round(sample_rate, 2),
            'windows': n_file_windows,
            'baseline_source': baseline_source,
        })

        print(
            f'  [{split_name} {local_id + 1}/{n_files}] '
            f'{entry["name"]} -> {n_file_windows} windows '
            f'(drop={file_stats.get("drop_pct", 0):.1f}%, '
            f'rej={file_stats.get("windows_rejected", 0)}, '
            f'total {total_windows})',
            flush=True,
        )

    if total_windows == 0:
        shutil.rmtree(shard_dir, ignore_errors=True)
        raise RuntimeError('no windows produced — check dataset paths')

    print(f'  merging {len(shard_paths)} shards for {split_name}...', flush=True)
    maps = merge_shards_into_memmap(shard_paths, tmp_dir, total_windows)
    shutil.rmtree(shard_dir, ignore_errors=True)
    return maps, manifest_rows, total_windows


def save_npz_compressed(path, memmap_arrays):
    """Write compressed NPZ from memmap arrays, one array at a time."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = path.parent / f'.npz_tmp_{path.stem}'
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(
            path, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True,
        ) as zf:
            for key in ARRAY_SPECS:
                tmp_npy = tmp_dir / f'{key}.npy'
                np.save(tmp_npy, memmap_arrays[key], allow_pickle=False)
                zf.write(tmp_npy, arcname=f'{key}.npy')
                tmp_npy.unlink()
                gc.collect()
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


def write_manifest(path, rows):
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_config(path, config):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w') as handle:
        json.dump(config, handle, indent=2)
        handle.write('\n')


def print_summary(name, npz_path, n_windows):
    with np.load(npz_path) as data:
        print(f'  {name}:')
        print(f'    windows     : {n_windows}')
        print(f'    X_feat        : {(n_windows, FEATURE_DIM)}')
        print(f'    X_seq_1s      : {(n_windows, TARGET_SEQ_1S, NUM_SUBCARRIERS)}')
        print(f'    X_seq_2s      : {(n_windows, TARGET_SEQ_2S, NUM_SUBCARRIERS)}')
        print(f'    y_occupied=1  : {int(data["y_occupied"].sum())}')
        print(f'    y_motion=1    : {int(data["y_motion"].sum())}')
        print(f'    y_fall=1      : {int(data["y_fall"].sum())}')


def main():
    global DATASET_ROOT, EMPTY_DIR, PRESENCE_DIR, MOTION_DIR, FALL_DIR, OUTPUT_ROOT

    parser = argparse.ArgumentParser(description='Preprocess CSI CSV dataset to NPZ windows')
    parser.add_argument('--dataset-root', default=str(DATASET_ROOT), help='Dataset root directory')
    parser.add_argument(
        '--fall-dir',
        default='',
        help='Fall capture directory (default: data/raw/fall_v2).',
    )
    parser.add_argument('--output-dir', default=str(OUTPUT_ROOT), help='Output directory for NPZ')
    parser.add_argument('--val-ratio', type=float, default=0.2, help='Validation split ratio by file')
    parser.add_argument('--seed', type=int, default=42, help='Random seed for split')
    parser.add_argument(
        '--baseline-mode',
        choices=(BASELINE_MODE_PER_FILE, BASELINE_MODE_EMPTY_REF, BASELINE_MODE_SESSION_MATCH),
        default=DEFAULT_BASELINE_MODE,
        help=(
            'Baseline subtraction mode (default: session_match — same-day '
            'empty-room reference per file when one exists, else falls back '
            'to the global reference; validated 2026-07-30, see docs/csi.md '
            '§4.1). empty_room_reference: single global reference for every '
            'file, as used before that date.'
        ),
    )
    parser.add_argument(
        '--classes',
        default='empty,presence,motion,fall',
        help='Comma-separated labels to include (default: all)',
    )
    parser.add_argument(
        '--empty-source',
        choices=('all', 'live', 'legacy'),
        default='all',
        help='Empty captures: all, live_empty_* only, or s*_empty only',
    )
    args = parser.parse_args()

    DATASET_ROOT = Path(args.dataset_root).resolve()
    EMPTY_DIR = DATASET_ROOT / 'empty_fan_on'
    PRESENCE_DIR = DATASET_ROOT / 'presence'
    MOTION_DIR = DATASET_ROOT / 'motion'
    FALL_DIR = Path(args.fall_dir).resolve() if args.fall_dir else DATASET_ROOT / 'fall_v2'
    OUTPUT_ROOT = Path(args.output_dir).resolve()

    print('=== CSI dataset preprocessing ===')
    print(f'dataset root: {DATASET_ROOT}')
    print(f'fall dir    : {FALL_DIR}')
    print(f'output dir  : {OUTPUT_ROOT}')
    print()

    file_entries = discover_capture_files(empty_source=args.empty_source)
    allowed = {c.strip() for c in args.classes.split(',') if c.strip()}
    if allowed:
        file_entries = [e for e in file_entries if e['label'] in allowed]
    if not file_entries:
        print('No capture files found.')
        return 1

    counts = {}
    for entry in file_entries:
        counts[entry['label']] = counts.get(entry['label'], 0) + 1
    print('Capture files found:')
    for label in ('empty', 'presence', 'motion', 'fall'):
        print(f'  {label:8s}: {counts.get(label, 0)}')
    print()

    train_files, val_files = split_files(file_entries, val_ratio=args.val_ratio, seed=args.seed)
    # Class weighting happens inside train_cascade.py. Do not duplicate source
    # recordings here, since duplicate windows add no new information.
    print(f'Train files: {len(train_files)} | Val files: {len(val_files)}')
    print(f'Baseline mode: {args.baseline_mode}')

    global_baseline = None
    empty_by_date = None
    if args.baseline_mode in (BASELINE_MODE_EMPTY_REF, BASELINE_MODE_SESSION_MATCH):
        global_baseline = compute_reference_baseline(EMPTY_DIR)
        print(f'Empty-room reference baseline computed from {EMPTY_DIR.name}')
    if args.baseline_mode == BASELINE_MODE_SESSION_MATCH:
        empty_by_date = group_empty_paths_by_date(EMPTY_DIR)
        print(f'Empty-room dates available for session matching: '
              f'{sorted(d.isoformat() for d in empty_by_date)}')
    print()

    print('Processing train split...')
    train_tmp = OUTPUT_ROOT / '.memmap_train'
    train_maps, train_manifest, train_n = build_split_memmap(
        train_files, train_tmp, 'train', file_id_offset=0,
        baseline_mode=args.baseline_mode, global_baseline=global_baseline,
        empty_by_date=empty_by_date,
    )
    print('Saving train.npz...')
    train_path = OUTPUT_ROOT / 'train.npz'
    save_npz_compressed(train_path, train_maps)
    del train_maps
    gc.collect()
    shutil.rmtree(train_tmp, ignore_errors=True)

    print('Processing val split...')
    val_tmp = OUTPUT_ROOT / '.memmap_val'
    val_maps, val_manifest, val_n = build_split_memmap(
        val_files, val_tmp, 'val', file_id_offset=len(train_files),
        baseline_mode=args.baseline_mode, global_baseline=global_baseline,
        empty_by_date=empty_by_date,
    )
    print('Saving val.npz...')
    val_path = OUTPUT_ROOT / 'val.npz'
    save_npz_compressed(val_path, val_maps)

    manifest_path = OUTPUT_ROOT / 'preprocess_manifest.csv'
    write_manifest(manifest_path, train_manifest + val_manifest)

    config = {
        'expected_len': EXPECTED_LEN,
        'expected_mac': EXPECTED_MAC,
        'num_subcarriers': NUM_SUBCARRIERS,
        'ht40_valid_sc_count': int(len(HT40_VALID_SC)),
        'ht40_guard_sc': sorted(HT40_GUARD_SC),
        'ht40_selected_sc': HT40_SELECTED_SC.tolist(),
        'packet_quality': {
            'max_valid_iq_abs': MAX_VALID_IQ_ABS,
            'max_valid_amp': MAX_VALID_AMP,
            'min_valid_sc_good_frac': MIN_VALID_SC_GOOD_FRAC,
        },
        'empty_source': args.empty_source,
        'window_2s_sec': WIN_2S,
        'window_1s_sec': WIN_1S,
        'hop_2s_sec': HOP_2S,
        'hop_1s_sec': HOP_1S,
        'hop_fall_sec': HOP_FALL,
        'fall_offset_sec_default': DEFAULT_FALL_OFFSET_SEC,
        'fall_window_sec_default': DEFAULT_FALL_WINDOW_SEC,
        'target_seq_1s': TARGET_SEQ_1S,
        'target_seq_2s': TARGET_SEQ_2S,
        'feature_dim': FEATURE_DIM,
        'class_ids': {'empty': 0, 'presence': 1, 'motion': 2, 'fall': 3},
        'train_files': len(train_files),
        'val_files': len(val_files),
        'baseline_mode': args.baseline_mode,
        'outlier_filter': {
            'spike_sc_amp_cap': SPIKE_SC_AMP_CAP,
            'spike_mean_amp_cap': SPIKE_MEAN_AMP_CAP,
            'mad_z_threshold': MAD_Z_THRESHOLD,
            'mad_mean_amp_cap': MAD_MEAN_AMP_CAP,
            'max_pkt_mean_jump': MAX_PKT_MEAN_JUMP,
            'min_keep_packet_ratio': MIN_KEEP_PACKET_RATIO,
            'max_window_delta': MAX_WINDOW_DELTA,
            'max_window_abs': MAX_WINDOW_ABS,
            'max_norm_pkt_abs': MAX_NORM_PKT_ABS,
            'max_missing_packets_per_window_gap': MAX_MISSING_PACKETS_PER_WINDOW_GAP,
        },
    }
    if global_baseline is not None:
        config['reference_baseline'] = global_baseline.tolist()
    try:
        config['fall_dir'] = str(FALL_DIR.relative_to(DATASET_ROOT))
    except ValueError:
        config['fall_dir'] = str(FALL_DIR)
    config['dataset_root'] = str(DATASET_ROOT)
    write_config(OUTPUT_ROOT / 'preprocess_config.json', config)

    print()
    print_summary('train', train_path, train_n)
    print_summary('val', val_path, val_n)
    del val_maps
    gc.collect()
    shutil.rmtree(val_tmp, ignore_errors=True)
    print()
    print('=== Done ===')
    print(f'train.npz : {train_path}')
    print(f'val.npz   : {val_path}')
    print(f'manifest  : {manifest_path}')
    print(f'config    : {OUTPUT_ROOT / "preprocess_config.json"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
