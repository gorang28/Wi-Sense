# WiSense — Project Handoff (for next developer)

**Last updated:** 2026-07-23  
**Repo root:** `/home/praful/Wi-Sense` (or your clone of this GitHub repo)  
**ESP-IDF:** 5.5.x recommended (project built with 5.5)

This document is the **continuation brief** for Cursor / a new laptop. Read this first, then open the firmware paths below.

**Also see:**
- [LIVE_DETECTION.md](./LIVE_DETECTION.md) — CSI ML live detection runbook (Python cascade)
- [../README.md](../README.md) — repo layout / CSI capture overview
- [../firmware/wisense_hw/README.md](../firmware/wisense_hw/README.md) — RX DevKit bring-up details

---

## 0. Where we left off (critical)

### Last thing implemented **and hardware-checked**

**RX ↔ TX emergency over ESP-NOW works end-to-end.**

Verified flow:

1. On RX DevKit (`wisense_hw`), type `f` in serial monitor → 15 s OLED countdown + fast buzzer.
2. After 15 s → RX sends **FALL_ALERT** ESP-NOW packet to ESP32-CAM.
3. TX (`csi_send`) receives it → emergency orchestrator runs (`IDLE → ALERT → RUNNING`).
4. Press cancel button on RX (GPIO27) → RX sends **FALL_CANCEL** → TX shuts down to `IDLE`.
5. CSI counter send on TX **keeps running** the whole time.

### Last code milestone on TX

**TX-1 — Emergency orchestrator** is implemented (stage stubs for servo/cam/mic/stream).  
**TX-2+ not started** (real servo / camera / mic / stream still stub logs only).

---

## 1. Two workstreams in this repo

| Workstream | Status | Where |
|------------|--------|--------|
| **A. CSI sensing + ML cascade** | Earlier work; presence/motion live; fall weak | `firmware/csi_*`, `python/`, `docs/LIVE_DETECTION.md` |
| **B. Hardware automation (current focus)** | RX done; TX-1 done + ESP-NOW verified | `firmware/wisense_hw`, `firmware/csi_send`, `firmware/components/wisense_*` |

**You are continuing workstream B** (TX modules), then later merge RX logic into `csi_recv` (ESP32-S3) after TinyML.

---

## 2. Board roles (current bring-up)

| Role | Board | Firmware project | Target |
|------|-------|------------------|--------|
| **RX bring-up** | ESP32 DevKit | `firmware/wisense_hw` | `esp32` |
| **TX (CSI + emergency)** | ESP32-CAM | `firmware/csi_send` | `esp32` |
| **Final RX (later)** | ESP32-S3 | `firmware/csi_recv` | `esp32s3` — merge after TinyML |

**Shared radio settings:** Wi-Fi channel **11**, HT40, TX MAC **`1a:00:00:00:00:00`**.

> DevKit RX is temporary. Production RX is ESP32-S3 `csi_recv` after TinyML replaces the UART placeholder classifier (`e/p/m/f`).

---

## 3. RX status — DONE (Modules 1–7)

Bring-up firmware: **`firmware/wisense_hw`**.  
Shared components: **`firmware/components/wisense_*`**.

| Module | Feature | Status |
|--------|---------|--------|
| 1 | SSD1306 OLED + UART classifier `e/p/m/f` | Done |
| 2 | Relay + LDR light automation | Done |
| 3 | FSR406 bed pressure → sleep → light off | Done |
| 4+5 | Fall emergency: 15 s countdown, cancel button, buzzer, LED | Done |
| 6 | (folded into 4+5 OLED emergency UI) | Done |
| 7 | ESP-NOW FALL_ALERT / FALL_CANCEL → TX | Done + verified |

### RX GPIO map (DevKit)

| Function | GPIO | Notes |
|----------|------|-------|
| OLED SDA / SCL | 21 / 22 | I2C addr `0x3C` |
| Relay | 26 | **HIGH = light ON** |
| LDR DO | 32 | **HIGH = dark** |
| FSR ADC | 34 | threshold default 1500 |
| Cancel button | 27 | active LOW to GND |
| Buzzer | 25 | fast beep countdown; continuous after alert |
| Emergency LED | 23 | flashes with beeps; solid in alert |

### RX behavior summary

- `e` Empty / `p` Presence / `m` Motion / `f` Fall (UART, no Enter).
- Empty→Presence/Motion + dark → relay ON; bright → poll LDR every 500 ms.
- Presence/Motion→Empty → relay OFF after **5 s**.
- Presence + FSR pressed → sleeping → relay OFF.
- Fall → 15 s countdown; button cancels during countdown **and** after alert.
- After alert: ESP-NOW **FALL_ALERT**; on cancel after alert: **FALL_CANCEL**.
- **No speaker/DFPlayer on TX** — RX buzzer is the alert audio.

### Build / flash RX

