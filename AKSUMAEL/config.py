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
CLAUDE_MODEL      = "claude-sonnet-5"


# ── Agent Loop ────────────────────────────────────────────────
LOOP_INTERVAL_SEC  = 0.5   # seconds between ticks — faster loop for responsive mining
YOLO_EVERY_N_TICKS = 1     # run YOLO every tick
KEY_HOLD_MS  = 500   # ms to hold each key press (was hardcoded 20ms)
MINE_HOLD_MS = 450   # ms to hold left-click per mining tick (fills most of LOOP_INTERVAL)

LLM_EVERY_N_TICKS  = 15    # call Claude every 15 ticks (~30s); only in EXPLORE/EAT
LOOK_SENSITIVITY   = 15    # pixels per "look left/right" action (tune as needed)

# If Claude's own observation/action text mentions "diamond" but YOLO's
# detector produced no diamond_ore box that tick, force a mine action
# instead of trusting the (missing) detection. Set False to disable.
VISION_SKILL_OVERRIDE = True

GAME_CONTEXT = """
You are AKSUMAEL, an AI agent playing Minecraft in survival mode.
Analyse the screenshot and decide ONE action. Respond with JSON only:
{
  "observation": "one sentence describing what you see",
  "action": "what you are doing",
  "key": "w/a/s/d/space/ctrl/e/f/1/2/3/4/5/6/7/8/9/esc or null",
  "click": "left/right/null",
  "look": {"dx": -15, "dy": 0},
  "goal": "optional short natural-language goal",
  "confidence": 0.0-1.0
}
"look" pans the camera: dx=-turn left, dx=+turn right, dy=-look up, dy=+look down; null to skip.
You may include a "goal" field with a short natural-language description of what you are trying to achieve (e.g. "go deeper to find diamonds", "flee from danger"). This helps with planning.
Be bold and decisive. Rules:
- Ores or resources visible → approach and mine (w + left click)
- Ore visible but off-center → use look to aim crosshair at it before mining
- Open cave/passage → explore forward (w)
- Dark area → light it up (place torch if you have one)
- Items on ground → pick up (w to walk over)
- Danger/lava/fall → retreat (s or a/d)
- Stuck or unsure → try a different direction (a or d)
- Chests, furnaces, doors, beds → right-click to open/use (click: "right_click")
- Need to cover ground fast → hold ctrl while moving (ctrl+w) to sprint
- Hotbar slots are assumed: 1=sword, 2=pickaxe, 3=axe — select before mining or fighting
- Iron/gold/lapis ore visible → approach and mine (w + left click), same as other ores
- Log (tree trunk) visible → chop for wood (w + left click); leaves can be ignored or broken for saplings
- Zombie or skeleton visible → fight if healthy and armed (left click), flee (s or a/d) if low health
- Spider visible → fight at range or retreat if surrounded
- Crafting table visible → right-click to open crafting menu
- Grass → safe to walk through, no special action needed
- At night (is_daytime=False), prioritize finding shelter or a bed rather than mining
Never return null unless there is actual danger. Always be moving or acting.
Y-level guide: diamonds spawn below Y=16, coal Y<40, iron Y<60. Ask Claude to go deeper when seeking diamonds.
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
F3_KEY_WAIT_TICKS = 2        # ticks to wait after pressing F3 before OCR

# ── Paths ─────────────────────────────────────────────────────
SKILLS_DIR = "data/skills"
REWARD_LOG = "data/reward_log.json"
