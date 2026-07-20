# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Gameplay Finite State Machine      ║
# ╚══════════════════════════════════════════════════════╝
#
# States
# ──────
#   EXPLORE   default; walk forward + pan camera to find targets
#   APPROACH  walk toward a detected target while aiming camera at it
#   MINE      hold left-click on a target block, re-aim every tick
#                (also used for chopping trees — same left-click mechanic)
#   COMBAT    flee from or attack a detected hostile mob
#   INTERACT  right-click an interactable block (chest, crafting, etc.)
#   EAT       hunger below threshold — delegate to HungerBehavior, idle here
#   COLLECT   walk forward briefly to pick up dropped items after mining
#   FISH      equip rod → right-click to cast → wait → right-click to reel in
#   HUNT      approach passive mob → left-click to kill → COLLECT drops
#   FARM      approach crop → left-click harvest → right-click to replant
#
# Priority order (highest first):
#   hunger → hostile mob → ore (high value) → tree (need wood)
#   → animal (need food) → fish → farm → EXPLORE
#
# Usage
# ──────
#   fsm = GameFSM()
#   state, action_dict = fsm.tick(objects, world_mem, hunger_frac)
#
# The returned action_dict has the same keys as the runtime action_dict
# (key, click, look, gamepad, observation, action, confidence) and can be
# merged or used directly.

import math
import enum
import time
import config
from core.aim import bbox_to_mouse_delta, is_on_target
from vision.color_detector import sample_box_pixel_count
from vision.target_lock import TargetLock


# ── Target label sets ─────────────────────────────────────────────────────────

MINE_TARGETS = {
    # High-value ores
    'diamond_ore', 'emerald_ore', 'coal_ore', 'iron_ore', 'gold_ore',
    'lapis_ore', 'redstone_ore', 'copper_ore', 'deepslate_diamond_ore',
    'deepslate_iron_ore', 'deepslate_gold_ore', 'deepslate_coal_ore',
    'nether_quartz_ore', 'nether_gold_ore', 'ancient_debris',
    # Wood / logs — same left-click mechanic as mining
    'oak_log', 'spruce_log', 'birch_log', 'jungle_log', 'acacia_log',
    'dark_oak_log', 'mangrove_log', 'cherry_log', 'wood', 'log', 'tree',
    # General stone (lower priority, good for tunnelling)
    'stone', 'cobblestone', 'gravel',
    # NOTE: 'leaves' intentionally excluded — leaves are a navigation proxy
    # (TREE_TARGETS), not a mine target. Mining leaves wastes time and doesn't
    # yield wood; the FSM should keep approaching until a log is visible.
}

# High-value ore subset — preferred over tree-chopping in priority
ORE_TARGETS = {
    'diamond_ore', 'emerald_ore', 'coal_ore', 'iron_ore', 'gold_ore',
    'lapis_ore', 'redstone_ore', 'copper_ore', 'deepslate_diamond_ore',
    'deepslate_iron_ore', 'deepslate_gold_ore', 'deepslate_coal_ore',
    'nether_quartz_ore', 'nether_gold_ore', 'ancient_debris',
}

# Priority order: higher index = lower priority; diamond always wins over emerald
ORE_PRIORITY = [
    'diamond_ore', 'deepslate_diamond_ore', 'ancient_debris',
    'emerald_ore',
    'gold_ore', 'deepslate_gold_ore', 'nether_gold_ore',
    'iron_ore', 'deepslate_iron_ore',
    'lapis_ore', 'redstone_ore', 'copper_ore',
    'deepslate_coal_ore', 'coal_ore', 'nether_quartz_ore',
]

TREE_TARGETS = {
    'log', 'oak_log', 'spruce_log', 'birch_log', 'jungle_log',
    'acacia_log', 'dark_oak_log', 'mangrove_log', 'cherry_log', 'wood', 'tree',
    'leaves',  # color-fallback detector reports leaves when YOLO can't see
               # logs at all (see vision/color_detector.py) — without this,
               # EXPLORE's tree_obj dispatch (below) never fires on a
               # leaves-only detection and the FSM gets stuck in EXPLORE
               # forever (2026-07-17).
}

PASSIVE_MOBS = {
    'cow', 'sheep', 'pig', 'chicken',
}

FISH_TARGETS = {'water'}

CROP_TARGETS = {'wheat', 'carrot', 'potato', 'melon', 'pumpkin', 'sugar_cane'}

INTERACT_TARGETS = {
    'chest', 'furnace', 'crafting_table', 'workbench', 'barrel',
    'enchanting_table', 'anvil', 'smithing_table', 'blast_furnace',
    'smoker', 'door', 'trapdoor', 'fence_gate', 'gate',
}

HOSTILE_MOBS = {
    'zombie', 'skeleton', 'creeper', 'spider', 'enderman', 'witch',
    'phantom', 'drowned', 'husk', 'stray', 'cave_spider', 'slime',
    'magma_cube', 'blaze', 'ghast', 'piglin', 'hoglin', 'wither_skeleton',
    'mob', 'enemy',
}

# ── FLEE (2026-07-18) ───────────────────────────────────────────────────
# Pre-empts everything (even COMBAT's plain back-away) when a hostile is
# genuinely dangerous: health is low, or a creeper is close enough to blow
# up. Sprints backward for FLEE_TICKS ticks (~2s at LOOP_INTERVAL_SEC=0.25),
# then hands control back to whatever state/goal was active before the
# scare — see GameFSM._flee_return_state.
FLEE_TICKS              = 8      # ~2s at config.LOOP_INTERVAL_SEC=0.25
LOW_HEALTH_FRAC         = 0.50   # health_pct below this counts as "health < 10/20"
CREEPER_CLOSE_BBOX_AREA = 8000   # px^2 — creeper bbox this large is close enough to detonate

# ── Mob persistence / ambient-mob filter (2026-07-18) ──────────────────────
# A hostile label seen this many consecutive ticks without health_pct ever
# dropping more than MOB_HEALTH_DROP_THRESHOLD since it first appeared is
# treated as ambient (harmless/stuck/false-positive) and excluded from
# Priority 2 COMBAT routing — see the mob-tracking block in tick(). A gap of
# MOB_ABSENT_TICKS_TO_RESET+ ticks with no detection clears the streak, so a
# mob that leaves and comes back starts the clock over as a fresh sighting.
MOB_AMBIENT_TICKS          = 15
MOB_HEALTH_DROP_THRESHOLD  = 0.05
MOB_ABSENT_TICKS_TO_RESET  = 3

# ── State watchdog (2026-07-18) ─────────────────────────────────────────────
# Safety net for any state (not just COMBAT) that stalls without the normal
# per-state give-up logic ever tripping. Forces EXPLORE after this many
# consecutive ticks in the same state with no sign of progress (a new mine
# target, a position change, or reward above WATCHDOG_REWARD_THRESH).
WATCHDOG_TICKS         = 90
WATCHDOG_REWARD_THRESH = 0.15


def _bbox_area(box) -> float:
    if not box or len(box) != 4:
        return 0.0
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


# Minimum YOLO confidence to act on a detection
MIN_CONF = 0.50

# Once already MINEing an ore, accept a much lower confidence before giving
# up on it — a partially-broken block's texture confuses YOLO and its conf
# score drops well below MIN_CONF even though the ore is still there.
MINE_HOLD_CONF = 0.30

# Ore must be fully undetected (not just low-confidence) for this many
# consecutive ticks before we treat it as broken and move to COLLECT.
MINE_ABSENT_TICKS_TO_BREAK = 3

# Direct pixel-cluster break check for color-sourced MINE targets (see
# vision.color_detector.sample_box_pixel_count). Re-samples the LOCKED
# target's own last-known bbox region every tick — a same-labeled block
# elsewhere in frame can't mask a real break here the way waiting for
# detect_ores_by_color() to stop reporting the label anywhere at all can.
# Requires a reasonably solid baseline sample (COLOR_BREAK_MIN_BASELINE_PX)
# before trusting the ratio, so a target that starts out barely qualifying
# for its own min_px threshold doesn't trip a false break on ordinary pixel
# jitter between ticks.
COLOR_BREAK_MIN_BASELINE_PX = 40
COLOR_BREAK_FRACTION        = 0.25   # cluster shrunk below this fraction of baseline = broken

# Ore labels permanently excluded from targeting regardless of blacklist state.
# emerald_ore only generates in mountain biomes — we're not in one, so every
# YOLO detection of it is a false positive. Hard-blocked here (not just the
# blacklist) so the FSM never re-targets it even after blacklist expiry.
_UNSUPPORTED_ORE_LABELS = frozenset({'emerald_ore'})

# Container/interactable blocks that must NEVER be a MINE target, regardless
# of source (YOLO or color-proxy) or confidence. These belong in
# INTERACT_TARGETS only (right-click, not break). Hard-blocked here as
# defense-in-depth — never let one reach ore_obj/tree_obj/mine_obj selection
# even if a future edit accidentally adds one to MINE_TARGETS.
# (2026-07-18: bot destroyed a chest — root cause traced to
# vision/color_detector.py's 'log' HSV range matching chest wood color and
# mislabeling it as a log; see color_detector.py note near ORE_COLOR_RANGES.)
PROTECTED_BLOCKS = frozenset({
    'chest', 'trapped_chest', 'ender_chest', 'barrel', 'shulker_box',
})

# Color-proxy detections (vision/color_detector.py, source == 'color') have no
# shape/texture discrimination — they're an HSV blob match, and wood-colored
# containers (chests) can fall inside the 'log' HSV window and get mislabeled.
# Restrict what a color-sourced detection may ever become a MINE target as,
# to just the tree labels the color detector exists to cover. This disables
# color-based ore targeting (diamond/gold/redstone/emerald via color) as a
# deliberate side effect of this safety fix — YOLO-sourced ore detection is
# unaffected.
SAFE_COLOR_MINE_LABELS = frozenset({'log', 'leaves', 'birch_log'})


# ── Tuning ────────────────────────────────────────────────────────────────────

APPROACH_TICKS   = 8     # ticks in APPROACH before switching to MINE
MINE_MAX_TICKS   = 60    # give up and re-approach after this many MINE ticks (60×0.5s=30s)
COLLECT_TICKS    = 4     # walk forward this long after block breaks
EXPLORE_WALK     = 12    # ticks walking before each sector-scan sweep

# ── EAT (2026-07-20) ─────────────────────────────────────────────────────
# Inventory tracking is unreliable (inv=[unknown] — see run notes), so EAT
# can't look up which hotbar slot actually holds food. Instead it blind-
# cycles every slot: tap the number key to select it, hold right-click long
# enough for one Minecraft eat animation, then check whether hunger_frac
# actually rose. Unchanged hunger means that slot wasn't food (or was
# empty) — advance to the next slot. A rise means it worked — keep eating
# from that same slot (another right-click hold) until hunger clears the
# threshold.
EAT_HOLD_TICKS      = 6     # ~1.5s at LOOP_INTERVAL_SEC=0.25 — one eat animation
EAT_HOTBAR_SLOTS    = 9     # try every slot before giving up
EAT_HUNGER_EPSILON  = 0.02  # hunger_frac must rise more than this (OCR jitter) to count as "ate"

