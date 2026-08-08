# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Configuration                      ║
# ╚══════════════════════════════════════════════════════╝

# ── Deployment identity ───────────────────────────────────────
# Which physical box this checkout is running on. Static by design: the
# point is for an operator (or core/claude_bridge.py's /state, which
# returns this) to confirm they're talking to the instance they think
# they are, so it has to be a value someone deliberately set rather than
# something probed at runtime and therefore always "correct".
#
# victus-t7 = HP Victus laptop booted off the Samsung T7 external SSD
# (the T7 *is* the boot drive — / is /dev/sda2 on "PSSD T7", it is not a
# secondary data disk). Clone this repo onto a different machine and this
# value should change with it.
NODE_NAME = "victus-t7"

# Expected hardware for NODE_NAME, for display/confirmation only — nothing
# below is enforced, and the runtime is free to land somewhere else (the
# capture card in particular falls back to /dev/video0 when video2 is
# absent, see CAMERA_FALLBACK_INDICES). Compare against the live values in
# /state's hardware_status to spot a mis-seated or missing device.
# GPU string verified against nvidia-smi 2026-08-08 — this is a 6GB RTX
# 4050 Laptop GPU, not the RTX 2060S it's occasionally called.
NODE_HARDWARE = {
    "machine":      "HP Victus laptop",
    "boot_drive":   "Samsung T7 external SSD (/dev/sda2)",
    "gpu":          "NVIDIA GeForce RTX 4050 Laptop GPU (6GB)",
    "capture_card": "/dev/video2",   # mirrors CAMERA_INDEX below
    "uart":         "/dev/ttyUSB0",  # mirrors UART_PORT below (KB2040 via FT232RL)
}

# ── Vision Provider ───────────────────────────────────────────
# Platform: HP Victus (RTX 4050 Laptop GPU) + Samsung T7 SSD, robocar-hub @ 192.168.1.156
# All LLM calls in the codebase go through core/llm_router.route_llm_call()
# (or the try_claude()/call_claude_direct() aliases, which now route to the
# same local tier). AKSUMAEL runs fully offline — every call, gameplay or
# training/labeling, goes to the local mesh-llm server. There is no
# automatic or manual fallback to Gemini or Claude; GEMINI_API_KEY and
# ANTHROPIC_API_KEY below are read but unused by the router.
import os
VISION_PROVIDER = "local"

# Single source of truth for LLM routing policy. "local" is the only
# supported value — kept as a flag (rather than hardcoding the choice
# inline everywhere) so a future cloud tier could be re-added without
# touching every call site, but core/llm_router.py currently ignores
# anything other than "local" and always calls mesh-llm.
INFERENCE_BACKEND = "local"

LOCAL_LLM_URL     = "http://localhost:9337/v1"
LOCAL_LLM_MODEL   = "auto"
LOCAL_LLM_TIMEOUT = 40      # seconds — live 2026-07-19 measurement shows this model's
                            # vision calls actually taking 30-38s (slower than the
                            # ~11-15s this was originally tuned for), not a hang;
                            # cutting this to 20s made every real vision call fail
                            # ("all LLM tiers failed") instead of just running less
                            # often. Tick latency is controlled by calling this less
                            # frequently (see LLM_MIN_TICK_GAP / LLM_EVERY_N_TICKS in
                            # core/runtime.py), not by starving the model of time to
                            # answer — 8s was cutting them off before a response ever
                            # came back, and 20s turned out to do the same thing.
LOCAL_LLM_ENABLED = True

# Inventory reader uses local-only per-slot crop classification (see
# behaviors/inventory_reader.py). Each slot is extracted as a 32×32 crop
# and composed into a small grid image for a single local Qwen call,
# avoiding the full-GUI screenshot that triggers bounding-box detection mode.
INVENTORY_READER_ENABLED = True

GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")   # aistudio.google.com/app/apikey
GEMINI_MODEL      = "gemini-2.0-flash"   # current free-tier model
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
ACTIVE_ENV = "training"
AVAILABLE_ENVS = ["minecraft", "fallout76", "driving", "robocar"]

