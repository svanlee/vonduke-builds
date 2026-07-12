# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Configuration                      ║
# ╚══════════════════════════════════════════════════════╝

# ── Vision Provider ───────────────────────────────────────────
# Platform: HP Victus (RTX 4050 Laptop GPU) + Samsung T7 SSD, robocar-hub @ 192.168.1.156
# "gemini" = free tier, ~1500 req/day
# "claude" = paid, one-line swap
import os
VISION_PROVIDER = "gemini"
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")   # aistudio.google.com/app/apikey
GEMINI_MODEL      = "gemini-2.5-flash"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL        = "claude-haiku-4-5-20251001"   # inventory/chest reads (cheap)
CLAUDE_VISION_MODEL = "claude-haiku-4-5-20251001"             # main gameplay decisions (smarter)


# ── Active Environment ──────────────────────────────────────────
# Selects which core/environment.py adapter core/env_registry.py hands back.
# Changing this has NO effect on core/runtime.py, which still drives
# Minecraft directly against ActionExecutor/YOLODetector/etc. — it only
# matters to code that goes through core/env_registry.create_adapter().
# Per-environment class list / action space / reward weights live in
# data/envs/<name>.yaml so they can be tuned without touching code.
ACTIVE_ENV     = "minecraft"
AVAILABLE_ENVS = ["minecraft", "fallout76", "driving", "robocar"]


# ── Agent Loop ────────────────────────────────────────────────
LOOP_INTERVAL_SEC  = 0.25  # seconds between ticks — faster loop for responsive mining
YOLO_EVERY_N_TICKS = 1     # run YOLO every tick
KEY_HOLD_MS  = 500   # ms to hold each key press (was hardcoded 20ms)
MINE_HOLD_MS = 450   # ms to hold left-click per mining tick (fills most of LOOP_INTERVAL)

LLM_EVERY_N_TICKS      = 30    # call LLM every 30 ticks (~15s) while in EXPLORE/EAT
LLM_EVERY_N_TICKS_MINE = 60    # slower cadence while actively MINE-ing/chopping —
                                # the FSM already drives per-tick aim+click, so the
                                # LLM only needs to check in occasionally (cheaper, faster loop)
LOOK_SENSITIVITY   = 15    # pixels per "look left/right" action (tune as needed)

# ── Scan / Identify / Pathfinder ──────────────────────────────
LOOK_SCAN_STEP       = 80   # px per sweep position — wide arc, fast environmental scan
LOOK_AIM_STEP        = 20   # px for fine threat zoom-in / targeting
SCAN_COOLDOWN_TICKS  = 30   # min ticks between scan runs (~15s)
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

# ── Phase-Specific Tactical Guidance ────────────────────────────
# Explicit, imperative per-phase instructions appended to GAME_CONTEXT on
# every LLM call (via config.game_context_for_phase). Written as a numbered
# checklist, not open-ended choices, so the LLM isn't left to improvise —
# and each one ends with an urgency line to stop it from lingering/exploring
# aimlessly once the phase objective is achievable.
PHASE_TACTICS = {
    "wood": (
        "PHASE TACTICS (WOOD):\n"
        "1. Find the nearest tree. Chop it until you have 12+ logs.\n"
        "2. Craft: logs -> planks -> crafting table -> sticks -> wooden pickaxe.\n"
        "3. The instant you have a pickaxe, find stone and mine it immediately.\n"
        "URGENCY: complete this phase's objective in under 50 moves. Do not wander or idle."
    ),
    "stone": (
        "PHASE TACTICS (STONE):\n"
        "1. Mine 20+ cobblestone with your pickaxe.\n"
        "2. Craft stone pickaxe, stone sword, and a furnace.\n"
        "3. Dig/tunnel down to find iron ore (Y=15-50). Mine 8+ iron ore.\n"
        "4. Smelt the iron ore the moment you have a furnace and fuel (coal/charcoal).\n"
        "URGENCY: complete this phase's objective in under 50 moves. Do not linger on the surface."
    ),
    "iron": (
        "PHASE TACTICS (IRON):\n"
        "1. Smelt iron ore into iron ingots (furnace + fuel).\n"
        "2. Craft iron pickaxe, iron sword, iron armor.\n"
        "3. With the iron pickaxe equipped, dig below Y=16 toward diamond-bearing strata.\n"
        "4. The moment you see diamond_ore, mine it — do not walk past it.\n"
        "URGENCY: complete this phase's objective in under 50 moves. Prioritize depth over exploration."
    ),
    "diamond": (
        "PHASE TACTICS (DIAMOND):\n"
        "1. Mine diamonds until you have 3+, then craft a diamond pickaxe immediately.\n"
        "2. Mine 10+ obsidian near lava using the diamond pickaxe.\n"
        "3. Craft flint and steel.\n"
        "4. Build a 4-wide x 5-tall obsidian portal frame and light it.\n"
        "URGENCY: complete this phase's objective in under 50 moves. Do not stockpile — build the portal."
    ),
    "nether": (
        "PHASE TACTICS (NETHER):\n"
        "1. Move cautiously — avoid lava, ghast fireballs, and open ledges.\n"
        "2. Find a Nether Fortress. Kill blazes for 6+ blaze rods.\n"
        "3. Kill Endermen for 12+ ender pearls.\n"
        "4. Craft Eyes of Ender, then return to the Overworld portal.\n"
        "URGENCY: complete this phase's objective in under 50 moves. Retreat from danger, don't fight everything."
    ),
    "end": (
        "PHASE TACTICS (END):\n"
        "1. Throw Eyes of Ender repeatedly to triangulate and reach the stronghold.\n"
        "2. Find the End Portal room, fill all 12 frame blocks with Eyes of Ender.\n"
        "3. Step through the portal. Destroy End Crystals on obsidian pillars first (attack from range).\n"
        "4. Attack the Ender Dragon whenever it perches or hovers over the portal.\n"
        "URGENCY: this is the final phase. Do not retreat. Kill the Ender Dragon to beat the game."
    ),
}


