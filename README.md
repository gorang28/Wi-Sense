# WiSense — WiFi CSI Sensing Pipeline

ESP32 CSI capture, dataset collection, and preprocessing for presence / motion / fall detection. Built on Espressif's `esp-csi` get-started examples.

**Docs:** [docs/handoff.md](docs/handoff.md) · [docs/LIVE_DETECTION.md](docs/LIVE_DETECTION.md)  
**Upstream demo (ASCII CSI):** [README_cn.md](README_cn.md)

## Layout

```
├── README.md
├── docs/                    # handoff, live detection notes
├── firmware/
│   ├── csi_send/            # ESP-NOW transmitter
│   ├── csi_recv/            # CSI receiver (binary UART)
│   └── csi_recv_router/     # optional router variant
├── python/
│   ├── proto/               # csi_binary_proto.py
│   ├── capture/             # capture_csi_binary.py
│   ├── collect/             # guided dataset collection
│   ├── preprocess/            # preprocess_csi.py
│   ├── train/                 # (empty — add training scripts)
│   ├── eval/                  # (empty — add eval scripts)
│   ├── live/                  # (empty — add live inference)
│   └── legacy/                # original ASCII CSI viewer
├── data/
│   ├── raw/                   # CSI CSV captures (gitignored)
│   └── processed/             # NPZ windows from preprocess
├── models/                    # trained artifacts (gitignored)
├── scripts/                   # shell helpers
├── requirements.txt
└── .env.example               # DATA_ROOT, MODELS_DIR
```

## Quick start

### 1. Flash firmware

```shell
cd firmware/csi_send
idf.py set-target esp32s3
idf.py flash -b 921600 -p COM3 monitor

cd ../csi_recv
idf.py set-target esp32s3
idf.py flash -b 921600 -p COM4
```

### 2. Python environment

```shell
python -m venv venv
# Windows: venv\Scripts\activate
# Linux:   source venv/bin/activate
pip install -r requirements.txt
```

Set `PYTHONPATH` to the `python/` directory (or run scripts from repo root as shown below).

Optional: copy `.env.example` → `.env` and set `DATA_ROOT` / `MODELS_DIR`.

### 3. Capture CSI

```shell
export PYTHONPATH=./python   # Windows: set PYTHONPATH=python
python python/capture/capture_csi_binary.py -p COM4 -s data/raw/empty_fan_on/test.csv --duration 60
```

Or use the helper script (Linux/macOS):

```shell
./scripts/capture_live_empty.sh /dev/ttyACM0
```

### 4. Collect datasets

```shell
python python/collect/collect_empty_room.py --fan-on
python python/collect/collect_presence_session.py
python python/collect/collect_motion_session.py
python python/collect/collect_fall_session.py
```

### 5. Preprocess

```shell
python python/preprocess/preprocess_csi.py --classes empty,presence,motion,fall
```

Output lands in `data/processed/` (`train.npz`, `val.npz`, config).

### 6. Train models (from scratch)

The `python/train/`, `python/eval/`, and `python/live/` directories are intentionally empty. Add your training, evaluation, and live inference scripts there after preprocessing.

## Legacy ASCII CSI viewer

The original Espressif PyQt5 demo lives in `python/legacy/`:

```shell
python python/legacy/csi_data_read_parse.py -p COM4
```

## Data & git

Raw CSV captures and trained models are **gitignored** (large / regeneratable). Back up `data/raw/` outside the repo or use git-lfs if needed.