# ── Environment Profile (game/OS-agnostic bootstrap) ─────────────
# Opt-in: when True, core/runtime.py calls core/env_detector.py at startup
# to capture a frame, ask mesh-llm what environment it's looking at, and
# load/create the matching core/env_profile.py profile for the session —
# its yolo_model_path overrides YOLO_MODEL below and its yolo_classes drive
# core/skill_transfer.py's active-skill filter. False (default) skips all
# of that and behaves exactly as before this existed: YOLO_MODEL as set
# below, SkillSystem unfiltered.
ENV_PROFILE_ENABLED = False

# Requires ENV_PROFILE_ENABLED. Feeds YOLO's below-threshold detections
# into core/label_queue.py for autonomous mesh-llm labeling + retraining,
# scoped to the active env_profile's env_id.
LABEL_QUEUE_ENABLED       = False
LABEL_QUEUE_EVERY_N_TICKS = 20    # how often to run a labeling pass (LLM round-trips — off the hot path)


# ── Agent Loop ────────────────────────────────────────────────
LOOP_INTERVAL_SEC  = 0.25  # seconds between ticks — faster loop for responsive mining
YOLO_EVERY_N_TICKS = 1     # run YOLO every tick
KEY_HOLD_MS  = 500   # ms to hold each key press (was hardcoded 20ms)
MINE_HOLD_MS = 450   # ms to hold left-click per mining tick (fills most of LOOP_INTERVAL)

# ── FSM state enable flags ────────────────────────────────────────────────────
ENABLE_EAT   = False   # allow EAT state (hunger recovery)
ENABLE_HUNT  = False   # allow HUNT state (find food)
ENABLE_MINE  = True    # allow MINE state (ore mining + tree chopping via left-click)
ENABLE_CHOP  = True    # allow CHOP state (tree chopping)
ENABLE_LEARN      = False   # disabled — YOLO-World does zero-shot detection, no orbit data collection needed
ENABLE_AUTO_RETRAIN = False  # disabled — no retraining needed with YOLO-World

LLM_EVERY_N_TICKS      = 30    # call LLM every 30 ticks (~15s) while in EXPLORE/EAT
LLM_EVERY_N_TICKS_MINE = 60    # slower cadence while actively MINE-ing/chopping —
                                # the FSM already drives per-tick aim+click, so the
                                # LLM only needs to check in occasionally (cheaper, faster loop)
LLM_MIN_TICK_GAP = 5    # floor on ticks between LLM calls even for a forced
                        # "reconsider" (skill/goal mismatch) — without this, a
                        # mismatch that re-detects every tick (e.g. an
                        # off-goal skill still in view) forced an LLM call on
                        # every single tick instead of respecting any cadence
                        # (37-55s ticks observed 2026-07-19). Bypassed when
                        # the FSM state just changed or the goal stack is
                        # empty, so a genuinely new situation isn't delayed.
LOOK_SENSITIVITY   = 15    # pixels per "look left/right" action (tune as needed)

# ── Scan / Identify / Pathfinder ──────────────────────────────
LOOK_SCAN_STEP       = 80   # px per sweep position — wide arc, fast environmental scan
LOOK_AIM_STEP        = 20   # px for fine threat zoom-in / targeting
SCAN_COOLDOWN_TICKS  = 8    # min ticks between scan runs (~4s)
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
  "gamepad": {"lx": 0, "ly": 0, "rx": 0, "ry": 0, "buttons": 0, "lt": 0, "rt": 0},
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

# ── Display / GUI ─────────────────────────────────────────────
# The Victus rig has its own laptop screen (DISPLAY=:0), so the live
# YOLO-overlay/labeling window renders there. Set False only when running
# genuinely headless (no GTK/Qt/Cocoa backend available) to skip the
# namedWindow/imshow/waitKey calls entirely instead of attempting-then-
# catching a guaranteed failure on every restart.
ENABLE_DISPLAY_UI = True

# ── Vision Source ─────────────────────────────────────────────
# Rybozen HDMI capture card via USB (the only vision source)
CAMERA_INDEX = 2     # -1 = auto-detect, or set 0/1/2 explicitly
                     # Run: v4l2-ctl --list-devices
                     # /dev/video2 = "USB3.0 Video" capture node (video3 is metadata-only)

