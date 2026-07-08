# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Configuration                      ║
# ╚══════════════════════════════════════════════════════╝

# ── Vision Provider ───────────────────────────────────────────
# "gemini" = free tier, ~1500 req/day
# "claude" = paid, one-line swap
import os
VISION_PROVIDER   = "claude"
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")   # aistudio.google.com/app/apikey
GEMINI_MODEL      = "gemini-2.5-flash"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL      = "claude-sonnet-4-20250514"


# ── Agent Loop ────────────────────────────────────────────────
LOOP_INTERVAL_SEC  = 2.0   # seconds between ticks (safe under free Gemini quota)
YOLO_EVERY_N_TICKS = 1     # run YOLO every tick
LLM_EVERY_N_TICKS  = 5     # call Gemini every 3 ticks

# ── Game Context ──────────────────────────────────────────────
GAME_CONTEXT = """
You are AKSUMAEL, an AI agent playing Minecraft in survival mode.
Analyse the screenshot and decide ONE action. Respond with JSON only:
{
  "observation": "one sentence describing what you see",
  "action": "what you are doing",
  "key": "w/a/s/d/space/e/1/2/esc or null",
  "click": null,
  "confidence": 0.0-1.0
}
Priority: danger → back away (s). Trees nearby → approach (w).
Items on ground → interact (e). Safe → explore (w). Unsure → wait (null).
"""

# ── Vision Source ─────────────────────────────────────────────
# Rybozen HDMI capture card via USB (the only vision source)
CAMERA_INDEX = -1    # -1 = auto-detect, or set 0/1/2 explicitly
                     # Run: v4l2-ctl --list-devices

# ── Action Output ─────────────────────────────────────────────
# "kb2040" = UART → KB2040 → USB HID keyboard+mouse+gamepad (primary)
# "ch9329" = UART → CH9329 → USB HID keyboard+mouse (backup, PC only)
# "print"  = dry-run, prints actions to console
ACTION_OUTPUT = "kb2040"   # ← change to "kb2040" once wired

# ── UART ──────────────────────────────────────────────────────
UART_PORT = "/dev/ttyAMA3"   # Pi hardware UART (GPIO 14/15)
UART_BAUD = 115200

# ── Platform Target ───────────────────────────────────────────
# "pc"    = desktop/laptop via USB HID
# "ps3"   = PS3 via KB2040 gamepad profile
# "xbox"  = Xbox Series X via KB2040 → Brook XB3
PLATFORM_TARGET = "pc"

# ── Controller Input ──────────────────────────────────────────
ENABLE_EVDEV   = True    # real controller plugged into Pi
ENABLE_I2C_JOY = True    # mini I2C joystick fallback (0x5A)
I2C_JOY_ADDR   = 0x5A
I2C_BUS        = 1
I2C_DEADZONE   = 15

# Blend mode: "aksumael_only" | "human_only" | "assist" | "blend"
BLEND_MODE = "aksumael_only"

# ── Audio ─────────────────────────────────────────────────────
ENABLE_TTS      = False    # AKSUMAEL speaks (pyttsx3, no mic needed)
ENABLE_GAME_EAR = True    # AKSUMAEL hears game audio (graceful if no device)

# TTS: "pyttsx3" (offline) | "elevenlabs" (cloud)
TTS_ENGINE         = "pyttsx3"
ELEVENLABS_API_KEY = "YOUR_ELEVENLABS_KEY_HERE"
ELEVENLABS_VOICE   = "Rachel"

# Game audio device index.
# -1 = auto (first available input device)
# Set to the ALSA card number of the Rybozen capture card audio output
# Run: arecord -l  — look for "USB3.0 Audio" and use its card number
GAME_AUDIO_INDEX = 3

# ── YOLO / Labeling ───────────────────────────────────────────
YOLO_MODEL          = "data/models/aksumael_mc.pt"
YOLO_CONF_THRESHOLD = 0.5
YOLO_LABEL_DB       = "data/yolo_labels.json"

# ── YOLO Fine-Tuning ──────────────────────────────────────────
COLLECT_FRAMES = False   # set True to save labeled frames during play

# ── Reward ────────────────────────────────────────────────────
REWARD_DECAY = 0.95

# ── Paths ─────────────────────────────────────────────────────
SKILLS_DIR = "data/skills"
REWARD_LOG = "data/reward_log.json"