```bash
cd firmware/wisense_hw
. ~/esp/esp-idf/export.sh   # adjust path for your machine
idf.py set-target esp32
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor   # adjust port
```

---

## 4. TX status — TX-1 done; TX-2+ next

Firmware: **`firmware/csi_send`** (ESP32-CAM).

| Module | Feature | Status |
|--------|---------|--------|
| CSI ESP-NOW send | 4-byte counter @ 100 Hz, ch 11, MAC `1a:00:00:00:00:00` | Done (upstream) |
| ESP-NOW emergency recv | Magic packet filter; ignore CSI counter | Done (Module 7) |
| **TX-1** Emergency orchestrator | State machine + stage hooks | **Done** |
| **TX-2** Servo privacy flap | LEDC PWM | **Next** |
| **TX-3** Camera OV2640 | Init on alert | Not started |
| **TX-4** INMP441 I2S mic | | Not started |
| **TX-5** Stream pipeline | Local first; Firebase later | Not started |
| **TX-6** Full sequence wiring | Tie stages together | Not started |

### TX free GPIOs (user confirmed) + pin plan

Free on CAM: **0, 2, 4, 12, 13, 14, 15, 16**

| Use | GPIO | Notes |
|-----|------|-------|
| **Servo signal (TX-2)** | **13** | Prefer this; not a strap pin |
| INMP441 BCK | 14 | TX-4 |
| INMP441 WS | 15 | TX-4; keep idle HIGH at boot |
| INMP441 SD | 4 | TX-4 |
| Avoid | 0, 12 | Boot/strapping |
| Careful | 2, 16 | Strap / possible PSRAM |

**Connections already done by previous developer** (servo + mic wired per plan above).

### Build / flash TX

```bash
cd firmware/csi_send
. ~/esp/esp-idf/export.sh
idf.py set-target esp32
idf.py build
idf.py -p /dev/ttyUSB1 flash monitor   # adjust port; CAM often different from RX
```

---

## 5. ESP-NOW emergency protocol (shared)

Component: `firmware/components/wisense_espnow/`

**Packet (8 bytes, little-endian)** — distinct from CSI’s 4-byte `uint32_t` counter:

| Field | Size | Value |
|-------|------|-------|
| magic | 4 | `0xE911C1A5` |
| type | 1 | `1` = FALL_ALERT, `2` = FALL_CANCEL |
| seq | 1 | incrementing |
| reserved | 2 | `0` |

Headers:
- `include/wisense_espnow_proto.h`
- `include/wisense_espnow_send.h` (RX)
- `include/wisense_espnow_recv.h` (TX)

RX send path: `wisense_emergency_notify.c` → `wisense_espnow_emerg_send()`.  
TX recv path: `wisense_espnow_recv.c` → callback in `csi_send/main/emergency_tx.c` → orchestrator.

---

## 6. TX-1 orchestrator architecture

Component: `firmware/components/wisense_emergency_tx/`

```
ESP-NOW callback (ISR context-safe)
  → queue event
  → worker task runs stages
  → CSI send loop in app_main NEVER blocked
```

**States:** `IDLE → ALERT → RUNNING → CANCELLED → IDLE`

**On FALL_ALERT:**
1. Servo open (stub today)
2. Pre-stream delay (default **2000 ms**, Kconfig)
3. Camera start (stub)
4. Mic start (stub)
5. Stream start (stub) → **RUNNING**

**On FALL_CANCEL:** stream stop → mic stop → camera stop → servo close → **IDLE**

### Stage hooks (weak stubs — override in later modules)

Declared in `wisense_emergency_tx_internal.h`, weak defaults in `wisense_emergency_tx_stages.c`:

- `wisense_emergency_tx_stage_servo_open/close`
- `wisense_emergency_tx_stage_camera_start/stop`
- `wisense_emergency_tx_stage_mic_start/stop`
- `wisense_emergency_tx_stage_stream_start/stop`

**TX-2 should provide strong `servo_*` implementations** (e.g. new `wisense_servo` component) so the orchestrator calls real PWM without changing TX-1 flow.

App glue: `firmware/csi_send/main/emergency_tx.c` + `emergency_tx.h`.

---

## 7. Key source paths

```
firmware/
├── wisense_hw/                    # RX DevKit bring-up app
│   ├── main/app_main.c
│   ├── main/emergency_rx.c
│   └── README.md
├── csi_send/                      # TX ESP32-CAM app
│   ├── main/app_main.c            # CSI send loop (do not block)
│   ├── main/emergency_tx.c        # ESP-NOW → orchestrator glue
│   └── sdkconfig.defaults
├── csi_recv/                      # Final ESP32-S3 RX (later merge)
└── components/
    ├── wisense_oled/
    ├── wisense_classifier/        # UART placeholder e/p/m/f
    ├── wisense_light/             # relay + LDR
    ├── wisense_fsr/
    ├── wisense_emergency/         # RX emergency SM + button/buzzer/LED/notify
    ├── wisense_espnow/            # shared emergency protocol + send/recv
    └── wisense_emergency_tx/      # TX-1 orchestrator + weak stage stubs
```