# Tried in order after CAMERA_INDEX itself fails to open or delivers no real
# frame. The capture card can vanish entirely (unplugged, or renumbered after
# a power outage — 2026-08-08), and a laptop webcam on /dev/video0 is a far
# better vision source than none at all: YOLO/the FSM keep running, they just
# see the room instead of the game. Set to [] to disable fallback and keep
# CAMERA_INDEX as the only acceptable device.
CAMERA_FALLBACK_INDICES = [0, 1]

# A candidate device must open AND hand back a frame whose mean pixel value
# clears this before it's accepted. Guards against a device that opens fine
# but only ever produces black (capture card with no HDMI source attached) —
# taking it would look like working vision while feeding the FSM nothing.
# vision/color_detector.py uses the same 20.0 floor for no-signal frames.
CAMERA_MIN_FRAME_MEAN = 8.0

# Re-probe cadence once AKSUMAEL has fallen back to vision-less mode, and how
# often it's allowed to say so in the log (the probe itself is quiet in
# between). Vision is restored automatically the moment a device comes back.
CAMERA_REPROBE_SEC   = 300   # 5 min between probe sweeps
CAMERA_LOG_THROTTLE_SEC = 60 # at most one "no camera" line per minute

# ── Action Output ─────────────────────────────────────────────
# "kb2040" = UART → KB2040 → USB HID keyboard+mouse+gamepad (primary)
# "ch9329" = UART → CH9329 → USB HID keyboard+mouse (backup, PC only)
# "print"  = dry-run, prints actions to console
ACTION_OUTPUT = "kb2040"   # ← change to "kb2040" once wired

# ── KB2040 role ───────────────────────────────────────────────
# What the KB2040 is flashed as right now. This is about the firmware on
# the board, not about what AKSUMAEL would like it to be — get it wrong
# and the host talks a protocol the board doesn't speak.
#
#   "hid"    = rp2040/code.py       — HID keyboard/mouse/gamepad, binary
#              0xAA 0xBB packets. Minecraft and everything else that
#              drives a game. Host side: uart/kb2040_packer.py.
#   "bridge" = uart/kb2040_bridge.py — general hardware I/O (GPIO, PWM,
#              ADC, UART, I2C, SPI), newline-delimited JSON. Host side:
#              uart/bridge_client.py.
#
# In "bridge" mode ActionExecutor skips the HID packer entirely: there is
# no game to drive, so game actions are logged rather than sent, and
# executor.bridge holds a live BridgeClient for hardware work.
# Set back to "hid" (and reflash rp2040/code.py) to play again.
KB2040_MODE = "bridge"     # "hid" | "bridge"

# Serial port for the bridge. None = auto-detect: every /dev/ttyACM*
# and /dev/ttyUSB* is pinged and the one that answers "kb2040-bridge"
# wins. Worth setting by hand only if two boards are attached — the
# KB2040 presents two ACM nodes (REPL console + CDC data endpoint) and
# only the data one answers.
BRIDGE_PORT = None
BRIDGE_BAUD = 115200

# ── UART ──────────────────────────────────────────────────────
# Preferred port, not a hard requirement: when this node doesn't exist,
# uart/kb2040_packer.py asks core/hardware_detector.find_kb2040_port() to
# write-probe every /dev/ttyUSB*/ttyACM* and binds to the first that takes
# a frame (FTDI/CP210x/CH340/RP2040 VIDs preferred). The adapter renumbers
# to ttyUSB1 after some replugs, and a KB2040 driven over its own USB CDC
# port enumerates as /dev/ttyACM* — both used to dead-end in print-mode.
# Set UART_AUTODETECT = False to bind to UART_PORT only.
UART_PORT = "/dev/ttyUSB0"   # FTDI FT232RL USB-TTL adapter (GND/TX/RX to KB2040)
UART_BAUD = 115200
UART_AUTODETECT = True

# The probe is a *write* probe, not a handshake: the link is one-way
# (host → FTDI → KB2040 RX) and rp2040/code.py never writes back, so
# "responds" can only mean "opened and accepted a release-all frame".
# A second serial gadget plugged in while the KB2040 is absent can
# therefore be selected — pin UART_AUTODETECT = False if that rig ever
# has one.

# ── Platform Target ───────────────────────────────────────────
# "pc"    = desktop/laptop via USB HID
# "ps3"   = PS3 via KB2040 gamepad profile
# "xbox"  = Xbox Series X via KB2040 → Brook XB3
PLATFORM_TARGET = "pc"