# When EAT exhausts every hotbar slot with no food, it hands off to HUNT so
# the bot can go kill something to eat. hunger_frac stays below the EAT
# threshold the whole time HUNT is chasing the animal, so without this grace
# window Priority 1 below (hunger) forces state right back to EAT on the very
# next tick — HUNT never even runs _do_hunt() once (2026-07-20 deadlock).
HUNT_GRACE_TICKS    = 30    # ticks HUNT is shielded from the hunger override

# ── HUNT / TargetLock (2026-07-20) ──────────────────────────────────────
# Kill-confirmation proxy for TargetLock's "animal confirmed dead" exit —
# a raw-meat/leather/feather drop entering this tick's detections means the
# mob just died, so HUNT can head to COLLECT immediately instead of waiting
# out TargetLock's full miss-count dropout. NOTE: none of these labels are
# in the current YOLO class list (data/yolo_dataset/classes.txt) yet, so
# this check is a no-op until the model is retrained to detect item drops —
# the miss-count dropout (TargetLock.is_locked) is what actually ends HUNT
# today. Left in place as the intended hook rather than omitted, since
# retraining to add these classes doesn't require any FSM change once done.
HUNT_KILL_LABELS = frozenset({
    'raw_beef', 'beef', 'raw_porkchop', 'porkchop', 'raw_chicken', 'chicken_raw',
    'raw_mutton', 'mutton', 'leather', 'feather', 'wool',
})

# ── Sector-scan EXPLORE sweep ────────────────────────────────────────────
# Instead of a blind left/right pan, divide the horizontal FOV into discrete
# sectors and dwell in each one long enough for vision to actually register
# whatever's there, sweeping the full arc in one direction then reversing
# next time. A target found mid-sweep pre-empts this entirely — tick()
# checks ore/tree/animal/interact/etc. targets before ever calling
# _do_explore(), so "lock on immediately" falls out of the existing
# priority order for free.
EXPLORE_SECTORS      = 5   # far-left, left, center, right, far-right
EXPLORE_SECTOR_DWELL = 8   # ticks to hold aim in each sector before advancing
EXPLORE_SECTOR_STEP  = 3   # multiplier on config.LOOK_SENSITIVITY per sector hop
_SECTOR_NAMES = ['far-left', 'left', 'center', 'right', 'far-right']

# Color-based detection (vision/color_detector.py) can't tell crafted oak
# planks/wood blocks apart from real logs — they share nearly identical HSV.
# Within this many blocks of spawn/base (where the player's own structure
# lives), color detections are untrustworthy; only real YOLO detections are
# allowed to trigger APPROACH/MINE there. (2026-07-18: bot mined through its
# own fort wall after color-detecting planks as 'log'.)
BASE_EXCLUSION_RADIUS = 30

# APPROACH stall guard: if a target's bbox isn't growing (i.e. we're not
# actually closing distance — likely snagged on a ledge, root, or other small
# terrain lip), try jumping in place for a few ticks before giving up
# entirely. Give up only after STALL_TICKS_TO_ABORT non-progressing ticks
# instead of sliding into MINE against whatever is blocking us. Must be
# < APPROACH_TICKS to actually pre-empt the MINE transition.
STALL_TICKS_TO_JUMP   = 4
STALL_TICKS_TO_ABORT  = 12
STALL_AREA_GROWTH_MIN = 1.05   # bbox area must grow at least 5% to count as progress

# Color-only detections don't participate in the bbox-growth stall guard
# (their box stays roughly fixed-size regardless of real distance), so they
# get a flat tick budget instead — long enough to physically walk up to a
# nearby tree even if MINE never triggers (e.g. stuck on a leaves lock).
APPROACH_COLOR_TICKS_TO_ABORT = 60

# APPROACH target lock: once we've locked onto a log mid-approach, per-tick
# re-evaluation must not flip the target to leaves just because the log's
# confidence dipped below MIN_CONF for a frame or two (2026-07-18: leaves
# routinely out-scores a thin/flickering log bbox and briefly wins the
# _pick_tree_target() race even though a log is right there). Only release
# the lock if the log is completely undetected for this many consecutive
# ticks, or a different log clearly outscores the locked one.
APPROACH_LOG_LOSS_TICKS    = 3     # consecutive fully-absent ticks before releasing the lock
APPROACH_LOG_SWITCH_MARGIN = 0.15  # a different log must beat the locked one by this much conf to steal the lock

# ── No-dig-straight-down guard (2026-07-18) ────────────────────────────────
# AKSUMAEL has no raycast / world-space (x, y, z) for a YOLO mine target —
# only a 2-D pixel bbox, plus a periodic F3 OCR fix for the player's OWN
# position (world_mem.pos_x/pos_z/y_level, refreshed roughly every 300
# ticks, not every tick). There's no live target (x, y, z) to diff against
# the player the way a world-aware bot would. The closest available proxy
# for "the block about to be mined is directly beneath the player" is: the
# bbox sitting dead-centre horizontally and low in frame (straight down,
# not off to a side) while the camera is already pitched steeply downward
# (cumulative_pitch_dy — see _dampen_pitch_dy). Ore/tree targets are exempt
# — legitimately reaching those sometimes requires looking down at a ledge.
STRAIGHT_DOWN_PITCH_MIN = 80      # cumulative_pitch_dy above this = looking steeply down
STRAIGHT_DOWN_X_FRAC    = 0.15    # bbox horizontal centre must be within this of frame centre
STRAIGHT_DOWN_Y_FRAC    = 0.70    # bbox vertical centre must be below this fraction of frame height
STRAIGHT_DOWN_EXEMPT    = ORE_TARGETS | TREE_TARGETS

# Fall detection: an F3 Y read that dropped more than this since the
# previous F3 read means we just fell into a hole/shaft/lava pocket. Jump
# for a couple of ticks to try to climb back out, pre-empting whatever
# state the FSM was in (set as world_mem.fall_detected by
# memory/world_memory.py's update_f3()).
FALL_JUMP_TICKS = 2

# ── rebuild_fort (2026-07-19) ───────────────────────────────────────────
# Fallback-scope implementation of the injectable rebuild_fort goal: this
# bot has no live "expected vs actual block" comparison (that needs a real
# block-presence read, which doesn't exist here) or true 3D aim, so instead
# of a full HTN wall-repair it navigates to the remembered fort/base point
# and blind-places a fixed build plan around it — floor, then a 2-high
# perimeter wall, then one chest — relying on Minecraft's own placement
# rule (a block always lands adjacent to whatever face the crosshair is
# over) to spread the pattern out from repeated "look, click" ticks, the
# same trick the original 8-cell floor ring used.
FORT_APPROACH_DIST  = 2.0   # stop navigating once within this many blocks
FORT_HALF_SIZE       = 2   # 5x5 footprint: offsets run -2..+2 on each axis
_FORT_RANGE          = range(-FORT_HALF_SIZE, FORT_HALF_SIZE + 1)
FORT_FLOOR_OFFSETS  = [(dx, dz) for dx in _FORT_RANGE for dz in _FORT_RANGE
                       if not (dx == 0 and dz == 0)]   # 24 cells, centre excluded (player stands there)
# Perimeter of the 5x5 footprint — the cells actually on the outer edge —
# each placed at two heights to make a 2-block-high wall.
FORT_WALL_OFFSETS   = [(dx, dz) for dx in _FORT_RANGE for dz in _FORT_RANGE
                       if abs(dx) == FORT_HALF_SIZE or abs(dz) == FORT_HALF_SIZE]
FORT_WALL_LAYERS    = (0, 1)   # two placement passes per perimeter cell
# Chest sits one cell in from a corner — inside the walls, reachable from centre.
FORT_CHEST_OFFSET   = (FORT_HALF_SIZE - 1, FORT_HALF_SIZE - 1)
# Hotbar slots assumed to hold each material. The FSM has no live
# hotbar-slot read (only behaviors/inventory_reader.py does, and it's a
# runtime-level behavior, not available inside core/fsm.py) — hardcoded
# per the rebuild_fort fallback spec; adjust if materials sit elsewhere.
FORT_FLOOR_SLOT  = '3'   # dirt/cobblestone
FORT_WALL_SLOT   = '4'   # planks/logs
FORT_CHEST_SLOT  = '5'   # chest

# Full sequential build plan: (phase, ox, oz, layer, hotbar_slot). Consumed
# one entry per tick by _do_rebuild_fort via self._fort_place_index.
FORT_BUILD_PLAN = (
    [('floor', ox, oz, 0, FORT_FLOOR_SLOT) for ox, oz in FORT_FLOOR_OFFSETS]
    + [('wall', ox, oz, layer, FORT_WALL_SLOT)
       for layer in FORT_WALL_LAYERS for ox, oz in FORT_WALL_OFFSETS]
    + [('chest', FORT_CHEST_OFFSET[0], FORT_CHEST_OFFSET[1], 0, FORT_CHEST_SLOT)]
)


# ── State enum ────────────────────────────────────────────────────────────────

class State(enum.Enum):
    EXPLORE  = 'EXPLORE'
    APPROACH = 'APPROACH'
    MINE     = 'MINE'
    COMBAT   = 'COMBAT'
    INTERACT = 'INTERACT'
    EAT      = 'EAT'
    FLEE     = 'FLEE'
    COLLECT  = 'COLLECT'
    FISH     = 'FISH'
    HUNT     = 'HUNT'
    FARM     = 'FARM'


# ── Helpers ───────────────────────────────────────────────────────────────────

def _idle() -> dict:
    return {
        'observation': '', 'action': 'wait',
        'key':   None, 'click': None,
        'look':  None, 'gamepad': None,
        'confidence': 0.0,
    }


def _pick_best(objects: list, label_set: set, min_conf: float = MIN_CONF):
    """Return the highest-confidence detection whose label is in label_set."""
    hits = [
        o for o in objects
        if o.get('label', '').lower() in label_set
        and o.get('conf', 0.0) >= min_conf
    ]
    return max(hits, key=lambda o: o.get('conf', 0.0)) if hits else None


def _pick_by_label(objects: list, label: str, min_conf: float = MIN_CONF):
    """Return the highest-confidence detection matching an exact label."""
    hits = [
        o for o in objects
        if o.get('label', '').lower() == label
        and o.get('conf', 0.0) >= min_conf
    ]
    return max(hits, key=lambda o: o.get('conf', 0.0)) if hits else None


def _pick_best_ore(objects: list, min_conf: float = MIN_CONF):
    """
    Pick the highest-priority ore detection.
    Uses ORE_PRIORITY order so diamond always beats emerald even if
    emerald has higher YOLO confidence.
    Falls back to highest-conf ore if label not in ORE_PRIORITY.
    """
    hits = {
        o.get('label', '').lower(): o
        for o in objects
        if o.get('label', '').lower() in ORE_TARGETS
        and o.get('label', '').lower() not in _UNSUPPORTED_ORE_LABELS
        and o.get('conf', 0.0) >= min_conf
    }
    if not hits:
        return None
    for label in ORE_PRIORITY:
        if label in hits:
            return hits[label]
    # fallback: highest conf
    return max(hits.values(), key=lambda o: o.get('conf', 0.0))


