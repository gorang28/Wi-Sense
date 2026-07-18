#!/usr/bin/env bash
# Capture 30 minutes of empty-room CSI from live binary stream (csi_recv).
# Room must stay empty entire time. Fan ON, channel 11 HT40.
#
# Usage:
#   ./scripts/capture_live_empty_30min.sh                  # default /dev/ttyACM0
#   ./scripts/capture_live_empty_30min.sh /dev/ttyUSB0
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}/python${PYTHONPATH:+:${PYTHONPATH}}"
DATA_ROOT="${DATA_ROOT:-${ROOT}/data}"

PORT="${1:-/dev/ttyACM0}"
DURATION_SEC=$((30 * 60))
OUT_DIR="${DATA_ROOT}/raw/empty_fan_on"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="${OUT_DIR}/live_empty_30m_${STAMP}.csv"
LOG="${OUT_DIR}/live_empty_30m_${STAMP}.log"

mkdir -p "$OUT_DIR"

if [[ ! -e "$PORT" ]]; then
  echo "Serial port not found: $PORT" >&2
  echo "Try: ls /dev/ttyACM* /dev/ttyUSB*" >&2
  exit 1
fi

echo "=== Empty-room live capture (30 min) ==="
echo "Port     : $PORT"
echo "Duration : ${DURATION_SEC}s (30 min)"
echo "Output   : $OUT"
echo "Log      : $LOG"
echo
echo "Before starting:"
echo "  - Close idf.py monitor / any other tool using $PORT"
echo "  - ESP32-CAM + ESP32-S3 powered, channel 11 HT40"
echo "  - Fan ON"
echo "  - LEAVE THE ROOM — stay out for the full 30 minutes"
echo
read -r -p "Press Enter when room is empty and you are ready to start..."

echo "Capturing... (Ctrl+C stops early; partial file is kept)"
python3 "${ROOT}/python/capture/capture_csi_binary.py" \
  -p "$PORT" \
  -s "$OUT" \
  -l "$LOG" \
  --duration "$DURATION_SEC" \
  --report-interval 30

echo
echo "Done."
echo "  CSV : $OUT"
echo "  Log : $LOG"
echo
echo "Next: python3 ${ROOT}/python/preprocess/preprocess_csi.py"