# ── Controller Input ──────────────────────────────────────────
ENABLE_EVDEV   = True    # real controller plugged into the laptop
ENABLE_I2C_JOY = False   # mini I2C joystick module no longer physically connected
I2C_JOY_ADDR   = 0x5A
I2C_BUS        = 1
I2C_DEADZONE   = 15

# Blend mode: "aksumael_only" | "human_only" | "assist" | "blend"
BLEND_MODE = "human_only"

# ── Audio ─────────────────────────────────────────────────────
ENABLE_TTS      = True      # AKSUMAEL speaks (pyttsx3, no mic needed)

# ── SmolVLA policy (driving sim / robocar) ──────────────────────────
ENABLE_SMOLVLA           = False
SMOLVLA_MODEL_PATH       = "/home/ros/models/smolvla"  # local cache, confirmed 2026-07-22
SMOLVLA_DEVICE           = "cuda"
SMOLVLA_MIN_FREE_VRAM_MB = 900
SMOLVLA_GOALS            = {"find_and_chop_tree", "explore", "find_food"}
ENABLE_GAME_EAR = True    # AKSUMAEL hears game audio (graceful if no device)

# TTS: "pyttsx3" (offline) | "elevenlabs" (cloud)
TTS_ENGINE         = "pyttsx3"
ELEVENLABS_API_KEY = "YOUR_ELEVENLABS_KEY_HERE"
ELEVENLABS_VOICE   = "Rachel"

# Game audio device index.
# Use 12 ("default") to let PipeWire route the stream.
# The wrapper sets PIPEWIRE_NODE='USB3.0 Video Analog Stereo' so PipeWire
# connects this process's recording to the Rybozen capture card audio,
# not the laptop mic.
# Run: wpctl status — look for "USB3.0 Video Analog Stereo" to confirm.
GAME_AUDIO_INDEX = 12

# ── YOLO / Labeling ───────────────────────────────────────────
# YOLO_USE_WORLD = True  →  use YOLO-World zero-shot text-query detection (no training needed).
# YOLO_USE_WORLD = False →  use the trained aksumael_mc.pt weights (legacy path).
YOLO_USE_WORLD      = False  # CPU ONNX fallback needs this False; fine-tuned aksumael_mc.pt used instead of YOLO-World
YOLO_WORLD_MODEL    = "yolov8s-world.pt"   # downloads automatically on first run (~50 MB)
YOLO_WORLD_CLASSES  = [
    # Entities — discrete objects, YOLO-World handles well
    "cow", "sheep", "pig", "chicken",
    "zombie", "skeleton", "spider", "creeper", "enderman",
    # Ores — small distinct blocks embedded in stone faces
    "coal ore", "iron ore", "gold ore", "diamond ore", "emerald ore", "redstone ore",
    # Wood — log columns visible in forests; "oak log" matches Minecraft terminology
    # better than "wood log" in CLIP embedding space (confirmed 2026-07-29: "wood log"
    # failed to fire on oak logs, which were instead mis-detected as "bed" at conf 0.20
    # due to the wooden grain texture on the top face)
    "oak log", "birch log",
    # Structures / items — discrete placed objects
    # NOTE: "bed" removed — its wooden texture false-fires on oak log tops at conf~0.20;
    # add back only if sleep goal is actively needed and with a tighter threshold
    "chest", "crafting table", "furnace",
    # NOTE: background terrain classes REMOVED ("grass block", "dirt", "sand",
    # "gravel", "stone", "water", "lava", "tree leaves") — YOLO-World fires
    # full-frame detections for these since the whole scene matches them.
    # Use color_detector.py for terrain/water signals instead.
]
YOLO_MODEL          = "data/models/aksumael_mc.pt"   # used only when YOLO_USE_WORLD=False
# Also passed as conf= directly to the model call (vision/yolo.py) — that's
# what actually controls which boxes Ultralytics returns at all; it's not
# just a post-hoc filter here (confirmed still wired up as of 2026-07-15).
# Lowered 0.25 -> 0.15 -> 0.10. NOTE: this does NOT fix tree detection —
# direct testing (both current and pre-retrain-backup weights, several
# tree-filled frames, down to conf=0.001) found zero log/leaves detections
# at any confidence: the training set has zero labeled examples of either
# class (0/4227 label files), so there's no learned signal to threshold
# into view regardless of this value. Left low anyway since it may still
# help borderline detections on classes that *are* trained (ore, mobs,
# HUD). Tree detection is instead covered by the color-based fallback in
# vision/color_detector.py ('log'/'leaves'/'birch_log' entries) until the
# model gets real tree training data. Also means the below-threshold
# 'unknown' labeling queue in vision/yolo.py will rarely trigger, since
# nothing under this value reaches it either.
YOLO_CONF_THRESHOLD = 0.10
YOLO_LABEL_DB       = "data/yolo_labels.json"
# Skip a detect() call entirely when free VRAM drops below this. The GPU is
# shared with a standalone llama-server process (local vision route) that
# permanently holds ~4GB of the 6GB card, leaving little headroom — running
# a CUDA inference call into that headroom risked a driver-level abort
# (SIGABRT, no Python traceback, crashed the whole process) rather than a
# catchable OOM error. Skipping ahead of time is cheaper than recovering
# after the fact (2026-07-18).
YOLO_MIN_FREE_VRAM_MB = 200  # steady-state free is ~310-350MB; model weights already resident in VRAM; inference working mem <100MB; SIGABRT at <10MB free

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
# Darkness is judged from the live captured frame (avg_brightness =
# frame.mean()), not the synthetic game_tick counter — that counter is
# real uptime mod MC_DAY_TICKS (see the Day/Night Cycle comment above)
# and drifts from the actual in-game clock over a long session. Flip to
# False to disable night-shelter behavior entirely (e.g. to test
# mining/chopping without it preempting every tick) — re-enable before
# any unattended/long-running session.
NIGHT_SURVIVAL_ENABLED = False
NIGHT_BRIGHTNESS_DARK = 35     # avg frame brightness (0-255) below which it's night/dark
NIGHT_BRIGHTNESS_DAWN = 60     # avg frame brightness above which it's day again
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

