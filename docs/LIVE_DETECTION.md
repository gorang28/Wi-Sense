# Live cascade detection on ESP32-S3

This guide runs the trained cascade models with your existing hardware.

## Architecture (phase 1 — works today)

```
ESP32-CAM (csi_send)  --ESP-NOW-->  ESP32-S3 (csi_recv)  --USB 921600-->  Laptop
                                                                              |
                                                                    live_cascade_detect.py
                                                                    (3x TFLite models)
```

The ESP32 streams binary CSI. The laptop runs preprocessing + inference and prints
`EMPTY | PRESENCE | MOTION | FALL` in real time.

On-device TFLite (phase 2) requires ESP-IDF + `esp-tflite-micro` and is not yet
bundled in this repo.

## 1. Flash firmware (if not already done)

```bash
cd ~/esp-csi/examples/get-started/csi_recv
idf.py set-target esp32s3
idf.py build flash monitor
```

Flash `csi_send` on ESP32-CAM the same way from `examples/get-started/csi_send`.

Both must use **channel 11**, HT40, MAC `1a:00:00:00:00:00`.

## 2. Install Python deps on laptop

```bash
cd ~/esp-csi/examples/get-started/tools
source venv/bin/activate
pip install tensorflow pyserial
```

Ensure models exist:

```bash
ls models/*.tflite
# empty_occupied.tflite  motion.tflite  fall.tflite
```

If missing:

```bash
./train_models.sh
# empty: live_empty_retrain_*.csv | presence | motion | fall: fall_v2
```

Retrain empty only after new captures:

```bash
./capture_empty_retrain.sh /dev/ttyACM0
python3 preprocess_csi.py --empty-source live --classes empty
python3 train_empty_model.py
```

## 3. Find serial port

```bash
ls /dev/ttyACM* /dev/ttyUSB*
# Usually /dev/ttyACM0 for ESP32-S3 USB
```

Add user to dialout if permission denied:

```bash
sudo usermod -aG dialout $USER
# log out and back in
```

## 4. Run live detection

**Default flow (saved baseline + session calibration):**

1. **10 seconds** — exit countdown (`>>> EXIT ROOM NOW <<<`)
2. **15 seconds** — empty-room calibration (`... calibrating empty room ...`) — stay out
3. **`detections active`** — wait for `[ ] EMPTY` with `occ < 0.3`, **then** enter

Total: **25 seconds** with room empty before you should walk in.

```bash
python3 live_cascade_detect.py --port /dev/ttyACM0
# skip calibration (less reliable): --calibration-sec 0
# more time to leave: --exit-grace-sec 20 --calibration-sec 20
```

Offline sanity check (no ESP needed):

```bash
python3 eval_cascade.py --sweep          # tune thresholds on val split
python3 live_cascade_detect.py --replay-csv dataset/empty_fan_on/s001_empty.csv
python3 live_cascade_detect.py --replay-csv dataset/motion/arm_wave_center/s001_arm_wave_center.csv
```

Example output:

```
[ ] EMPTY    | occ=0.12 mot=0.00 fall=0.00 | pkts=4521
[.] PRESENCE | occ=0.91 mot=0.23 fall=0.01 | pkts=4580
[o] MOTION   | occ=0.88 mot=0.76 fall=0.02 | pkts=4635
[!] FALL       | occ=0.93 mot=0.41 fall=0.08 | pkts=4690
```

Symbols: space=empty, `.`=presence, `o`=motion, `!=`fall

## 5. Test checklist

| Action | Expected |
|--------|----------|
| Empty room, fan on | `EMPTY` |
| Stand still in room | `PRESENCE` |
| Walk / wave arms | `MOTION` |
| Fall (careful test) | `FALL` (may need 2 windows) |

## Thresholds (val-tuned)

| Stage | Threshold | Notes |
|-------|-----------|-------|
| Occupied ON | 0.65 | Needs 2 consecutive windows |
| Occupied OFF | 0.45 | Needs 3 consecutive windows to return empty |
| Motion | 0.50 | Takes priority over fall when moving |
| Fall | 0.40 | Only when relatively still; model is weak |

Run `python3 eval_cascade.py --sweep` to re-check on the held-out val split.

**Important:** Use default `--backend keras` on laptop. Always use the saved
baseline (default). Live 30 s calibration without the saved reference causes
false presence/motion because the model was trained on the global empty-room
reference, not a per-session median.

Tune live if needed:

```bash
python3 live_cascade_detect.py --port /dev/ttyACM0 \
  --occupied-on 0.70 --motion-thr 0.60 --fall-thr 0.35
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| No output / garbage | Reflash `csi_recv`, check 921600 baud |
| Permission denied on port | `sudo usermod -aG dialout $USER` |
| Always EMPTY | Use default saved baseline; do not use live-only calibration |
| False PRESENCE when empty | Re-run with saved baseline; try `--occupied-on 0.70` |
| Fall never triggers | Lower `--hop-sec 0.25`; collect more fall data |
| TensorFlow crash | Use `venv/bin/python3`, not system python |

## Next: on-device inference (future)

To run models on ESP32-S3 without a laptop:

1. Embed `.tflite` in `csi_recv/components/csi_cascade/`
2. Add `espressif/esp-tflite-micro` via Component Manager
3. Port `preprocess_csi.py` windowing to C in a FreeRTOS task
4. Print `DETECT_JSON` lines over USB instead of full binary CSI

Model sizes (~45 KB total) fit ESP32-S3 PSRAM/RAM with careful tensor arena sizing.