def _pick_tree_target(objects: list, min_conf: float = MIN_CONF):
    """Pick a tree-chopping target, preferring log bboxes over leaves, and
    real YOLO detections over color-detector ones at each tier.

    Leaves are a canopy nav-proxy only (see TREE_TARGETS comment above) —
    if ANY log-labelled detection is visible this tick, it always wins
    regardless of confidence, so EXPLORE/APPROACH never lock onto a
    higher-confidence leaves bbox while an actual log sits right next to
    it (2026-07-18: bot stalled in APPROACH(leaves) forever because leaves
    routinely out-score the thin log bbox on confidence).
    Falls back to the general TREE_TARGETS pick (which includes leaves)
    only when no log is visible at all.

    Color-detector hits (vision/color_detector.py; conf fixed at 0.75,
    source='color') are valid candidates at both tiers — otherwise EXPLORE
    never fires an APPROACH transition on a color-only sighting and the bot
    wanders in TREE-FALLBACK circles forever whenever YOLO can't see logs at
    all (2026-07-18). But a real YOLO detection is trusted more than a color
    guess, so within each tier a non-color hit always wins over a color one
    regardless of confidence.
    """
    def _area(o):
        box = o.get('box')
        if not box or len(box) != 4:
            return 0
        return max(0, box[2] - box[0]) * max(0, box[3] - box[1])

    def _best(cands):
        return max(cands, key=lambda o: (o.get('conf', 0.0), _area(o))) if cands else None

    logs = [
        o for o in objects
        if 'log' in o.get('label', '').lower()
        and o.get('conf', 0.0) >= min_conf
    ]
    if logs:
        real_logs = [o for o in logs if o.get('source') != 'color']
        return _best(real_logs) or _best(logs)

    candidates = [
        o for o in objects
        if o.get('label', '').lower() in TREE_TARGETS
        and o.get('conf', 0.0) >= min_conf
    ]
    real_candidates = [o for o in candidates if o.get('source') != 'color']
    return _best(real_candidates) or _best(candidates)


def _near_base(world_mem) -> bool:
    """True if current F3 position is within BASE_EXCLUSION_RADIUS of the
    stored spawn/base location. No position fix yet (F3 never read this
    session) counts as NOT near base — we only suppress color detections
    once we can actually confirm proximity."""
    if world_mem is None:
        return False
    px = getattr(world_mem, 'pos_x', None)
    pz = getattr(world_mem, 'pos_z', None)
    if px is None or pz is None:
        return False
    bx = getattr(world_mem, 'spawn_x', 0.0)
    bz = getattr(world_mem, 'spawn_z', 0.0)
    return ((px - bx) ** 2 + (pz - bz) ** 2) ** 0.5 <= BASE_EXCLUSION_RADIUS


def _resolve_fort_coords(world_mem):
    """Fort/base centre coordinates for the rebuild_fort goal, in priority
    order: an explicitly remembered fort_location/base_coords/home (see
    memory/world_memory.py — settable externally or read from
    data/world_memory.json if present), else the session's spawn point.
    Returns (x, y, z) or None if nothing is available."""
    if world_mem is None:
        return None
    for attr in ('fort_location', 'base_coords', 'home'):
        val = getattr(world_mem, attr, None)
        if val and len(val) == 3:
            return tuple(val)
    sx = getattr(world_mem, 'spawn_x', None)
    sz = getattr(world_mem, 'spawn_z', None)
    if sx is not None and sz is not None:
        sy = getattr(world_mem, 'spawn_y', 64)
        return (sx, sy, sz)
    return None


def _is_straight_down(box, fw: int, fh: int, world_mem) -> bool:
    """True if `box` sits at the bottom-centre of frame (i.e. roughly at the
    player's own feet) while cumulative pitch shows the camera is already
    looking steeply down — see STRAIGHT_DOWN_* constants above."""
    if not box:
        return False
    cum_pitch = getattr(world_mem, 'cumulative_pitch_dy', 0) if world_mem is not None else 0
    if cum_pitch < STRAIGHT_DOWN_PITCH_MIN:
        return False
    cx = (box[0] + box[2]) / 2.0
    cy = (box[1] + box[3]) / 2.0
    x_off = abs(cx - fw / 2.0) / (fw / 2.0)
    return x_off < STRAIGHT_DOWN_X_FRAC and cy > fh * STRAIGHT_DOWN_Y_FRAC


def _infer_frame_dims(objects: list) -> tuple:
    """
    Guess YOLO frame size from bounding-box extents.
    Falls back to (640, 360) — the small-frame pipeline default.
    """
    if not objects:
        return 640, 360
    boxes = [o.get('box') for o in objects if o.get('box') and len(o['box']) == 4]
    if not boxes:
        return 640, 360
    max_x = max(b[2] for b in boxes)
    max_y = max(b[3] for b in boxes)
    # Round up to the nearest 64-px grid
    w = max(640, int(math.ceil(max_x / 64) * 64))
    h = max(360, int(math.ceil(max_y / 64) * 64))
    return w, h


# ── FSM ───────────────────────────────────────────────────────────────────────