# ── Voice (core/voice.py) ─────────────────────────────────────
# Local, offline voice control — Whisper STT + piper TTS + F9 push-to-talk,
# running as a daemon thread of the *main* process (started from
# core/runtime.py). This was axon.service until 2026-08-08; it was folded in
# because two processes on one box were fighting over the mic and speaker
# (Axon's Whisper/piper vs the runtime's audio/game_ear.py and audio/tts.py)
# and ALSA hands those out first-come-first-served. See core/voice.py's
# module docstring.
#
# Whisper runs on-device (no cloud STT); the only network call is the routed
# LLM fallback for free-form commands the rule-based fast path doesn't
# recognize. Commands land in the same data/injected_goals.json queue the
# mastermind hive uses (see memory.goals.GoalStack.check_injected_goals), so
# no separate polling path is needed in the tick loop.
VOICE_ENABLED       = True   # False disables the thread entirely
VOICE_WHISPER_MODEL = "base"  # tiny/base/small — bigger is slower but more accurate

# piper (offline neural TTS) is tried first for a JARVIS-like confident
# British voice, falling back to pyttsx3/espeak if piper or its voice model
# isn't available. Re-fetch a missing model with:
#   venv/bin/python3 -m piper.download_voices <name> --data-dir data/piper_voices
VOICE_PIPER_VOICE_DIR = "data/piper_voices"
VOICE_PIPER_VOICE     = "en_GB-alan-medium"   # alt: en_GB-northern_english_male-medium

# Output level for spoken replies, 0.0-1.0. piper normalizes each utterance to
# full scale first (SynthesisConfig.normalize_audio defaults True), so this is
# an absolute amplitude, not a multiplier on whatever the voice model happened
# to emit — 1.0 means "peak the full range" every time.
#
# 1.0 is what Axon effectively used (it never passed a volume, and piper's
# default is 1.0), so this is not a regression from the axon.service era.
# Measured 2026-08-08: piper at 1.0 → peak 1.000 / rms 0.202, at 0.4 → peak
# 0.400 / rms 0.079. If speech is still too quiet, the knob that actually
# helps is the PipeWire sink, which applies on top and was sitting at 0.60:
#   wpctl set-volume @DEFAULT_AUDIO_SINK@ 1.0
# Turn THIS down only if AKSUMAEL is too loud relative to game audio.
VOICE_TTS_VOLUME = 1.0

