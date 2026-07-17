# AKSUMAEL v1.0.0

Standalone AI game-playing agent. Watches the game via HDMI capture,
decides via a local/Gemini/Claude 3-tier LLM router + learned skills,
sends inputs via KB2040 USB HID.

---

## Final Architecture

```
Game PC / Console
    │ HDMI out
    ▼
Rybozen Capture Card ── USB ──→ Laptop (RTX 4050, brain)
    │ HDMI loop out                  │
    ▼                                │ UART (FTDI USB-TTL adapter)
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
| Laptop (HP Victus, RTX 4050) | Brain — vision, decisions, skills |
| Rybozen capture card | HDMI video + audio into the laptop |
| FTDI FT232RL USB-TTL adapter | UART bridge, laptop → KB2040 |
| KB2040 | UART → USB HID output (keyboard+mouse+gamepad) |
| Brook Wingman XB3 | Console auth pass-through (Xbox) |
| CH9329 | Backup HID chip (PC only, keyboard+mouse) |

No Raspberry Pi, 7" display, or I2C joystick module in the current
setup — all removed; earlier revisions of this doc assumed that
Pi-based rig.

---

## Wiring

### Laptop → KB2040 (UART via FTDI adapter)
```
FTDI TX  → KB2040 D0 (RX)
FTDI RX  ← KB2040 D1 (TX)
FTDI GND ── KB2040 GND
FTDI USB → laptop (config.py: UART_PORT = "/dev/ttyUSB0")
KB2040 USB-C → PC  (or Brook Wingman → console)
```

### Capture card
```
Game HDMI out → card HDMI IN
card HDMI loop out → your monitor
card USB → laptop
```

---

## DAY 1 TEST PLAN — run in this order

### 0. One-time setup
```bash
cd AKSUMAEL
chmod +x install.sh && ./install.sh
# config.py: VISION_PROVIDER defaults to "local" (mesh-llm) — set
# GEMINI_API_KEY / ANTHROPIC_API_KEY env vars for the fallback tiers.
```

### 1. Flash the KB2040 (on any computer)
Follow `rp2040/README.md`:
CircuitPython UF2 → adafruit_hid lib → boot.py + code.py.

### 2. Validate KB2040 ← run before anything else
```bash
# KB2040 wired to the laptop via the FTDI adapter, USB-C into PC, Notepad focused on PC
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

### 4. Dry-run AKSUMAEL
```bash
# config.py: ACTION_OUTPUT = "print"   (default)
python3 main.py
# Confirm: capture window shows the game, YOLO boxes appear,
# vision-LLM observations print, [ACTION] lines look sane
```

### 5. Go live
```bash
# config.py: ACTION_OUTPUT = "kb2040"
python3 main.py
# AKSUMAEL plays.
```

---

## The Labeling UI

| Input | Action |
|---|---|
| Click a red box, type, Enter | Teach AKSUMAEL what an object is |
| TAB | Next unknown box |
| `g` / `b` | Good / bad reward |
| `p` | Pause / resume |
| `m` | Cycle blend mode |
| `s` | Toggle skill sidebar |
| `q` | Quit |

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
core/vision_brain.py         local/Gemini/Claude vision calls (core/llm_router.py)
core/world_model.py          session-persistent history
uart/kb2040_packer.py        KB2040 packet protocol (primary)
uart/ch9329_packer.py        CH9329 protocol (backup)
rp2040/boot.py, code.py      KB2040 CircuitPython firmware
input/controller_router.py   evdev → aksumael chain
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
tools/skill_manager.py       skill CLI
tools/yolo_finetune.py       Minecraft YOLO training pipeline
tools/ch9329_config.py       CH9329 validator (backup path)
```

---

## Known First-Run Issues

| Symptom | Fix |
|---|---|
| KB2040 not typing | Re-check TX/RX crossed; re-flash with BOOT held |
| CIRCUITPY drive gone | Normal — boot.py hides it. BOOT button to re-flash |
| No video frame | HDMI source on? Check user is in the `video` group, re-login |
| Local LLM (mesh-llm) unreachable | Falls back to Gemini, then Claude automatically — check `LOCAL_LLM_URL` / that mesh-llm is running |
| Gemini 400 error | Key wrong/not yet active — wait a few min after creating |
| Game ear disabled | Fine — runs without audio. Set GAME_AUDIO_INDEX later |
| Gamepad not in joy.cpl | Wait 30 s; try another USB port; check lib folder copied |