def game_context_for_phase(phase: str | None) -> str:
    """GAME_CONTEXT with the current phase's tactical checklist appended.

    Falls back to plain GAME_CONTEXT when phase is unknown/None (e.g. the
    threat-scan LLM call, which doesn't need phase tactics)."""
    tactics = PHASE_TACTICS.get(phase)
    if not tactics:
        return GAME_CONTEXT
    return f"{GAME_CONTEXT}\n{tactics}"

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
ENABLE_EVDEV   = True    # real controller plugged into the laptop
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

# ── Night Survival ────────────────────────────────────────────
NIGHT_APPROACH_TICK  = 11000   # start sheltering once game_tick passes this (dusk warning)
PILLAR_HEIGHT        = 6       # blocks to pillar up when caught in the open at night
BLOCK_SLOT           = '4'     # hotbar slot assumed to hold building blocks (cobblestone/dirt)
NIGHT_MAX_WAIT_TICKS = 3000    # safety cap on waiting out the night (~12.5 min at 0.25s/tick)

# ── Torch Placement ───────────────────────────────────────────
TORCH_SLOT         = '7'    # hotbar slot assumed to hold torches
TORCH_DARK_Y_LEVEL = 50     # below this Y-level, treat surroundings as cave/dark
TORCH_COOLDOWN_SEC = 30.0   # min seconds between automatic torch placements

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
MEMORY_DIR = "data/memory"   # episodes, retired_goals, skill_evolution logs

# ── Self-Improving Loop (JARVIS-1+ architecture) ───────────────
# Goal retirement — how long (ticks) an unachievable goal is allowed to
# stay active before the goal stack gives up on it.
GOAL_MAX_AGE_TICKS       = 200   # ~50s at LOOP_INTERVAL_SEC=0.25
GOAL_MAX_AGE_SURVIVE     = None  # 'survive'-class goals never retire

# Curriculum generator — how often (ticks) to ask the LLM what to attempt
# next, and only when the goal stack looks idle (current goal == explore).
CURRICULUM_INTERVAL_TICKS = 300  # ~75s

# Skill evolution pass — proven/blacklist marking + duplicate merging.
SKILL_EVOLVE_TICKS       = 1000  # ~250s
SKILL_PROVEN_USES        = 5     # success_count threshold to mark 'proven'
SKILL_BLACKLIST_FAILURES = 3     # failed_count threshold to blacklist

# Inner monologue — real LLM-generated thought, gated to fire this often
# (cheap haiku call, ~50 tokens) instead of every tick.
MONOLOGUE_EVERY_N_TICKS  = 50

# Episode memory — rolling window persisted to data/memory/episodes.jsonl
EPISODE_MEMORY_MAX        = 200
EPISODE_RETRIEVE_TOP_K    = 3

# Code skills — LLM-generated Python functions as a more robust alternative
# to recorded key-sequence skills. Executing LLM-generated code carries real
# risk even on a single-user local rig, so this is OFF by default; flip to
# True only after reviewing generated skills in data/skills/code/.
ENABLE_CODE_SKILLS        = False
CODE_SKILLS_DIR            = "data/skills/code"
