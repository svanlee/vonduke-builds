# AKSUMAEL v1.0.0

Standalone AI game-playing agent. Watches the game via HDMI capture,
decides via Gemini vision + learned skills, sends inputs via KB2040 USB HID.

---

## Final Architecture

```
Game PC / Console
    │ HDMI out
    ▼
Rybozen Capture Card ── USB ──→ Raspberry Pi 4 (brain)
    │ HDMI loop out                  │
    ▼                                │ UART (GPIO 14/15)
Your Monitor (game visible)          ▼
                                 KB2040 (USB HID kb+mouse+gamepad)
                                     │ USB-C
                                     ▼
                          PC ─ or ─ Brook Wingman ─→ Console
```

**Brook Wingman family = console expansion.** Same KB2040, different Brook box:
XB3 → Xbox Series X/S · PS2 → PS1/PS2 · SNES → NES/SNES · SD → Saturn/Dreamcast

---

## Hardware

| Part | Role |
|---|---|
| Raspberry Pi 4 | Brain — vision, decisions, skills |
| Rybozen capture card | HDMI video + audio into the Pi |
| KB2040 | UART → USB HID output (keyboard+mouse+gamepad) |
| Brook Wingman XB3 | Console auth pass-through (Xbox) |
| I2C joystick (0x5A) | Physical control panel / manual input fallback |
| 7" monitor | Pi display for the labeling UI |
| CH9329 | Backup HID chip (PC only, keyboard+mouse) |

---

## Wiring

### Pi → KB2040 (UART)
```
Pi Pin 8  (GPIO 14 TX) → KB2040 D0 (RX)
Pi Pin 10 (GPIO 15 RX) ← KB2040 D1 (TX)
Pi Pin 6  (GND)        ── KB2040 GND
KB2040 USB-C           → PC  (or Brook Wingman → console)
```

### Pi → I2C Joystick
```
Pi Pin 1 (3.3V) → VCC      Pi Pin 3 (SDA) ↔ SDA
Pi Pin 9 (GND)  ── GND     Pi Pin 5 (SCL) ↔ SCL
```

### Capture card
```
Game HDMI out → card HDMI IN
card HDMI loop out → your monitor
card USB → Pi
```

---

## DAY 1 TEST PLAN — run in this order

### 0. One-time Pi setup
```bash
sudo raspi-config
#   Interface Options → Serial Port → shell: No, hardware: Yes
#   Interface Options → I2C → Enable
sudo reboot

ls -la /dev/serial0     # must point to ttyAMA0
# If it points to ttyS0:
echo "dtoverlay=disable-bt" | sudo tee -a /boot/config.txt && sudo reboot

cd ~/AKSUMAEL_v1_0_0
chmod +x install.sh && ./install.sh
nano config.py          # paste GEMINI_API_KEY
```

### 1. Flash the KB2040 (on any computer)
Follow `rp2040/README.md`:
CircuitPython UF2 → adafruit_hid lib → boot.py + code.py.

### 2. Validate KB2040 ← run before anything else
```bash
# KB2040 wired to Pi UART, USB-C into PC, Notepad focused on PC
python3 tools/kb2040_test.py all
```
Pass = text typed, cursor traced a square, gamepad visible in joy.cpl.

### 3. Validate the capture card
```bash
v4l2-ctl --list-devices          # find the video device
arecord -l                       # find "USB3.0 Audio" card number
python3 - << 'EOF'
import cv2
cap = cv2.VideoCapture(0)
ret, frame = cap.read()
print('Frame:', frame.shape if ret else 'NONE — check HDMI source is on')
cap.release()
EOF
# Set GAME_AUDIO_INDEX in config.py to the arecord card number
```

### 4. Validate the joystick pipeline
```bash
i2cdetect -y 1                   # should show 5a
python3 tools/joystick_harness.py
# Move stick / press buttons → inputs appear on the PC
```

### 5. Dry-run AKSUMAEL
```bash
# config.py: ACTION_OUTPUT = "print"   (default)
python3 main.py
# Confirm: capture window shows the game, YOLO boxes appear,
# Gemini observations print, [ACTION] lines look sane
```

### 6. Go live
```bash
# config.py: ACTION_OUTPUT = "kb2040"
python3 main.py
# AKSUMAEL plays.
```

---

## The Labeling UI (on the Pi's 7" monitor)

| Input | Action |
|---|---|
| Click a red box, type, Enter | Teach AKSUMAEL what an object is |
| TAB | Next unknown box |
| `g` / `b` | Good / bad reward |
| `p` | Pause / resume |
| `m` | Cycle blend mode |
| `s` | Toggle skill sidebar |
| `q` | Quit |

Joystick: A=good · B=bad · C=pause · D=cycle mode · stick=manual input

---

## Skill System

High-reward action sequences are mined into skills, matched by on-screen
objects (with Minecraft synonym fuzzy matching), and replayed with original
timing instead of calling Gemini.

```bash
python3 tools/skill_manager.py list | show <name> | delete <name> | prune 0.0 | stats
```

## YOLO Fine-Tuning

```bash
# config.py: COLLECT_FRAMES = True  → frames auto-save during play
python3 tools/yolo_finetune.py status
python3 tools/yolo_finetune.py train 10     # on the Pi (slow) or a laptop
# Then: YOLO_MODEL = "data/models/aksumael_mc.pt"
```

---

## Module Map

```
config.py                    all settings
main.py                      entry point
core/runtime.py              main loop
core/vision_brain.py         Gemini / Claude vision calls
core/world_model.py          session-persistent history
uart/kb2040_packer.py        KB2040 packet protocol (primary)
uart/ch9329_packer.py        CH9329 protocol (backup)
rp2040/boot.py, code.py      KB2040 CircuitPython firmware
input/controller_router.py   evdev → I2C joystick → aksumael chain
audio/tts.py                 Cortana-style voice (pyttsx3/ElevenLabs)
audio/voice_persona.py       personality lines
audio/game_ear.py            game-audio classifier (graceful if no device)
vision/screen.py             capture card input
vision/yolo.py               detection + user label DB
skills/skill_system.py       mine / match / replay
ui/labeling.py               click-to-label window + skill sidebar
actions/executor.py          kb2040 | ch9329 | print
memory/reward.py             vision + audio + manual reward
tools/kb2040_test.py         ← run FIRST on hardware day
tools/joystick_harness.py    full pipeline test, no Gemini
tools/skill_manager.py       skill CLI
tools/yolo_finetune.py       Minecraft YOLO training pipeline
tools/ch9329_config.py       CH9329 validator (backup path)
```

---

## Known First-Run Issues

| Symptom | Fix |
|---|---|
| `/dev/serial0` → ttyS0 | `dtoverlay=disable-bt` in /boot/config.txt, reboot |
| KB2040 not typing | Re-check TX/RX crossed; re-flash with BOOT held |
| CIRCUITPY drive gone | Normal — boot.py hides it. BOOT button to re-flash |
| No video frame | HDMI source on? `sudo usermod -aG video $USER`, re-login |
| Joystick missing in i2cdetect | SDA/SCL swapped, or VCC on 5V instead of 3.3V |
| Gemini 400 error | Key wrong/not yet active — wait a few min after creating |
| Game ear disabled | Fine — runs without audio. Set GAME_AUDIO_INDEX later |
| Gamepad not in joy.cpl | Wait 30 s; try another USB port; check lib folder copied |
