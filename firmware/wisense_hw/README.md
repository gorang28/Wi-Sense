# wisense_hw — Hardware bring-up firmware

ESP-IDF 5.5 project for developing WiSense peripherals on an **ESP32 DevKit**
before merging into `csi_recv` (ESP32-S3) with TinyML.

## Module 1 (done)

- SSD1306 OLED 128x64 over I2C (SDA=GPIO21, SCL=GPIO22, addr=0x3C)
- Placeholder classifier via UART keys: `e` Empty, `p` Presence, `m` Motion, `f` Fall

## Module 2 (done)

- JQC3F-05VDC-C relay on **GPIO26** (default: **HIGH = relay ON / light on**, **LOW = empty / light off**)
- Digital LDR module DO on **GPIO32**
- **Empty → Presence/Motion** in a dark room turns the relay ON immediately
- **Empty → Presence/Motion** in daylight leaves the light OFF, but LDR is polled every 500 ms
- **While Presence/Motion**, if the room becomes dark → relay ON
- **Presence/Motion → Empty** turns relay OFF after **5 seconds** if still Empty

## Module 3 (done)

- FSR406 pressure sensor on **GPIO34** (ADC analog input)
- **Presence + pressure on bed** → treat as sleeping → relay **OFF**
- **Motion** while on bed still uses normal LDR light rules (not sleep mode)

## Module 4+5 (current)

- Cancel push button on **GPIO27** (other leg to GND, active LOW)
- **`f` (Fall)** starts **15 s** emergency countdown on OLED
- **Press button** during countdown → cancel emergency
- **Countdown reaches 0** → OLED shows **ALERT SENT** (ESP-NOW to TX in Module 7)

## Build / flash / monitor

```bash
cd /home/praful/Wi-Sense/firmware/wisense_hw
. ~/esp/esp-idf/export.sh
idf.py set-target esp32
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

In the monitor, type `e`, `p`, `m`, or `f` (no Enter required). The OLED should
update to Empty / Presence / Motion / Fall.

**Module 3 test:** Dark room, `e` → `p` with **no** pressure → light ON.
Press on FSR while still on `p` → light OFF (`Sleeping on bed` in log).

Tune FSR sensitivity: **menuconfig → WiSense FSR → Raw ADC threshold** (default 1500).
Watch the boot log `FSR ready (GPIO34 raw=...)` and adjust threshold above idle, below pressed.

**Module 4+5 test:** Type `f` → OLED shows **EMERGENCY** and **15 sec** countdown.
Press GPIO27 button → cancelled. Type `f` again, wait 15 s → **ALERT SENT** in log.

## Wiring

| OLED pin | ESP32 |
|----------|-------|
| VCC      | 3V3   |
| GND      | GND   |
| SDA      | GPIO21 |
| SCL      | GPIO22 |

| Relay IN | GPIO26 |
| Relay VCC| 5V (module supply — do not feed 5V into GPIO) |
| Relay GND| GND    |

| LDR DO   | GPIO32 |
| LDR VCC  | 3V3    |
| LDR GND  | GND    |

| FSR (via divider) | GPIO34 |
| FSR GND           | GND    |

| Button            | GPIO27 |
| Button            | GND    |

Default LDR: **HIGH = dark**, **LOW = bright**.

If relay or LDR polarity is inverted on your modules, change
`Component config → WiSense Light (Relay + LDR)` in menuconfig.