class GameFSM:
    """
    Tick-driven finite state machine for AKSUMAEL gameplay.

    Call tick() each iteration of the runtime loop.  Returns
    (current_state, action_dict) — the caller decides how to blend this
    with the skill system and LLM output.
    """

    def __init__(self):
        self.state        = State.EXPLORE
        self._state_ticks = 0   # ticks spent in the current state

        # MINE tracking
        self._smooth_dx         = 0
        self._smooth_dy         = 0
        self._mine_ticks        = 0
        self._mine_timeout_count = 0   # consecutive timeouts on current target
        self._mine_timeout_label = None  # label being timed out on
        self._mine_absent_ticks = 0    # consecutive ticks with NO detection at all
        self._mine_last_box     = None  # last known box, held onto through low-conf flicker
        self._mine_on_target_ticks = 0  # consecutive on-target ticks (locks aim once stable)
        # Target lock: once MINE begins, only detections of this label update
        # the aim point — prevents flip-flopping between simultaneously
        # visible labels (e.g. leaves/log/birch_log) each tick, which kept
        # aim from ever converging on one block.
        self._mine_target       = None  # {'label': str, 'box': [..]} or None
        # Ore blacklist: {label: expire_tick} — suppress re-targeting after give-up
        self._ore_blacklist      = {}
        self._ore_blacklist_strikes = {}   # {label: times blacklisted} — for exponential backoff
        self._total_ticks        = 0   # global tick counter for blacklist expiry

        # APPROACH stall tracking — detects a target bbox that isn't growing
        # (i.e. we're not actually closing distance, likely already flush
        # against a wall/obstruction)
        self._approach_stall_area  = None
        self._approach_stall_count = 0
        # Color-only targets skip the bbox-growth stall check entirely (see
        # _do_approach) since their box doesn't grow as distance closes —
        # instead they get a generous flat tick budget to physically reach
        # the target before giving up.
        self._approach_color_ticks = 0

        # APPROACH target lock (see APPROACH_LOG_LOSS_TICKS above) — holds
        # onto a locked-on log across per-tick leaves/log flicker.
        self._approach_locked_label     = None
        self._approach_locked_box       = None
        self._approach_locked_conf      = 0.0
        self._approach_log_absent_ticks = 0

        # COLLECT tracking
        self._collect_ticks = 0

        # EXPLORE sub-state: 'walk' | 'sector_scan'
        self._explore_phase        = 'walk'
        self._explore_pticks       = 0    # ticks in 'walk' phase
        self._explore_sector_idx   = 0    # 0=far-left .. EXPLORE_SECTORS-1=far-right
        self._explore_sector_dir   = 1    # sweep direction: +1 = L->R, -1 = R->L
        self._explore_sector_ticks = 0    # ticks dwelt in current sector

        # FISH tracking
        self._fish_phase     = 'cast'     # 'cast' | 'wait' | 'reel'
        self._fish_ticks     = 0

        # HUNT tracking (see vision/target_lock.py — TargetLock replaces the
        # old "predict once, gone next tick, bail" logic with a proper
        # miss-tolerant lock)
        self._hunt_ticks     = 0
        self._hunt_target    = None
        self.target_lock     = TargetLock()

        # EAT tracking (see EAT_HOLD_TICKS above)
        self._eat_slot            = 1
        self._eat_phase           = 'select'  # 'select' | 'hold_start' | 'holding' | 'check'
        self._eat_hold_ticks_left = 0
        self._eat_pre_hunger      = None
        self._hunt_grace_ticks_left = 0  # see HUNT_GRACE_TICKS above

        # FARM tracking
        self._farm_ticks     = 0

        # Direct pixel-cluster break check for color-sourced MINE targets
        # (see COLOR_BREAK_FRACTION above) — baseline pixel count sampled on
        # the first MINE tick where a frame is available for the locked target.
        self._mine_color_baseline_px = None

        # Fall-escape tracking (see FALL_JUMP_TICKS above)
        self._fall_jump_ticks = 0

        # FLEE tracking (see FLEE_TICKS above)
        self._flee_ticks_left  = 0
        self._flee_return_state = None   # State to resume once FLEE ends

        # rebuild_fort tracking
        self._fort_place_index = 0

        # Mob persistence tracking (see MOB_AMBIENT_TICKS below) — a hostile
        # label detected every tick for a long stretch without ever actually
        # damaging the player is almost certainly a stationary/harmless false
        # sighting (color-proxy noise, a mob stuck on the far side of a wall,
        # etc.), not a real ongoing threat. Without this, Priority 2 routes
        # to COMBAT every single tick and the FSM can never escape it.
        self._mob_seen_ticks: dict[str, int] = {}            # label -> consecutive ticks seen
        self._mob_health_at_first_sight: dict[str, float] = {}  # label -> health when streak started
        self._mob_absent_ticks: dict[str, int] = {}          # label -> consecutive ticks NOT seen

        # State watchdog (see WATCHDOG_TICKS below) — separate from
        # _state_ticks because some state handlers deliberately rewrite
        # _state_ticks mid-state (e.g. _do_approach holding it back for a
        # leaves target), which would corrupt a shared stuck-detector.
        self._same_state_ticks = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def tick(self, objects: list, world_mem=None, hunger_frac: float = 1.0,
             goal: str = None, frame=None, reward: float = 0.0):
        """
        Evaluate one FSM tick.

        Args:
            objects:     YOLO detection list.  Each item: {label, conf, box}.
            world_mem:   WorldMemory instance (may be None).
            hunger_frac: Hunger bar fullness 0.0–1.0 (1.0 = full).
            goal:        Active goal name (e.g. 'find_and_chop_tree'), or None.
            frame:       Current gameplay frame (BGR ndarray) or None — used
                         only by MINE's color-cluster break check (see
                         COLOR_BREAK_FRACTION above); every other code path
                         works fine without it.
            reward:      This tick's reward signal (see WATCHDOG_REWARD_THRESH
                         above) — defaults to 0.0 for callers that don't have
                         one available yet.

        Returns:
            (State, action_dict)
        """
        self._state_ticks += 1
        self._total_ticks += 1

        # ── Watchdog: force EXPLORE if stuck in one state too long ────────
        # Evaluated before every other priority (including fall/hunger) so a
        # genuinely stalled state always gets rescued regardless of what a
        # lower-priority check would otherwise decide this tick. EAT used to
        # be exempted here (HungerBehavior "owns" that state's pacing), but
        # that exemption let a broken EAT loop run all night doing nothing
        # (2026-07-19) — a stuck EAT is exactly the kind of stall this
        # watchdog exists to catch, so it's no longer special-cased.
        if reward is not None and reward > WATCHDOG_REWARD_THRESH:
            self._same_state_ticks = 0
        else:
            self._same_state_ticks += 1
        if self._same_state_ticks > WATCHDOG_TICKS:
            print(f'[WATCHDOG] state={self.state.value} stuck for '
                  f'{self._same_state_ticks} ticks → forcing EXPLORE')
            self._same_state_ticks = 0
            return self._goto(State.EXPLORE, _idle())

        fw, fh = _infer_frame_dims(objects)

        # ── Priority 0: fall detection ─────────────────────────────
        # world_mem.fall_detected is set by WorldMemory.update_f3() when a
        # fresh F3 read shows Y dropped more than FALL_Y_DROP_THRESHOLD since
        # the previous read — we just fell into a hole/shaft/lava pocket.
        # Pre-empts every other priority (including hunger/combat) for a
        # couple of ticks and just jumps, trying to climb back out.
        if world_mem is not None and getattr(world_mem, 'fall_detected', False):
            world_mem.fall_detected = False
            self._fall_jump_ticks = FALL_JUMP_TICKS
            print('[FSM] fall detected (F3 Y dropped) — jumping to escape')
        if self._fall_jump_ticks > 0:
            self._fall_jump_ticks -= 1
            ad = _idle()
            ad['key']         = 'space'
            ad['action']      = 'fall:jump_escape'
            ad['observation'] = f'Fell — jumping to escape ({FALL_JUMP_TICKS - self._fall_jump_ticks}/{FALL_JUMP_TICKS})'
            ad['confidence']  = 0.9
            return self.state, ad

        # Near base: crafted planks/wood blocks color-match logs (see
        # vision/color_detector.py) — distrust color detections here so the
        # bot doesn't APPROACH/MINE its own structure. Real YOLO detections
        # (source unset / != 'color') still pass through untouched.
        if _near_base(world_mem):
            objects = [o for o in objects if o.get('source') != 'color']

        # Expire blacklist entries
        self._ore_blacklist = {
            lbl: exp for lbl, exp in self._ore_blacklist.items()
            if exp > self._total_ticks
        }

        # ── Priority 1: hunger ────────────────────────────────────
        # Only force the transition on entry — once already in EAT, fall
        # through so lower-priority checks (hostile mob, etc.) still run
        # every tick and _do_eat() below drives the actual eat attempts.
        if hunger_frac < 0.20 and self.state != State.EAT:
            if self.state == State.HUNT and self._hunt_grace_ticks_left > 0:
                # EAT found no food and handed off to HUNT — let it actually
                # chase the animal instead of bouncing straight back to EAT.
                self._hunt_grace_ticks_left -= 1
            else:
                return self._goto(State.EAT, self._begin_eat())

        # ── Priority 2: hostile mob → flee ────────────────────────
        # Mob persistence tracking (see MOB_AMBIENT_TICKS above): a hostile
        # label detected every tick for a long stretch without health_pct
        # ever dropping counts as ambient (harmless/stuck/false-positive)
        # and is excluded from routing below — otherwise a static false
        # sighting (e.g. color-detector noise) locks the FSM in COMBAT
        # forever since Priority 2 wins every tick.
        health_pct = getattr(world_mem, 'health_pct', None) if world_mem is not None else None
        _hostile_hits = {
            o.get('label', '').lower(): o
            for o in objects
            if o.get('label', '').lower() in HOSTILE_MOBS
            and o.get('conf', 0.0) >= MIN_CONF
        }
        _ambient_labels = set()
        for label in list(self._mob_seen_ticks.keys()):
            if label not in _hostile_hits:
                self._mob_absent_ticks[label] = self._mob_absent_ticks.get(label, 0) + 1
                if self._mob_absent_ticks[label] >= MOB_ABSENT_TICKS_TO_RESET:
                    self._mob_seen_ticks.pop(label, None)
                    self._mob_health_at_first_sight.pop(label, None)
                    self._mob_absent_ticks.pop(label, None)
        for label in _hostile_hits:
            self._mob_absent_ticks[label] = 0
            if label not in self._mob_seen_ticks:
                self._mob_seen_ticks[label] = 1
                self._mob_health_at_first_sight[label] = health_pct if health_pct is not None else 1.0
            else:
                self._mob_seen_ticks[label] += 1
            first_health = self._mob_health_at_first_sight.get(label, 1.0)
            health_dropped = (health_pct is not None
                              and (first_health - health_pct) > MOB_HEALTH_DROP_THRESHOLD)
            if health_dropped:
                # Took real damage since this streak started — the mob is
                # "real" again; treat it like a fresh sighting from here.
                self._mob_seen_ticks[label] = 1
                self._mob_health_at_first_sight[label] = health_pct
            elif self._mob_seen_ticks[label] == MOB_AMBIENT_TICKS:
                print(f'[FSM] {label} seen {MOB_AMBIENT_TICKS} ticks with no '
                      f'health loss → treating as ambient, excluding from COMBAT')
                _ambient_labels.add(label)
            elif self._mob_seen_ticks[label] > MOB_AMBIENT_TICKS:
                _ambient_labels.add(label)

        _combat_candidates = [o for o in objects if o.get('label', '').lower() not in _ambient_labels]
        mob = _pick_best(_combat_candidates, HOSTILE_MOBS)
        if mob:
            low_health = health_pct is not None and health_pct < LOW_HEALTH_FRAC
            creeper_close = (mob.get('label', '').lower() == 'creeper'
                             and _bbox_area(mob.get('box')) > CREEPER_CLOSE_BBOX_AREA)
            if low_health or creeper_close:
                if self.state != State.FLEE:
                    self._flee_return_state = self.state
                self._flee_ticks_left = FLEE_TICKS
                return self._goto(State.FLEE, self._do_flee(mob)[1])
            return self._goto(State.COMBAT, self._combat_action(mob))

        # ── Gather candidate targets ───────────────────────────────
        # Filter out blacklisted ore labels and protected container blocks
        # (chest/barrel/shulker/etc.) before picking a MINE target — these
        # must only ever be reached via INTERACT_TARGETS (right-click), never
        # broken. Protected blocks are NOT stripped from `objects` itself so
        # interact_obj (picked from `objects` below) still sees them.
        non_blacklisted = [
            o for o in objects
            if o.get('label', '') not in self._ore_blacklist
            and o.get('label', '').lower() not in PROTECTED_BLOCKS
            and (o.get('source') != 'color'
                 or o.get('label', '').lower() in SAFE_COLOR_MINE_LABELS)
        ]
        # While already MINEing, accept much lower-confidence ore detections —
        # a partially-broken block's changed texture tanks YOLO's confidence
        # well below MIN_CONF even though the ore is still there to finish off.
        _ore_conf_floor = MINE_HOLD_CONF if self.state == State.MINE else MIN_CONF
        ore_obj      = _pick_best_ore(non_blacklisted, min_conf=_ore_conf_floor)
        tree_obj     = _pick_tree_target(non_blacklisted)
        if (tree_obj is not None and world_mem is not None
                and getattr(world_mem, 'pos_x', None) is not None
                and getattr(world_mem, 'pos_z', None) is not None):
            world_mem.record_tree_sighting(world_mem.pos_x, world_mem.pos_z)
        mine_obj     = ore_obj or tree_obj or _pick_best(non_blacklisted, MINE_TARGETS)
        interact_obj = _pick_best(objects, INTERACT_TARGETS)
        animal_obj   = _pick_best(objects, PASSIVE_MOBS)
        water_obj    = _pick_best(objects, FISH_TARGETS)
        crop_obj     = _pick_best(objects, CROP_TARGETS)
        bobber_obj   = _pick_best(objects, {'fishing_bobber'})

        # ── Per-state logic ───────────────────────────────────────
        s = self.state

        if s == State.EAT:
            return self._do_eat(hunger_frac, animal_obj)

        elif s == State.COLLECT:
            return self._do_collect()

        elif s == State.MINE:
            # Target-locked: while a label is locked in, only accept
            # detections of that same label — ignore other simultaneously
            # visible targets so aim can actually converge.
            if self._mine_target is not None:
                locked_obj = _pick_by_label(
                    objects, self._mine_target['label'], min_conf=MINE_HOLD_CONF)
                if locked_obj is not None:
                    self._mine_target['box'] = locked_obj.get('box', self._mine_target['box'])
                return self._do_mine(locked_obj, fw, fh, world_mem, frame)
            return self._do_mine(mine_obj, fw, fh, world_mem, frame)

        elif s == State.APPROACH:
            target = mine_obj or animal_obj
            if target:
                target = self._apply_approach_log_lock(target, non_blacklisted)
                return self._do_approach(target, fw, fh, world_mem)
            return self._goto(State.EXPLORE, _idle())

        elif s == State.INTERACT:
            if interact_obj:
                return self._do_interact(interact_obj, fw, fh)
            return self._goto(State.EXPLORE, _idle())

        elif s == State.FISH:
            return self._do_fish(bobber_obj, water_obj)

        elif s == State.HUNT:
            return self._do_hunt(objects, animal_obj, fw, fh)

        elif s == State.FARM:
            return self._do_farm(crop_obj, fw, fh)

        elif s == State.FLEE:
            return self._do_flee(mob)

        else:  # EXPLORE (default) — dispatch by priority
            if interact_obj and interact_obj.get('conf', 0) > 0.65:
                ad = self._do_interact(interact_obj, fw, fh)[1]
                return self._goto(State.INTERACT, ad)
            # Priority 3: ore (high value)
            if ore_obj:
                ad = self._do_approach(ore_obj, fw, fh, world_mem)[1]
                return self._goto(State.APPROACH, ad, world_mem)
            # Priority 4: tree (need wood)
            if tree_obj:
                ad = self._do_approach(tree_obj, fw, fh, world_mem)[1]
                return self._goto(State.APPROACH, ad, world_mem)
            # Priority 5: animal (need food)
            if animal_obj:
                return self._goto(State.HUNT, self._begin_hunt(objects, animal_obj))
            # Priority 6: fish (near water)
            if water_obj:
                return self._goto(State.FISH, self._begin_fish())
            # Priority 7: farm
            if crop_obj:
                return self._goto(State.FARM, self._begin_farm())
            return self._do_explore(world_mem, goal)

    # ── State handlers ────────────────────────────────────────────────────────

    def _do_explore(self, world_mem=None, goal: str = None):
        """Walk forward, then pan left/right, repeat — unless a tree goal
        is active and none is on screen, in which case head toward the
        nearest remembered tree location instead of scanning blind, or a
        rebuild_fort goal is active, in which case navigate to the fort and
        place blocks (see _do_rebuild_fort)."""
        if goal == 'rebuild_fort' and world_mem is not None:
            fort_ad = self._do_rebuild_fort(world_mem)
            if fort_ad is not None:
                return self.state, fort_ad
        if goal == 'find_and_chop_tree' and world_mem is not None:
            nav_ad = self._navigate_to_known_tree(world_mem)
            if nav_ad is not None:
                return self.state, nav_ad
        return self._do_explore_sweep(world_mem)

    # Cardinal facing (from F3 OCR, world_mem.facing) expressed as degrees,
    # matching the atan2(dx, -dz) convention used below (north=0, clockwise).
    _CARDINAL_DEG = {'north': 0, 'east': 90, 'south': 180, 'west': 270}

    def _navigate_to_known_tree(self, world_mem):
        """Return an action_dict biasing walk/look toward the nearest
        known_trees entry, or None if we can't (no fix, no facing, no
        remembered tree, or already close enough to let vision take over)."""
        px = getattr(world_mem, 'pos_x', None)
        pz = getattr(world_mem, 'pos_z', None)
        facing = getattr(world_mem, 'facing', None)
        cardinal_deg = self._CARDINAL_DEG.get(facing)
        if px is None or pz is None or cardinal_deg is None:
            return None
        tree = world_mem.nearest_known_tree(px, pz)
        if tree is None:
            return None
        dx, dz = tree['x'] - px, tree['z'] - pz
        if (dx * dx + dz * dz) ** 0.5 < 3.0:
            return None  # close enough — let normal detection take over

        target_bearing = math.degrees(math.atan2(dx, -dz)) % 360
        diff = (target_bearing - cardinal_deg + 180) % 360 - 180

        ad = _idle()
        ad['key'] = 'w'
        if diff > 20:
            ad['look'] = {'dx': config.LOOK_SENSITIVITY, 'dy': 0}
        elif diff < -20:
            ad['look'] = {'dx': -config.LOOK_SENSITIVITY, 'dy': 0}
        ad['action']      = 'explore:known_tree'
        ad['observation'] = f'Heading toward remembered tree at ({tree["x"]:.0f},{tree["z"]:.0f})'
        ad['confidence']  = 0.5
        return ad

    def _do_rebuild_fort(self, world_mem):
        """rebuild_fort goal handler: navigate to the remembered fort/base
        point, then work through FORT_BUILD_PLAN — a 5x5 floor, a 2-high
        perimeter wall, and one chest. Returns None if we can't navigate yet
        (no F3 fix, no facing, no fort coords) — caller falls back to blind
        explore sweep in that case."""
        fort = _resolve_fort_coords(world_mem)
        px = getattr(world_mem, 'pos_x', None)
        pz = getattr(world_mem, 'pos_z', None)
        facing = getattr(world_mem, 'facing', None)
        cardinal_deg = self._CARDINAL_DEG.get(facing)
        if fort is None or px is None or pz is None or cardinal_deg is None:
            return None

        fx, _fy, fz = fort
        dx, dz = fx - px, fz - pz
        dist = (dx * dx + dz * dz) ** 0.5

        ad = _idle()
        if dist > FORT_APPROACH_DIST:
            target_bearing = math.degrees(math.atan2(dx, -dz)) % 360
            diff = (target_bearing - cardinal_deg + 180) % 360 - 180
            ad['key'] = 'w'
            if diff > 20:
                ad['look'] = {'dx': config.LOOK_SENSITIVITY, 'dy': 0}
            elif diff < -20:
                ad['look'] = {'dx': -config.LOOK_SENSITIVITY, 'dy': 0}
            ad['action']      = 'rebuild_fort:navigate'
            ad['observation'] = f'Heading to fort at ({fx:.0f},{fz:.0f}), {dist:.1f} blocks away'
            ad['confidence']  = 0.5
            return ad

        # Close enough — place the next step in the hardcoded build plan
        # (floor, then 2-high perimeter wall, then a chest — see
        # FORT_BUILD_PLAN above).
        if self._fort_place_index >= len(FORT_BUILD_PLAN):
            ad['action']      = 'rebuild_fort:done'
            ad['observation'] = 'Fort build plan complete'
            ad['confidence']  = 0.7
            self._fort_place_index = 0   # reset in case the goal gets re-injected
            return ad

        phase, ox, oz, layer, slot = FORT_BUILD_PLAN[self._fort_place_index]
        # Floor: look straight down. Wall layer 0: level pitch (placing
        # against the floor block's side). Wall layer 1 / chest: look
        # slightly up to stack the second course. Same blind "look and
        # click, let Minecraft's face-adjacency rule spread the pattern"
        # trick the floor ring already relied on — no real 3D aim here.
        pitch = 30 if phase == 'floor' else (0 if layer == 0 else -20)
        ad['key']         = slot
        ad['look']        = {'dx': 0, 'dy': pitch}
        ad['click']       = [50.0, 50.0]
        ad['button']      = 'right'
        ad['action']      = f'rebuild_fort:place_{phase}({fx + ox:.0f},{fz + oz:.0f})'
        ad['observation'] = (f'Placing {phase} {self._fort_place_index + 1}/'
                             f'{len(FORT_BUILD_PLAN)} at fort')
        ad['confidence']  = 0.6
        if phase == 'chest':
            # Runtime records this in memory/chest_memory.py once executed.
            ad['chest_coords'] = (fx + ox, _fy, fz + oz)
        self._fort_place_index += 1
        return ad

    def _do_explore_sweep(self, world_mem=None):
        """Walk forward, then systematically sweep the horizontal FOV across
        EXPLORE_SECTORS discrete sectors (far-left..far-right), dwelling
        EXPLORE_SECTOR_DWELL ticks per sector so vision has time to settle
        and lock onto anything in view before the camera moves again.
        Sweeps left-to-right, then right-to-left next time, alternating —
        the completed direction is recorded in world_mem.last_scan_direction."""
        ad = _idle()

        if self._explore_phase == 'walk':
            ad['key']         = 'w'
            ad['action']      = 'explore:walk'
            ad['observation'] = 'Exploring — walking forward'
            ad['confidence']  = 0.5
            self._explore_pticks += 1
            if self._explore_pticks >= EXPLORE_WALK:
                self._explore_phase        = 'sector_scan'
                self._explore_pticks       = 0
                self._explore_sector_idx   = (0 if self._explore_sector_dir == 1
                                               else EXPLORE_SECTORS - 1)
                self._explore_sector_ticks = 0

        else:  # sector_scan
            if self._explore_sector_ticks == 0:
                # Just entered this sector — hop the camera one sector-width,
                # then hold still for the rest of the dwell so vision settles.
                dx = config.LOOK_SENSITIVITY * EXPLORE_SECTOR_STEP * self._explore_sector_dir
                ad['look'] = {'dx': dx, 'dy': 0}
            else:
                ad['look'] = {'dx': 0, 'dy': 0}
            sector_name = _SECTOR_NAMES[self._explore_sector_idx]
            ad['action']      = f'explore:scan:{sector_name}'
            ad['observation'] = f'Exploring — scanning {sector_name} sector'
            ad['confidence']  = 0.4
            self._explore_sector_ticks += 1

            if self._explore_sector_ticks >= EXPLORE_SECTOR_DWELL:
                self._explore_sector_ticks = 0
                self._explore_sector_idx  += self._explore_sector_dir
                if not (0 <= self._explore_sector_idx < EXPLORE_SECTORS):
                    # Swept the full arc — record direction, reverse for next
                    # time, and resume walking.
                    if world_mem is not None:
                        world_mem.last_scan_direction = (
                            'left_to_right' if self._explore_sector_dir == 1
                            else 'right_to_left')
                    self._explore_sector_dir *= -1
                    self._explore_phase  = 'walk'
                    self._explore_pticks = 0

        return self.state, ad

    def _apply_approach_log_lock(self, target: dict, objects: list) -> dict:
        """Hysteresis for APPROACH targeting (see APPROACH_LOG_LOSS_TICKS).

        Once locked onto a log, keep aiming at that same log even if this
        tick's fresh _pick_tree_target() call picked leaves instead — only
        release the lock if the locked log is completely undetected for
        APPROACH_LOG_LOSS_TICKS consecutive ticks, or a different log beats
        it by APPROACH_LOG_SWITCH_MARGIN confidence. Non-log targets (ore,
        animals) pass through untouched.
        """
        label  = target.get('label', '').lower()
        locked = self._approach_locked_label

        if locked is None:
            if 'log' in label:
                self._approach_locked_label     = label
                self._approach_locked_box       = target.get('box')
                self._approach_locked_conf      = target.get('conf', 0.0)
                self._approach_log_absent_ticks = 0
            return target

        # Re-find the locked log among this tick's raw detections, accepting
        # the same lowered confidence floor MINE uses for a locked target.
        locked_hit = _pick_by_label(objects, locked, min_conf=MINE_HOLD_CONF)

        if locked_hit is not None:
            self._approach_log_absent_ticks = 0
            self._approach_locked_box  = locked_hit.get('box', self._approach_locked_box)
            self._approach_locked_conf = locked_hit.get('conf', self._approach_locked_conf)
            # A different log must clearly outscore the locked one to steal it.
            if ('log' in label and label != locked
                    and target.get('conf', 0.0) >=
                        self._approach_locked_conf + APPROACH_LOG_SWITCH_MARGIN):
                self._approach_locked_label = label
                self._approach_locked_box   = target.get('box')
                self._approach_locked_conf  = target.get('conf', 0.0)
                return target
            return locked_hit

        # Locked log wasn't detected at all this tick — hold the lock through
        # a short grace period instead of snapping to whatever else is
        # visible (leaves, usually).
        self._approach_log_absent_ticks += 1
        if self._approach_log_absent_ticks < APPROACH_LOG_LOSS_TICKS:
            return {
                'label': locked,
                'box':   self._approach_locked_box,
                'conf':  self._approach_locked_conf,
            }

        # Fully lost the lock — release and adopt whatever this tick found.
        self._approach_locked_label     = None
        self._approach_locked_box       = None
        self._approach_locked_conf      = 0.0
        self._approach_log_absent_ticks = 0
        if 'log' in label:
            self._approach_locked_label = label
            self._approach_locked_box   = target.get('box')
            self._approach_locked_conf  = target.get('conf', 0.0)
        return target

    def _do_approach(self, target: dict, fw: int, fh: int, world_mem=None):
        """Walk toward target, aiming camera at its bbox centre."""
        box = target.get('box', [fw//4, fh//4, 3*fw//4, 3*fh//4])

        # Stall guard: if the bbox isn't growing tick-over-tick, we're not
        # actually closing distance — likely already pressed up against a
        # wall or other obstruction that only looks like a valid target.
        # Give up before this slides into MINE against whatever it is.
        #
        # Color-sourced detections (vision/color_detector.py) don't work
        # here — their bbox is the extent of a matched HSV pixel cluster,
        # which stays roughly fixed/low-variance as the player closes in
        # rather than growing like a real YOLO box does, so area-growth
        # always reads as "stalled" and aborts before MINE. Alignment
        # (is_on_target) doesn't work as a substitute either — the pixel
        # cluster jitters tick to tick and rarely sits inside the 5% dead
        # zone long enough to reset the counter, which just reproduces the
        # same false-stall deadlock (confirmed 2026-07-18: still aborted
        # at 12 ticks with alignment tracking). There's no reliable
        # "making progress" signal from a color bbox at all, so just don't
        # run the stall guard for color targets — let the normal
        # APPROACH_TICKS counter carry it into MINE.
        is_color = target.get('source') == 'color'
        area = max(1, (box[2] - box[0]) * (box[3] - box[1]))
        if is_color:
            self._approach_stall_count = 0
            self._approach_color_ticks += 1
            if self._approach_color_ticks >= APPROACH_COLOR_TICKS_TO_ABORT:
                label = target.get('label', 'block')
                print(f'[FSM] APPROACH: color-only {label} target not reached after '
                      f'{self._approach_color_ticks} ticks → blacklist + EXPLORE')
                self._ore_blacklist[label] = self._total_ticks + 60
                self._ore_blacklist_strikes[label] = self._ore_blacklist_strikes.get(label, 0) + 1
                self._approach_color_ticks = 0
                self._approach_stall_area  = None
                self._approach_stall_count = 0
                return self._goto(State.EXPLORE, _idle())
        else:
            self._approach_color_ticks = 0
            if self._approach_stall_area is None:
                self._approach_stall_area  = area
                self._approach_stall_count = 0
            else:
                if area < self._approach_stall_area * STALL_AREA_GROWTH_MIN:
                    self._approach_stall_count += 1
                else:
                    self._approach_stall_count = 0
                self._approach_stall_area = max(self._approach_stall_area, area)

            if self._approach_stall_count >= STALL_TICKS_TO_ABORT:
                label = target.get('label', 'block')
                print(f'[FSM] APPROACH: {label} bbox not growing after '
                      f'{self._approach_stall_count} ticks (stuck against something) '
                      f'→ blacklist + EXPLORE')
                self._ore_blacklist[label] = self._total_ticks + 60
                self._ore_blacklist_strikes[label] = self._ore_blacklist_strikes.get(label, 0) + 1
                self._approach_stall_area  = None
                self._approach_stall_count = 0
                return self._goto(State.EXPLORE, _idle())

        dx, dy = bbox_to_mouse_delta(box, fw, fh)
        # Leaves are a canopy nav-proxy — the trunk is below them. Pitching up
        # into the canopy causes an uncontrolled upward spiral; suppress dy so
        # we only turn left/right to face the tree, not pitch into the leaves.
        if target.get('label', '').lower() == 'leaves':
            dy = 0
        else:
            # Same runaway risk as MINE (see _dampen_pitch_dy) — a target
            # sitting high in frame for many consecutive APPROACH ticks with
            # no damping is what actually blew cumulative pitch past the
            # runtime's soft clamp on 2026-07-18 (bot ended up staring
            # straight up mid-approach, tree still visible off to the side).
            dy = self._dampen_pitch_dy(dy, world_mem)
        ad = _idle()
        # Ore → select the pickaxe on the very first approach tick (hotbar
        # selection persists in-game, so one press is enough) instead of
        # waiting for MINE to begin. Every other tick keeps walking ('w'
        # and '2' can't be sent in the same tick — see kb2040_packer combo
        # parsing, which only supports modifier+key, not two regular keys).
        if target.get('label', '').lower() in ORE_TARGETS and self._state_ticks == 1:
            ad['key'] = '2'
        else:
            ad['key'] = 'w'
        # Stalled against a small terrain lip (ledge/root) rather than a real
        # wall — a jump tap can hop over it. Try this before the abort at
        # STALL_TICKS_TO_ABORT gives up and blacklists the target outright.
        # Restricted to log targets: leaves bboxes never grow while closing
        # in on the trunk below them (same non-growth signature as a real
        # stall), so without this guard the bot jump-spams at leaves canopy
        # instead of walking under it (2026-07-18).
        if (STALL_TICKS_TO_JUMP <= self._approach_stall_count < STALL_TICKS_TO_ABORT
                and 'log' in target.get('label', '').lower()):
            ad['key'] = 'space'
        ad['look']        = {'dx': dx, 'dy': dy}
        ad['action']      = f'approach:{target.get("label","target")}'
        ad['observation'] = (f'Approaching {target.get("label","target")} '
                             f'conf={target.get("conf",0):.2f}')
        ad['confidence']  = target.get('conf', 0.5)

        # After enough ticks (roughly 2–3 seconds at 2 s/tick), switch to MINE.
        # Never transition to MINE targeting leaves — they're a navigation proxy
        # only; keep approaching until we can see an actual log block.
        if self._state_ticks >= APPROACH_TICKS:
            if 'leaves' in target.get('label', '').lower():
                # Reset timer so we keep approaching (don't spam the transition)
                self._state_ticks = APPROACH_TICKS - 2
            elif self._approach_stall_count >= STALL_TICKS_TO_JUMP:
                # Currently stalled/jumping (see guard above) — don't slide
                # into MINE against whatever's blocking us just because the
                # tick count ran out; let the jump-then-abort logic above
                # keep working it until it either clears or gives up.
                pass
            else:
                _label = target.get('label', '').lower()
                # No-dig-straight-down guard — see STRAIGHT_DOWN_* constants
                # above. Refuse to start MINEing a plain terrain block that
                # sits directly beneath the player; abort back to EXPLORE
                # instead of digging a hole to fall into.
                if (_label not in STRAIGHT_DOWN_EXEMPT
                        and _is_straight_down(target.get('box'), fw, fh, world_mem)):
                    print(f'[FSM] APPROACH: refusing straight-down dig on {_label} → EXPLORE')
                    self._ore_blacklist[_label] = self._total_ticks + 60
                    self._ore_blacklist_strikes[_label] = self._ore_blacklist_strikes.get(_label, 0) + 1
                    return self._goto(State.EXPLORE, _idle())
                mine_ad = self._begin_mine(target, fw, fh)
                # Double-check: MINE must never lock onto a leaves target —
                # if _mine_target somehow ended up as leaves (e.g. a future
                # caller passes one directly), bail back to EXPLORE instead
                # of holding click on canopy that yields nothing.
                locked_label = self._mine_target.get('label', '') if self._mine_target else ''
                if 'leaves' in locked_label:
                    self._mine_target = None
                    return self._goto(State.EXPLORE, _idle())
                return self._goto(State.MINE, mine_ad, world_mem)
        return State.APPROACH, ad

    def _begin_mine(self, target: dict, fw: int, fh: int) -> dict:
        """Build the initial action_dict for entering MINE state (aim only, no click yet)."""
        self._mine_ticks          = 0
        self._mine_absent_ticks   = 0
        self._smooth_dx           = 0
        self._smooth_dy           = 0
        self._mine_on_target_ticks = 0
        self._mine_last_box     = target.get('box')
        self._mine_target       = {'label': target.get('label', '').lower(),
                                    'box':   target.get('box')}
        self._mine_color_baseline_px = None
        box = target.get('box', [fw//4, fh//4, 3*fw//4, 3*fh//4])
        dx, dy = bbox_to_mouse_delta(box, fw, fh)
        ad = _idle()
        label = target.get('label', '').lower()
        # Tree targets → axe (slot 1); ore/stone → pickaxe (slot 2)
        ad['key']    = '1' if label in TREE_TARGETS else '2'
        ad['look']   = {'dx': dx, 'dy': dy}  # aim toward target
        ad['action'] = f'aim:{label or "block"}'
        return ad

    def _smooth_aim(self, dx: float, dy: float) -> tuple:
        """Exponentially smooth the raw aim delta to damp mouse oscillation."""
        SMOOTH = 0.35  # lower = smoother/slower, higher = faster
        self._smooth_dx = SMOOTH * dx + (1 - SMOOTH) * self._smooth_dx
        self._smooth_dy = SMOOTH * dy + (1 - SMOOTH) * self._smooth_dy
        return int(self._smooth_dx), int(self._smooth_dy)

    def _dampen_pitch_dy(self, dy: int, world_mem) -> int:
        """
        Vertical aim during APPROACH/MINE is prone to a positive-feedback loop: if
        the target bbox sits above frame centre the camera looks up, which (for a
        target being closed in on) tends to push the bbox even higher next tick,
        so dy keeps growing more negative. Halve the vertical gain, and once the
        session's cumulative pitch has already walked up past the runtime
        pitch-clamp's own threshold, refuse to send any more upward delta at all
        until the clamp corrects it back down.

        Runaway can still outrun that soft clamp — its nudge is only a few
        pixels a tick against dy that can hit -127 every tick. Past -150
        cumulative, stop trusting target-tracking dy entirely and force a fixed
        downward correction instead, regardless of where the bbox says to look.
        """
        cum_pitch = getattr(world_mem, 'cumulative_pitch_dy', 0) if world_mem is not None else 0
        if cum_pitch < -150:
            return 30
        dy = int(dy * 0.4)
        if cum_pitch < -100 and dy < 0:
            return 0
        return dy

    def _do_mine(self, mine_obj, fw: int, fh: int, world_mem=None, frame=None):
        """
        Two-phase mining:
          AIM  — send look delta each tick until crosshair is on the ore bbox.
                 No click yet; prevents chipping the wrong block.
          MINE — once on target, hold left-click for MINE_HOLD_MS per tick.
        """
        self._mine_ticks += 1

        if mine_obj is None:
            self._mine_absent_ticks += 1
            # Ore not detected this tick, but it's only been a flicker so far
            # (occlusion / motion blur / a conf dip below even MINE_HOLD_CONF).
            # Keep mining toward its last known position instead of bailing.
            if (self._mine_absent_ticks < MINE_ABSENT_TICKS_TO_BREAK
                    and self._mine_last_box is not None):
                box = self._mine_last_box
                dx, dy = bbox_to_mouse_delta(box, fw, fh)
                dy = self._dampen_pitch_dy(dy, world_mem)
                dx, dy = self._smooth_aim(dx, dy)
                ad = _idle()
                ad['key']         = '2'
                ad['look']        = {'dx': dx, 'dy': dy}
                ad['click']       = [50.0, 50.0]
                ad['delay_ms']    = config.MINE_HOLD_MS
                ad['action']      = 'mining:holding_through_flicker'
                ad['observation'] = (f'Mining — ore undetected '
                                     f'({self._mine_absent_ticks}/{MINE_ABSENT_TICKS_TO_BREAK}), '
                                     f'holding aim')
                ad['confidence']  = 0.5
                return State.MINE, ad

            # Gone for MINE_ABSENT_TICKS_TO_BREAK+ ticks in a row — broken!
            print(f'[FSM] MINE: target gone after {self._mine_ticks} ticks → COLLECT')
            broken_label = self._mine_target['label'] if self._mine_target else None
            if broken_label in TREE_TARGETS and world_mem is not None:
                world_mem.record_wood_chopped()
            self._mine_ticks        = 0
            self._mine_timeout_count = 0
            self._mine_timeout_label = None
            self._mine_absent_ticks  = 0
            self._mine_last_box      = None
            return self._goto(State.COLLECT, self._begin_collect())

        self._mine_absent_ticks = 0
        self._mine_last_box     = mine_obj.get('box', self._mine_last_box)

        # Direct pixel-cluster break check (color-sourced targets only — see
        # COLOR_BREAK_FRACTION above). Catches a break immediately even if a
        # same-labeled block elsewhere in frame would otherwise keep
        # mine_obj non-None and stall the MINE_ABSENT_TICKS_TO_BREAK wait.
        if frame is not None and mine_obj.get('source') == 'color':
            _color_label = mine_obj.get('label', '').lower()
            _px = sample_box_pixel_count(frame, mine_obj.get('box'), _color_label)
            if self._mine_color_baseline_px is None:
                if _px >= COLOR_BREAK_MIN_BASELINE_PX:
                    self._mine_color_baseline_px = _px
            elif _px < self._mine_color_baseline_px * COLOR_BREAK_FRACTION:
                print(f'[FSM] MINE: {_color_label} color cluster collapsed '
                      f'({_px}px < {COLOR_BREAK_FRACTION:.0%} of baseline '
                      f'{self._mine_color_baseline_px}px) → COLLECT')
                if _color_label in TREE_TARGETS and world_mem is not None:
                    world_mem.record_wood_chopped()
                self._mine_target            = None
                self._mine_ticks             = 0
                self._mine_timeout_count     = 0
                self._mine_timeout_label     = None
                self._mine_absent_ticks      = 0
                self._mine_last_box          = None
                self._mine_color_baseline_px = None
                return self._goto(State.COLLECT, self._begin_collect())

        box = mine_obj.get('box', [fw//4, fh//4, 3*fw//4, 3*fh//4])

        # No-dig-straight-down guard, continuous variant — the entry check in
        # _do_approach only catches a target that was already straight-down
        # at MINE start; sloped terrain can drift into this mid-dig. Bail out
        # every tick it's true rather than only once at entry.
        _mine_label = mine_obj.get('label', '').lower()
        if (_mine_label not in STRAIGHT_DOWN_EXEMPT
                and _is_straight_down(box, fw, fh, world_mem)):
            print(f'[FSM] MINE: aborting straight-down dig on {_mine_label} → EXPLORE')
            self._mine_target       = None
            self._mine_ticks        = 0
            self._mine_timeout_count = 0
            self._mine_timeout_label = None
            return self._goto(State.EXPLORE, _idle())

        dx, dy = bbox_to_mouse_delta(box, fw, fh)
        dy = self._dampen_pitch_dy(dy, world_mem)
        dx, dy = self._smooth_aim(dx, dy)
        on_target = is_on_target(box, fw, fh)

        if on_target:
            self._mine_on_target_ticks += 1
        else:
            self._mine_on_target_ticks = 0

        ad = _idle()
        # Use axe (slot 1) for wood/tree targets, pickaxe (slot 2) for everything else
        _lbl = mine_obj.get('label', '').lower() if mine_obj else (
            self._mine_target.get('label', '') if self._mine_target else '')
        ad['key']  = '1' if _lbl in TREE_TARGETS else '2'
        # Once locked on for a few ticks, stop nudging the camera entirely —
        # further "correction" off a jittery bbox centre is what drives the
        # pitch runaway; holding still and clicking is enough to keep mining.
        if self._mine_on_target_ticks >= 3:
            ad['look'] = {'dx': 0, 'dy': 0}
        else:
            ad['look'] = {'dx': dx, 'dy': dy}

        if on_target:
            ad['click']    = [50.0, 50.0]        # left-click screen centre
            ad['delay_ms'] = config.MINE_HOLD_MS  # hold for full mining tick
            phase = 'mining'
        else:
            phase = 'aiming'

        label = mine_obj.get('label', 'block')
        ad['action']      = f'{phase}:{label}'
        ad['observation'] = (f'{phase.capitalize()} {label} '
                             f'({self._mine_ticks}/{MINE_MAX_TICKS})'
                             f'{" dx="+str(dx)+",dy="+str(dy) if not on_target else ""}')
        ad['confidence']  = mine_obj.get('conf', 0.7)

        # Timed out — block probably at a bad angle; go back and re-approach
        if self._mine_ticks >= MINE_MAX_TICKS:
            curr_label = mine_obj.get('label', 'block') if mine_obj else 'block'
            # Track consecutive timeouts on same target
            if curr_label == self._mine_timeout_label:
                self._mine_timeout_count += 1
            else:
                self._mine_timeout_count = 1
                self._mine_timeout_label = curr_label
            self._mine_ticks = 0

            if self._mine_timeout_count >= 3:
                # Stuck 3+ times on same target — blacklist + explore.
                # Duration doubles each time a label gets blacklisted (capped at
                # 480 ticks) so a permanently-unreachable block doesn't just get
                # re-targeted the instant a fixed-length blacklist expires.
                BLACKLIST_TICKS = 60
                strikes = self._ore_blacklist_strikes.get(curr_label, 0)
                blacklist_duration = min(BLACKLIST_TICKS * (2 ** strikes), 480)
                self._ore_blacklist[curr_label] = self._total_ticks + blacklist_duration
                self._ore_blacklist_strikes[curr_label] = strikes + 1
                print(f'[FSM] MINE: {self._mine_timeout_count} timeouts on {curr_label}, '
                      f'blacklisting for {blacklist_duration} ticks (strike {strikes + 1}) → EXPLORE')
                self._mine_timeout_count = 0
                self._mine_timeout_label = None
                return self._goto(State.EXPLORE, _idle())

            print(f'[FSM] MINE: timed out after {MINE_MAX_TICKS} ticks on {curr_label} '
                  f'(attempt {self._mine_timeout_count}/3), re-approaching')
            return self._goto(State.APPROACH, _idle(), world_mem)

        return State.MINE, ad

    def _begin_collect(self) -> dict:
        self._collect_ticks = 0
        ad = _idle()
        ad['key']    = 'w'
        ad['action'] = 'collect:walk_to_drops'
        return ad

    def _do_collect(self):
        """Walk forward briefly to pick up dropped items."""
        self._collect_ticks += 1
        ad = _idle()
        ad['key']         = 'w'
        ad['action']      = 'collect:walk_to_drops'
        ad['observation'] = f'Collecting drops ({self._collect_ticks}/{COLLECT_TICKS})'
        ad['confidence']  = 0.6

        if self._collect_ticks >= COLLECT_TICKS:
            return self._goto(State.EXPLORE, _idle())
        return State.COLLECT, ad

    def _do_interact(self, target: dict, fw: int, fh: int):
        """Aim at an interactable block and right-click it."""
        box = target.get('box', [fw//4, fh//4, 3*fw//4, 3*fh//4])
        dx, dy = bbox_to_mouse_delta(box, fw, fh)
        ad = _idle()
        ad['look']        = {'dx': dx, 'dy': dy}
        ad['click']       = [50.0, 50.0]
        ad['button']      = 'right'
        ad['action']      = f'interact:{target.get("label","block")}'
        ad['observation'] = f'Interacting with {target.get("label","block")}'
        ad['confidence']  = target.get('conf', 0.6)

        # Stay in INTERACT for a couple of ticks, then return to EXPLORE
        if self._state_ticks >= 3:
            return self._goto(State.EXPLORE, _idle())
        return State.INTERACT, ad

    def _combat_action(self, mob: dict) -> dict:
        """Default combat response: flee (back away)."""
        ad = _idle()
        ad['key']         = 's'   # back away from mob
        ad['action']      = f'combat:flee:{mob.get("label","mob")}'
        ad['observation'] = (f'Hostile {mob.get("label","mob")} detected — fleeing '
                             f'(conf={mob.get("conf",0):.2f})')
        ad['confidence']  = 0.9
        return ad

    def _do_flee(self, mob: dict = None):
        """FLEE state: sprint backward (shift+s) for FLEE_TICKS ticks, then
        hand control back to whatever state was active before this flee
        began (see _flee_return_state, set at the trigger site in tick()).
        Re-triggered every tick the danger condition still holds (tick()'s
        priority-2 check refreshes _flee_ticks_left back to FLEE_TICKS), so
        this only actually counts down once the threat is no longer judged
        dangerous."""
        self._flee_ticks_left -= 1
        ad = _idle()
        ad['key']         = 'shift+s'   # sneak-backward — see kb2040_packer combo parsing
        label = mob.get('label', 'threat') if mob else 'threat'
        ad['action']      = f'flee:sprint_backward:{label}'
        ad['observation'] = (f'Fleeing {label} '
                             f'({FLEE_TICKS - self._flee_ticks_left}/{FLEE_TICKS})')
        ad['confidence']  = 0.9

        if self._flee_ticks_left <= 0:
            return_state = self._flee_return_state or State.EXPLORE
            self._flee_return_state = None
            return self._goto(return_state, _idle())
        return State.FLEE, ad

    # ── FISH state ────────────────────────────────────────────────────────────

    def _begin_fish(self) -> dict:
        """Equip fishing rod (hotbar slot 5) and prepare to cast."""
        self._fish_phase = 'cast'
        self._fish_ticks = 0
        ad = _idle()
        ad['key']    = '5'   # equip fishing rod
        ad['action'] = 'fish:equip_rod'
        return ad

    def _do_fish(self, bobber_obj, water_obj):
        """
        FISH state machine:
          cast  → right-click to cast line, transition to 'wait'
          wait  → idle 3-8 s; if bobber disappears, reel in
          reel  → right-click to reel, go to COLLECT
        """
        self._fish_ticks += 1
        ad = _idle()

        if self._fish_phase == 'cast':
            ad['click']       = [50.0, 50.0]
            ad['button']      = 'right'
            ad['action']      = 'fish:cast'
            ad['observation'] = 'Fishing — casting line'
            ad['confidence']  = 0.7
            self._fish_phase  = 'wait'
            self._fish_ticks  = 0

        elif self._fish_phase == 'wait':
            ad['action']      = 'fish:waiting'
            ad['observation'] = f'Fishing — waiting for bite ({self._fish_ticks})'
            ad['confidence']  = 0.5
            # Reel in if bobber disappears (dip detected) or after max wait
            bobber_gone = bobber_obj is None and self._fish_ticks > 3
            if bobber_gone or self._fish_ticks >= 8:
                self._fish_phase = 'reel'

        else:  # reel
            ad['click']       = [50.0, 50.0]
            ad['button']      = 'right'
            ad['action']      = 'fish:reel'
            ad['observation'] = 'Fishing — reeling in'
            ad['confidence']  = 0.8
            self._fish_phase  = 'cast'   # ready to cast again
            self._fish_ticks  = 0
            return self._goto(State.COLLECT, self._begin_collect())

        return State.FISH, ad

    # ── EAT state ────────────────────────────────────────────────────────────

    def _begin_eat(self) -> dict:
        """Enter EAT: start blind-cycling hotbar slots from slot 1."""
        self._eat_slot            = 1
        self._eat_phase           = 'select'
        self._eat_hold_ticks_left = 0
        self._eat_pre_hunger      = None
        ad = _idle()
        ad['action'] = 'eat:begin'
        return ad

    def _do_eat(self, hunger_frac: float, animal_obj):
        """
        EAT state machine — see EAT_HOLD_TICKS comment above for why this
        blind-cycles instead of reading inventory:

          select     -> tap the current slot's number key
          hold_start -> press-and-hold right-click (mouse_hold 'down')
          holding    -> keep holding for EAT_HOLD_TICKS ticks
          check      -> release right-click, compare hunger before/after —
                        rose -> that slot is food, keep eating it; unchanged
                        -> advance to the next slot

        After EAT_HOTBAR_SLOTS slots produce no hunger increase, there's no
        food in the hotbar at all — go hunt an animal for meat instead of
        idling forever.
        """
        if hunger_frac >= 0.20:
            print(f'[EAT] hunger restored ({hunger_frac:.0%}) — resuming')
            return self._goto(State.EXPLORE, _idle())

        ad = _idle()

        if self._eat_phase == 'select':
            if self._eat_slot > EAT_HOTBAR_SLOTS:
                print('[EAT] no food in hotbar, transitioning to HUNT')
                self._hunt_grace_ticks_left = HUNT_GRACE_TICKS
                _detections = [animal_obj] if animal_obj else []
                return self._goto(State.HUNT, self._begin_hunt(_detections, animal_obj or {'label': 'animal'}))
            print(f'[EAT] attempting eat on slot {self._eat_slot}')
            ad['key']         = str(self._eat_slot)
            ad['action']      = f'eat:select_slot:{self._eat_slot}'
            ad['observation'] = f'Eating — selecting hotbar slot {self._eat_slot}'
            ad['confidence']  = 0.5
            self._eat_phase   = 'hold_start'
            return State.EAT, ad

        if self._eat_phase == 'hold_start':
            self._eat_pre_hunger      = hunger_frac
            self._eat_hold_ticks_left = EAT_HOLD_TICKS
            ad['mouse_hold']        = 'down'
            ad['mouse_button_name'] = 'right'
            ad['action']            = f'eat:hold_start:slot{self._eat_slot}'
            ad['observation']       = f'Eating — holding right-click on slot {self._eat_slot}'
            ad['confidence']        = 0.5
            self._eat_phase = 'holding'
            return State.EAT, ad

        if self._eat_phase == 'holding':
            self._eat_hold_ticks_left -= 1
            ad['action']      = f'eat:holding:slot{self._eat_slot}'
            ad['observation'] = (f'Eating — holding right-click '
                                 f'({EAT_HOLD_TICKS - self._eat_hold_ticks_left}/{EAT_HOLD_TICKS})')
            ad['confidence']  = 0.5
            if self._eat_hold_ticks_left <= 0:
                self._eat_phase = 'check'
            return State.EAT, ad

        # phase == 'check'
        ad['mouse_hold']        = 'up'
        ad['mouse_button_name'] = 'right'
        ad['action']            = f'eat:release:slot{self._eat_slot}'
        ad['confidence']        = 0.5

        ate = (self._eat_pre_hunger is not None
               and hunger_frac > self._eat_pre_hunger + EAT_HUNGER_EPSILON)
        if ate:
            print(f'[EAT] slot {self._eat_slot} restored hunger '
                  f'({self._eat_pre_hunger:.0%} → {hunger_frac:.0%}) — continuing')
            ad['observation'] = f'Eating — slot {self._eat_slot} worked, hunger rising'
            # Same slot again — go another round of hold/check.
        else:
            print(f'[EAT] slot {self._eat_slot} did not restore hunger — trying next slot')
            ad['observation'] = f'Eating — slot {self._eat_slot} was not food, trying next'
            self._eat_slot += 1
        self._eat_phase = 'select'
        return State.EAT, ad

    # ── HUNT state ────────────────────────────────────────────────────────────

    def _begin_hunt(self, detections: list, target: dict) -> dict:
        """Latch TargetLock onto the best passive-mob detection and start
        sprinting toward it.

        `detections` is the current tick's full detection list — TargetLock
        scores every cow/sheep/pig/chicken itself (largest bbox, conf as
        tiebreak) rather than trusting the single pre-picked `target`, which
        exists only for logging/label purposes here."""
        self._hunt_ticks  = 0
        self._hunt_target = target.get('label', 'animal') if target else 'animal'
        self.target_lock.acquire(detections, PASSIVE_MOBS)
        ad = _idle()
        ad['key']    = 'w'
        ad['action'] = f'hunt:approach:{self._hunt_target}'
        return ad

    def _do_hunt(self, objects: list, animal_obj, fw: int, fh: int):
        """
        HUNT state: keep TargetLock's lock updated every tick and sprint
        toward / attack its aim point.

        Replaces the old "predict once, animal gone next tick, bail"
        behavior: a single missed detection no longer drops the target —
        TargetLock tolerates LOCK_DROPOUT_TICKS consecutive misses and,
        while locked, predicted_centroid (velocity extrapolation) keeps
        aim tracking the mob even on ticks where it wasn't re-detected.
        Only gives up once TargetLock.is_locked goes False, or a kill is
        confirmed by a meat/leather/feather drop (see HUNT_KILL_LABELS).
        """
        self._hunt_ticks += 1
        ad = _idle()

        animal_candidates = [
            o for o in objects
            if o.get('label', '').lower() in PASSIVE_MOBS
        ]
        matched = self.target_lock.update(animal_candidates)

        killed = any(o.get('label', '').lower() in HUNT_KILL_LABELS for o in objects)
        if killed:
            print(f'[HUNT] kill confirmed (drop detected) — '
                  f'track_id={self.target_lock.track_id} → COLLECT')
            return self._goto(State.COLLECT, self._begin_collect())

        if not self.target_lock.is_locked:
            print(f'[HUNT] {self._hunt_target} lost — '
                  f'miss={self.target_lock.consecutive_miss_count} → COLLECT')
            return self._goto(State.COLLECT, self._begin_collect())

        box = matched.get('box') if matched else None
        if box is None:
            # is_locked (checked above) guarantees last_known_centroid is
            # set, so predicted_centroid is never None here.
            cx, cy = self.target_lock.predicted_centroid
            lb = self.target_lock.last_known_bbox
            half_w = (lb[2] - lb[0]) / 2.0
            half_h = (lb[3] - lb[1]) / 2.0
            box = [cx - half_w, cy - half_h, cx + half_w, cy + half_h]

        dx, dy = bbox_to_mouse_delta(box, fw, fh)
        label  = matched.get('label') if matched else (self.target_lock.label or self._hunt_target)
        on_target = is_on_target(box, fw, fh)

        # Keep closing distance the whole time — melee needs to actually be
        # adjacent to the mob, and it may keep wandering while being chased.
        ad['key']  = 'w'
        ad['look'] = {'dx': dx, 'dy': dy}

        if self._hunt_ticks < APPROACH_TICKS and not on_target:
            ad['action']      = f'hunt:sprint:{label}'
            ad['observation'] = f'Hunting {label} — approaching'
            ad['confidence']  = matched.get('conf', 0.6) if matched else 0.4
        else:
            # Real in-game swing. 'click': [...] (used elsewhere in this
            # file) only moves the OS-absolute pointer and never registers
            # as an attack while Minecraft has the mouse captured — see
            # uart/kb2040_packer.py's action_dict_to_packets() comment.
            # 'mouse_button' goes out over the relative device the game
            # actually reads, so only swing once the crosshair is actually
            # on the mob (otherwise we just whiff air every tick).
            if on_target:
                ad['mouse_button'] = 'left'
            ad['action']      = f'hunt:attack:{label}'
            ad['observation'] = f'Hunting {label} — attacking ({self._hunt_ticks})'
            ad['confidence']  = matched.get('conf', 0.7) if matched else 0.4

        cx_log, cy_log = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
        print(f'[HUNT] locked track_id={self.target_lock.track_id} | '
              f'centroid=({cx_log:.0f},{cy_log:.0f}) | '
              f'miss={self.target_lock.consecutive_miss_count}')

        return State.HUNT, ad

    # ── FARM state ────────────────────────────────────────────────────────────

    def _begin_farm(self) -> dict:
        self._farm_ticks = 0
        ad = _idle()
        ad['action'] = 'farm:approach_crop'
        ad['key']    = 'w'
        return ad

    def _do_farm(self, crop_obj, fw: int, fh: int):
        """
        FARM state:
          phase 0-3  — walk to crop
          phase 4    — left-click to harvest
          phase 5    — right-click to replant seed
          phase 6+   — return to EXPLORE
        """
        self._farm_ticks += 1
        ad = _idle()

        if crop_obj is None:
            return self._goto(State.EXPLORE, _idle())

        box   = crop_obj.get('box', [fw//4, fh//4, 3*fw//4, 3*fh//4])
        dx, dy = bbox_to_mouse_delta(box, fw, fh)
        label  = crop_obj.get('label', 'crop')

        if self._farm_ticks <= 3:
            ad['key']         = 'w'
            ad['look']        = {'dx': dx, 'dy': dy}
            ad['action']      = f'farm:approach:{label}'
            ad['observation'] = f'Farming — walking to {label}'
            ad['confidence']  = 0.5
        elif self._farm_ticks == 4:
            ad['look']        = {'dx': dx, 'dy': dy}
            ad['click']       = [50.0, 50.0]
            ad['action']      = f'farm:harvest:{label}'
            ad['observation'] = f'Farming — harvesting {label}'
            ad['confidence']  = 0.7
        elif self._farm_ticks == 5:
            ad['look']        = {'dx': dx, 'dy': dy}
            ad['click']       = [50.0, 50.0]
            ad['button']      = 'right'
            ad['action']      = f'farm:replant:{label}'
            ad['observation'] = f'Farming — replanting {label}'
            ad['confidence']  = 0.6
        else:
            return self._goto(State.EXPLORE, _idle())

        return State.FARM, ad

    # ── Internal ──────────────────────────────────────────────────────────────

    def _goto(self, new_state: State, action: dict, world_mem=None):
        """Transition to a new state (or stay if already there)."""
        if new_state != self.state:
            print(f'[FSM] {self.state.value} → {new_state.value}')
            if self.state == State.MINE and new_state != State.MINE:
                self._mine_target = None   # clear target lock on any MINE exit/timeout
            if self.state == State.APPROACH and new_state != State.APPROACH:
                self._approach_locked_label     = None
                self._approach_locked_box       = None
                self._approach_locked_conf      = 0.0
                self._approach_log_absent_ticks = 0
            if self.state == State.HUNT and new_state != State.HUNT:
                self.target_lock.drop()   # keep TargetLock stateless between HUNT sessions
            # Any exit from EAT while mid-hold must release the physically
            # held right-click button (mouse_hold has no auto-release — see
            # uart/kb2040_packer.py) or it stays down through whatever state
            # comes next (e.g. a hostile-mob interrupt straight into FLEE).
            if (self.state == State.EAT and new_state != State.EAT
                    and self._eat_phase in ('hold_start', 'holding') and action is not None):
                action['mouse_hold']        = 'up'
                action['mouse_button_name'] = 'right'
                self._eat_phase = 'select'
            # Mandatory pitch reset on entry into APPROACH/MINE — a one-time
            # downward nudge that counteracts whatever upward drift EXPLORE's
            # scanning/panning accumulated before this transition, so it
            # can't compound through another approach+mine cycle on top of
            # it (2026-07-18: bot drifted past the runtime pitch clamp and
            # ended up staring straight at the sky).
            if new_state in (State.APPROACH, State.MINE) and action is not None:
                look = action.get('look') or {'dx': 0, 'dy': 0}
                look['dy'] = look.get('dy', 0) + 100
                action['look'] = look
                # If cumulative_pitch_dy is already past (or near) the runtime's
                # ±200 clamp on entry, the clamp fires on the very next tick and
                # jolts the camera before the state has a chance to do anything
                # useful — target falls out of frame and the state aborts right
                # back to EXPLORE/COLLECT. Reset (with a slight downward bias)
                # so a fresh APPROACH/MINE always starts with clamp headroom
                # (2026-07-18: cumulative_pitch_dy=217 caused an immediate
                # APPROACH→EXPLORE bounce).
                if world_mem is not None:
                    world_mem.cumulative_pitch_dy = 30
            self.state        = new_state
            self._state_ticks = 0
            self._same_state_ticks = 0
        return self.state, action
