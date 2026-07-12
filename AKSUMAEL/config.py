# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Configuration                      ║
# ╚══════════════════════════════════════════════════════╝

# ── Vision Provider ───────────────────────────────────────────
# "gemini" = free tier, ~1500 req/day
# "claude" = paid, one-line swap
import os
VISION_PROVIDER   = "gemini"
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")   # aistudio.google.com/app/apikey
GEMINI_MODEL      = "gemini-2.5-flash"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL      = "claude-haiku-4-5-20251001"   # used for inventory reads only


# ── Agent Loop ────────────────────────────────────────────────
LOOP_INTERVAL_SEC  = 0.5   # seconds between ticks — faster loop for responsive mining
YOLO_EVERY_N_TICKS = 1     # run YOLO every tick
KEY_HOLD_MS  = 500   # ms to hold each key press (was hardcoded 20ms)
MINE_HOLD_MS = 450   # ms to hold left-click per mining tick (fills most of LOOP_INTERVAL)

LLM_EVERY_N_TICKS  = 30    # call LLM every 30 ticks (~15s); only in EXPLORE/EAT
LOOK_SENSITIVITY   = 15    # pixels per "look left/right" action (tune as needed)

# ── Scan / Identify / Pathfinder ──────────────────────────────
LOOK_SCAN_STEP       = 80   # px per sweep position — wide arc, fast environmental scan
LOOK_AIM_STEP        = 20   # px for fine threat zoom-in / targeting
SCAN_COOLDOWN_TICKS  = 60   # min ticks between scan runs (~30s)
SCAN_MAX_THREATS     = 3    # max threats to zoom+identify per scan (keeps it fast)
SCAN_LOG_DIR         = "data/scan_log"  # where identified threat frames are saved

# If Claude's own observation/action text mentions "diamond" but YOLO's
# detector produced no diamond_ore box that tick, force a mine action
# instead of trusting the (missing) detection. Set False to disable.
VISION_SKILL_OVERRIDE = True

GAME_CONTEXT = """
You are AKSUMAEL, a Minecraft AI. MASTER GOAL: kill the Ender Dragon to beat the game.
Current phase and mechanics are injected in the history block below.

Respond with JSON only:
{
  "observation": "one sentence — what you see",
  "action": "what you are doing",
  "key": "w/a/s/d/space/ctrl/e/f/1/2/3/4/5/6/7/8/9/esc or null",
  "click": "left/right or null",
  "look": {"dx": 0, "dy": 0} or null,
  "goal": "short phrase — current objective (REQUIRED)",
  "confidence": 0.0-1.0,
  "discovery": "optional — one new fact you just learned (e.g. 'coal seam at cave entrance', 'zombies burned at dawn')"
}
look: dx=-turn left, dx=+turn right, dy=-look up, dy=+look down.
discovery: only include when you observe something genuinely new and useful. Omit otherwise.

Tactical rules:
- Log/tree visible → chop (w + left-click) in WOOD/STONE phases
- Ore visible → aim crosshair (look), approach (w), mine (left-click)
- Cave opening → explore forward
- Items on ground → walk over (w)
- Lava/fall/mob → retreat (s or a/d)
- Stuck → turn (look dx) and try new direction
- Crafting table/furnace/chest → right-click to open
- Sprint across open ground (ctrl+w)
- Hotbar: 1=sword 2=pickaxe 3=axe — select the right tool first
- Night in the open → find shelter or pillar up 6 blocks
- NEVER idle — always move toward the current phase milestone
"""

# ── Vision Source ─────────────────────────────────────────────
# Rybozen HDMI capture card via USB (the only vision source)
CAMERA_INDEX = 2     # -1 = auto-detect, or set 0/1/2 explicitly
                     # Run: v4l2-ctl --list-devices
                     # /dev/video2 = "USB3.0 Video" capture node (video3 is metadata-only)

# ── Action Output ─────────────────────────────────────────────
# "kb2040" = UART → KB2040 → USB HID keyboard+mouse+gamepad (primary)
# "ch9329" = UART → CH9329 → USB HID keyboard+mouse (backup, PC only)
# "print"  = dry-run, prints actions to console
ACTION_OUTPUT = "kb2040"   # ← change to "kb2040" once wired

# ── UART ──────────────────────────────────────────────────────
UART_PORT = "/dev/ttyUSB0"   # FTDI FT232RL USB-TTL adapter (GND/TX/RX to KB2040)
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
GAME_AUDIO_INDEX = 4

# ── YOLO / Labeling ───────────────────────────────────────────
YOLO_MODEL          = "data/models/aksumael_mc.pt"
YOLO_CONF_THRESHOLD = 0.25
YOLO_LABEL_DB       = "data/yolo_labels.json"

# ── YOLO Fine-Tuning ──────────────────────────────────────────
COLLECT_FRAMES         = False   # disabled — survey behavior handles collection now
SURVEY_CONF_THRESH     = 0.45    # trigger survey when avg YOLO conf below this
SURVEY_UNKNOWN_TRIGGER = True    # trigger survey when unknown objects detected
SURVEY_LLM_CONF_THRESH = 0.45    # trigger survey when Claude reports low confidence
SURVEY_FRAMES_PER_SWEEP = 3      # number of frames to save per survey (from diff angles)
SURVEY_COOLDOWN_SEC    = 8.0     # min seconds between surveys

# ── Auto-Training ─────────────────────────────────────────────
AUTO_TRAIN_AFTER_FRAMES = 50    # trigger retraining after this many new survey frames
AUTO_TRAIN_MIN_TOTAL    = 30    # minimum total dataset size before any training
AUTO_TRAIN_COOLDOWN_SEC = 3600  # don't retrain more than once per hour

# ── Reward ────────────────────────────────────────────────────
REWARD_DECAY = 0.95

# ── Day/Night Cycle (approximate — real MC time isn't readable from
#    video, this just gives Claude a sense of time passing) ────
MC_DAY_TICKS       = 24000   # Minecraft day cycle
DAYTIME_SAFE_RANGE = (0, 13000)   # ticks 0-13000 are daylight

# ── Tool Durability ───────────────────────────────────────────
PICKAXE_DURABILITY = 200   # uses before warning Claude to craft/switch tools

# ── F3 Debug Screen OCR ────────────────────────────────────────
F3_OCR_EVERY_N_TICKS = 30    # opportunistically OCR the F3 overlay this often
F3_READ_EVERY_N_TICKS = 300  # periodically open/close F3 ourselves (~10 min);
                             # 0 disables the auto-toggle (opportunistic-only)
F3_KEY_WAIT_TICKS = 12       # ticks to wait after pressing F3 before OCR (12 * 0.2 = 2.4s)

# ── Paths ─────────────────────────────────────────────────────
SKILLS_DIR = "data/skills"
REWARD_LOG = "data/reward_log.json"