# ── Voice activity detection (always-on listening) ─────────────
# Default mode is "on": the mic stays open and a VAD cuts it into utterances
# at natural pauses, so talking to the bot needs no key press. Override per
# box by writing "ptt" or "off" to data/voice_mode.txt (read live, no
# restart). These knobs only apply to "on".
#
# webrtcvad aggressiveness, 0-3: how eagerly non-speech is filtered. 0 lets
# through nearly anything; 3 is aggressive enough to drop quiet or distant
# speech along with the noise. 2 holds up with game audio on the same
# speakers. Ignored when webrtcvad isn't installed (energy fallback).
VOICE_VAD_AGGRESSIVENESS = 2

# Quiet needed to declare an utterance finished. Too low and a mid-sentence
# breath cuts the sentence in two; too high and every command waits on it.
VOICE_VAD_SILENCE_SEC = 0.8

# Utterances shorter than this are discarded unheard — a cough, a chair, a
# door. Below roughly 0.4s there isn't enough audio for whisper to do
# anything but hallucinate a caption.
VOICE_VAD_MIN_SPEECH_SEC = 0.4

# Hard cap on one utterance, so continuous background speech (a video
# playing, someone on a call) can't grow a buffer forever without whisper
# ever being handed anything.
VOICE_VAD_MAX_UTTERANCE_SEC = 15.0

# Audio kept from just before the VAD triggers. It takes ~240ms of voiced
# frames to decide speech started, and without this the first word is
# already clipped by the time the buffer opens.
VOICE_VAD_PREROLL_SEC = 0.3

# Energy-threshold last resort (RMS, 0.0-1.0). Reached only on a box that has
# neither webrtcvad nor a keyboard hook (pynput/keyboard) — with webrtcvad
# missing but a hook present, core/voice.py picks push-to-talk over always-on
# instead, because an RMS gate can't tell a voice from a fan and game audio on
# the same speakers would trip it continuously. Raise if the room floor keeps
# triggering it; lower if quiet speech is missed.
VOICE_VAD_ENERGY_THRESHOLD = 0.08

# Seconds to suppress mic input after the bot finishes speaking. sd.wait()
# returns once playback ends but room acoustics keep the mic hot for ~1s;
# without this window the bot transcribes its own echo and loops.
VOICE_POST_SPEAK_SUPPRESS_SEC = 1.5

# Legacy aliases — axon/ is still on disk (hub.py, speaker.py, command_parser.py)
# but no longer runs as a service. Kept pointing at the VOICE_* values so the
# two can't drift while it's being retired; delete with the axon/ directory.
AXON_WHISPER_MODEL   = VOICE_WHISPER_MODEL
AXON_PIPER_VOICE_DIR = VOICE_PIPER_VOICE_DIR
AXON_PIPER_VOICE     = VOICE_PIPER_VOICE

# ── Audio device selection (audio/device_probe.py) ─────────────
# None = auto-detect at startup by name keyword priority (Victus > USB
# Audio > HDMI Audio > default), so the same config works unmodified
# whichever box AKSUMAEL is running on (T7 "Aksumael" has no mic/speaker;
# the HP Victus and the Z490 desktop build do). Set to an int sounddevice
# index to force a specific device instead.
AUDIO_INPUT_DEVICE  = None
AUDIO_OUTPUT_DEVICE = None

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
# (cheap haiku call, ~50 tokens) instead of every tick. Wall-clock gated
# (not tick-count gated) because tick duration swings wildly — a MINE/idle
# tick is ~0.5s but a tick that triggers a vision-LLM call can take 30-40s
# (see data/live.log) — a tick-count gate made the black-strip caption in
# ui/labeling.py look "stuck" for minutes at a time whenever the loop hit
# a run of slow ticks (2026-07-19).
MONOLOGUE_EVERY_N_SECONDS = 8

# Episode memory — rolling window persisted to data/memory/episodes.jsonl
EPISODE_MEMORY_MAX        = 200
EPISODE_RETRIEVE_TOP_K    = 3

# Code skills — LLM-generated Python functions as a more robust alternative
# to recorded key-sequence skills. Executing LLM-generated code carries real
# risk even on a single-user local rig; sandboxed via forbidden-pattern regex
# (blocks import/exec/eval/os./sys./subprocess/socket/dunder-attrs), restricted
# builtins, and a thread timeout — see core/code_skill_generator.py. Review
# generated skills in data/skills/code/ periodically.
ENABLE_CODE_SKILLS        = True
CODE_SKILLS_DIR            = "data/skills/code"