`EXTRA_COMPONENT_DIRS` points at `firmware/components` from both `wisense_hw` and `csi_send`.

---

## 8. Two-board emergency test (regression)

1. Flash `csi_send` on ESP32-CAM, `wisense_hw` on DevKit (both channel 11).
2. Open both monitors.
3. On RX: type `f` → wait 15 s.
4. Expect TX logs like:
   - `FALL_ALERT — starting TX emergency workflow`
   - `state IDLE → ALERT` … → `RUNNING`
   - stub lines: `servo open`, `camera start`, `mic start`, `stream start`
5. Press RX GPIO27 → TX should log `FALL_CANCEL` and return to `IDLE`.
6. Confirm CSI send still logging / looping on TX.

If ESP-NOW fails: check both on channel 11, TX MAC `1a:00:00:00:00:00`, same 2.4 GHz environment, RX sender init ran (`wisense_espnow_emerg_sender_init`).

---

## 9. What to implement next (ordered)

```
TX-2  Servo on GPIO 13 (open on alert, close on cancel)
  → TX-3  Camera init after alert
    → TX-4  INMP441 on BCK=14, WS=15, SD=4
      → TX-5  Local stream stub (Firebase later)
        → TX-6  Full sequence polish
          → Later: merge RX wisense_* into csi_recv + TinyML
```

### TX-2 acceptance criteria (suggested)

- New component e.g. `firmware/components/wisense_servo/` using LEDC PWM.
- Strong overrides of `wisense_emergency_tx_stage_servo_open/close`.
- Kconfig: GPIO default **13**, open/closed angles, pulse widths.
- FALL_ALERT → flap opens; FALL_CANCEL → flap closes.
- CSI loop uninterrupted.

### Constraints (do not break)

1. **CSI `esp_now_send` loop in `csi_send/main/app_main.c` must keep running.**
2. Emergency work in tasks/timers only — never long-block the send loop.
3. Stay on **channel 11** and TX MAC **`1a:00:00:00:00:00`**.
4. Avoid camera pin conflicts; stick to the free-GPIO plan above.
5. No DFPlayer/speaker on TX (RX buzzer only).

---

## 10. CSI / ML stack (background — not the current coding focus)

Original pipeline: ESP32-CAM CSI TX → ESP32-S3 CSI RX → USB binary @ 921600 → laptop sklearn cascade.

| State | Live status (approx.) |
|-------|------------------------|
| EMPTY | Fixed with cal-over-ML fallback; retrain recommended |
| PRESENCE | Working |
| MOTION | Working |
| FALL | Weak model; experimental |

Python lives under `python/`; data under `data/`; ops in `docs/LIVE_DETECTION.md`.  
Do **not** mix this path into TX-2 servo work unless asked.

---

## 11. Cursor tips for the next session

Suggested first prompt after `git pull`:

> Continue WiSense from `docs/handoff.md`. RX is done. TX-1 orchestrator is done and ESP-NOW FALL_ALERT/FALL_CANCEL was verified on hardware. Implement **TX-2 servo** on GPIO 13 using strong overrides of `wisense_emergency_tx_stage_servo_*`. Do not block the CSI send loop in `csi_send/main/app_main.c`.

Useful files to open first:
1. `docs/handoff.md` (this file)
2. `firmware/components/wisense_emergency_tx/wisense_emergency_tx.c`
3. `firmware/components/wisense_emergency_tx/wisense_emergency_tx_stages.c`
4. `firmware/csi_send/main/emergency_tx.c`
5. `firmware/wisense_hw/README.md`

---

## 12. Quick command cheat sheet

```bash
# RX DevKit
cd firmware/wisense_hw && idf.py -p PORT flash monitor
# keys: e p m f

# TX ESP32-CAM
cd firmware/csi_send && idf.py -p PORT flash monitor

# menuconfig (TX orchestrator delay / later servo)
cd firmware/csi_send && idf.py menuconfig
# → Component config → WiSense TX Emergency Orchestrator
```

---

## 13. Done vs not done (one-glance)

| Item | Done? |
|------|-------|
| RX OLED / light / FSR / emergency / ESP-NOW send | Yes |
| TX CSI send | Yes |
| TX ESP-NOW emergency receive | Yes |
| TX-1 orchestrator (states + stub stages) | Yes |
| **Hardware verify: RX↔TX emergency ESP-NOW** | **Yes** |
| TX-2 real servo GPIO13 | No |
| TX-3 camera | No |
| TX-4 mic | No |
| TX-5/6 stream + full sequence | No |
| Merge into `csi_recv` + TinyML | No |
| Firebase streaming | No (later) |
