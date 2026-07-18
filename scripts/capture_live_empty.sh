#!/usr/bin/env bash
# Capture ~60s empty-room CSI for training data collection.
# Stay out of the room. Fan should be ON (same as presence/motion/fall captures).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}/python${PYTHONPATH:+:${PYTHONPATH}}"
DATA_ROOT="${DATA_ROOT:-${ROOT}/data}"

PORT="${1:-/dev/ttyACM0}"
OUT_DIR="${DATA_ROOT}/raw/empty_fan_on"
OUT="${OUT_DIR}/live_empty_$(date +%Y%m%d_%H%M%S).csv"
mkdir -p "$OUT_DIR"

echo "Capturing 60s empty CSI on ${PORT} → ${OUT}"
echo "Stay out of the room until this finishes."
python3 "${ROOT}/python/capture/capture_csi_binary.py" -p "$PORT" -s "$OUT" --duration 60
echo "Done: $OUT"
echo "Next: python3 ${ROOT}/python/preprocess/preprocess_csi.py"