# ── Neural Policy (PPO backbone) ───────────────────────────────
# Low-level learned policy that blends with the rule-based (skill/FSM/LLM)
# decision — see core/neural_policy.py, core/policy_blender.py. Off by
# default: opt in once a checkpoint has actually been trained on this rig.
NEURAL_POLICY_ENABLED = False
NEURAL_POLICY_PATH    = "data/models/neural_policy.pt"
RL_TRAIN_EVERY_N_TICKS = 1000

# ── LeRobotDataset episode recording ────────────────────────────
# Passive (observation, action) capture for offline behavioral cloning
# (ACT, Diffusion Policy) / RL fine-tuning — see core/lerobot_recorder.py.
# Purely additive: writes to LEROBOT_DATA_DIR alongside the existing tick
# loop and never influences what AKSUMAEL actually does in-game.
ENABLE_LEROBOT_RECORDING  = True
LEROBOT_DATA_DIR          = "data/lerobot"
LEROBOT_MAX_EPISODE_TICKS = 500

# ── Mastermind Hive Coordinator ────────────────────────────────
# Opt-in: set MASTERMIND_ENABLED=True and point MASTERMIND_HOST at the
# machine running mastermind/coordinator.py (see tools/start_mastermind.sh)
# to have this instance join the hive — publish status/observations over
# MQTT and accept goal assignments. Safe to leave True with no broker
# reachable; core/runtime.py degrades to solo mode if the connection fails.
MASTERMIND_ENABLED = False
MASTERMIND_HOST    = "127.0.0.1"
MASTERMIND_PORT    = 1883
MASTERMIND_AGENT_ID = None   # None = auto-generate from hostname

# ── Telegram channel ───────────────────────────────────────────
# Scott texts the bot; the inner monologue answers (see
# core/telegram_channel.py). Safe to leave True with no token
# configured — the channel logs a warning and no-ops every method.
TELEGRAM_CHANNEL_ENABLED = True

# Token is read from $TELEGRAM_BOT_TOKEN first, then this file. Never
# commit it — the path is outside the repo for exactly that reason.
TELEGRAM_TOKEN_FILE = os.path.expanduser("~/.config/telegram/token")

# A Telegram bot is reachable by anyone who knows its @name. Leave None
# to accept the first chat that messages it (the id is printed on every
# inbound message); set to a list of ints to lock it to Scott's chat.
TELEGRAM_ALLOWED_CHAT_IDS = None

# Where replies go before Scott has ever messaged the bot. None = wait
# for an inbound message to learn the chat id.
TELEGRAM_CHAT_ID = None

# ── Honcho persistent memory ───────────────────────────────────
# Self-hosted Honcho (Postgres + pgvector + deriver) reached over HTTP
# with the Apache-2.0 honcho-ai SDK. Server setup, working config.toml
# and the three blockers are in docs/HONCHO_SPIKE.md. Requires three
# extra processes plus Postgres; degrades to no-op if any are down.
HONCHO_CONTEXT_ENABLED = True
HONCHO_URL             = "http://localhost:8000"
HONCHO_WORKSPACE       = "aksumael"
HONCHO_TIMEOUT         = 10.0    # seconds per SDK call; no SDK-level retries

# context() is a Postgres+pgvector round-trip whose freshness depends on
# a deriver that occupies mesh-llm for 2-7s per derivation — the same
# single llama.cpp instance the vision loop needs. Cache aggressively;
# never call it per tick.
HONCHO_CONTEXT_REFRESH_SECONDS = 30.0
HONCHO_CONTEXT_MAX_TOKENS      = 600   # mesh-llm n_ctx is only 4096

# Writes are the expensive direction: every message handed to Honcho is
# work the deriver will do on mesh-llm. The monologue fires every
# MONOLOGUE_EVERY_N_SECONDS (8s) — persisting all of those would keep the
# deriver saturated, so routine thoughts are throttled to this. Thoughts
# that answer a Telegram message bypass the throttle.
HONCHO_WRITE_EVERY_N_SECONDS = 120
