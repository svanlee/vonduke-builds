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
    'leaves',  # break leaves for saplings / apples
    # General stone (lower priority, good for tunnelling)
    'stone', 'cobblestone', 'gravel',
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

# Minimum YOLO confidence to act on a detection
MIN_CONF = 0.50

# Once already MINEing an ore, accept a much lower confidence before giving
# up on it — a partially-broken block's texture confuses YOLO and its conf
# score drops well below MIN_CONF even though the ore is still there.
MINE_HOLD_CONF = 0.30

# Ore must be fully undetected (not just low-confidence) for this many
# consecutive ticks before we treat it as broken and move to COLLECT.
MINE_ABSENT_TICKS_TO_BREAK = 3

# Ore labels permanently excluded from targeting regardless of blacklist state.
# emerald_ore only generates in mountain biomes — we're not in one, so every
# YOLO detection of it is a false positive. Hard-blocked here (not just the
# blacklist) so the FSM never re-targets it even after blacklist expiry.
_UNSUPPORTED_ORE_LABELS = frozenset({'emerald_ore'})


# ── Tuning ────────────────────────────────────────────────────────────────────

APPROACH_TICKS   = 8     # ticks in APPROACH before switching to MINE
MINE_MAX_TICKS   = 40    # give up and re-approach after this many MINE ticks (40×0.5s=20s)
COLLECT_TICKS    = 4     # walk forward this long after block breaks
EXPLORE_WALK     = 12    # ticks walking before each pan
EXPLORE_PAN      = 6     # ticks of each directional pan


# ── State enum ────────────────────────────────────────────────────────────────

class State(enum.Enum):
    EXPLORE  = 'EXPLORE'
    APPROACH = 'APPROACH'
    MINE     = 'MINE'
    COMBAT   = 'COMBAT'
    INTERACT = 'INTERACT'
    EAT      = 'EAT'
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
        self._mine_ticks        = 0
        self._mine_timeout_count = 0   # consecutive timeouts on current target
        self._mine_timeout_label = None  # label being timed out on
        self._mine_absent_ticks = 0    # consecutive ticks with NO detection at all
        self._mine_last_box     = None  # last known box, held onto through low-conf flicker
        # Target lock: once MINE begins, only detections of this label update
        # the aim point — prevents flip-flopping between simultaneously
        # visible labels (e.g. leaves/log/birch_log) each tick, which kept
        # aim from ever converging on one block.
        self._mine_target       = None  # {'label': str, 'box': [..]} or None
        # Ore blacklist: {label: expire_tick} — suppress re-targeting after give-up
        self._ore_blacklist      = {}
        self._ore_blacklist_strikes = {}   # {label: times blacklisted} — for exponential backoff
        self._total_ticks        = 0   # global tick counter for blacklist expiry

        # COLLECT tracking
        self._collect_ticks = 0

        # EXPLORE sub-state: 'walk' | 'pan'
        self._explore_phase  = 'walk'
        self._explore_pticks = 0          # ticks in current phase
        self._pan_dir        = 1          # +1 = right, -1 = left

        # FISH tracking
        self._fish_phase     = 'cast'     # 'cast' | 'wait' | 'reel'
        self._fish_ticks     = 0

        # HUNT tracking
        self._hunt_ticks     = 0
        self._hunt_target    = None

        # FARM tracking
        self._farm_ticks     = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def tick(self, objects: list, world_mem=None, hunger_frac: float = 1.0, goal: str = None):
        """
        Evaluate one FSM tick.

        Args:
            objects:     YOLO detection list.  Each item: {label, conf, box}.
            world_mem:   WorldMemory instance (may be None).
            hunger_frac: Hunger bar fullness 0.0–1.0 (1.0 = full).
            goal:        Active goal name (e.g. 'find_and_chop_tree'), or None.

        Returns:
            (State, action_dict)
        """
        self._state_ticks += 1
        self._total_ticks += 1
        fw, fh = _infer_frame_dims(objects)

        # Expire blacklist entries
        self._ore_blacklist = {
            lbl: exp for lbl, exp in self._ore_blacklist.items()
            if exp > self._total_ticks
        }

        # ── Priority 1: hunger ────────────────────────────────────
        if hunger_frac < 0.40:
            return self._goto(State.EAT, _idle())

        # ── Priority 2: hostile mob → flee ────────────────────────
        mob = _pick_best(objects, HOSTILE_MOBS)
        if mob:
            return self._goto(State.COMBAT, self._combat_action(mob))

        # ── Gather candidate targets ───────────────────────────────
        # Filter out blacklisted ore labels before picking
        non_blacklisted = [
            o for o in objects
            if o.get('label', '') not in self._ore_blacklist
        ]
        # While already MINEing, accept much lower-confidence ore detections —
        # a partially-broken block's changed texture tanks YOLO's confidence
        # well below MIN_CONF even though the ore is still there to finish off.
        _ore_conf_floor = MINE_HOLD_CONF if self.state == State.MINE else MIN_CONF
        ore_obj      = _pick_best_ore(non_blacklisted, min_conf=_ore_conf_floor)
        tree_obj     = _pick_best(objects, TREE_TARGETS)
        if (tree_obj is not None and world_mem is not None
                and getattr(world_mem, 'pos_x', None) is not None
                and getattr(world_mem, 'pos_z', None) is not None):
            world_mem.record_tree_sighting(world_mem.pos_x, world_mem.pos_z)
        mine_obj     = ore_obj or tree_obj or _pick_best(objects, MINE_TARGETS)
        interact_obj = _pick_best(objects, INTERACT_TARGETS)
        animal_obj   = _pick_best(objects, PASSIVE_MOBS)
        water_obj    = _pick_best(objects, FISH_TARGETS)
        crop_obj     = _pick_best(objects, CROP_TARGETS)
        bobber_obj   = _pick_best(objects, {'fishing_bobber'})

        # ── Per-state logic ───────────────────────────────────────
        s = self.state

        if s == State.EAT:
            if hunger_frac >= 0.40:
                return self._goto(State.EXPLORE, _idle())
            return self.state, _idle()

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
                return self._do_mine(locked_obj, fw, fh)
            return self._do_mine(mine_obj, fw, fh)

        elif s == State.APPROACH:
            target = mine_obj or animal_obj
            if target:
                return self._do_approach(target, fw, fh)
            return self._goto(State.EXPLORE, _idle())

        elif s == State.INTERACT:
            if interact_obj:
                return self._do_interact(interact_obj, fw, fh)
            return self._goto(State.EXPLORE, _idle())

        elif s == State.FISH:
            return self._do_fish(bobber_obj, water_obj)

        elif s == State.HUNT:
            return self._do_hunt(animal_obj, fw, fh)

        elif s == State.FARM:
            return self._do_farm(crop_obj, fw, fh)

        else:  # EXPLORE (default) — dispatch by priority
            if interact_obj and interact_obj.get('conf', 0) > 0.65:
                ad = self._do_interact(interact_obj, fw, fh)[1]
                return self._goto(State.INTERACT, ad)
            # Priority 3: ore (high value)
            if ore_obj:
                ad = self._do_approach(ore_obj, fw, fh)[1]
                return self._goto(State.APPROACH, ad)
            # Priority 4: tree (need wood)
            if tree_obj:
                ad = self._do_approach(tree_obj, fw, fh)[1]
                return self._goto(State.APPROACH, ad)
            # Priority 5: animal (need food)
            if animal_obj:
                return self._goto(State.HUNT, self._begin_hunt(animal_obj))
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
        nearest remembered tree location instead of scanning blind."""
        if goal == 'find_and_chop_tree' and world_mem is not None:
            nav_ad = self._navigate_to_known_tree(world_mem)
            if nav_ad is not None:
                return self.state, nav_ad
        return self._do_explore_sweep()

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

    def _do_explore_sweep(self):
        """Walk forward, then pan left/right, repeat."""
        ad = _idle()

        if self._explore_phase == 'walk':
            ad['key']         = 'w'
            ad['action']      = 'explore:walk'
            ad['observation'] = 'Exploring — walking forward'
            ad['confidence']  = 0.5
            self._explore_pticks += 1
            if self._explore_pticks >= EXPLORE_WALK:
                self._explore_phase  = 'pan'
                self._explore_pticks = 0
                self._pan_dir        = 1   # start pan right

        else:  # pan
            dx = config.LOOK_SENSITIVITY * self._pan_dir
            ad['look']        = {'dx': dx, 'dy': 0}
            ad['action']      = 'explore:pan'
            ad['observation'] = 'Exploring — looking around'
            ad['confidence']  = 0.4
            self._explore_pticks += 1
            if self._explore_pticks >= EXPLORE_PAN:
                if self._pan_dir == 1:
                    self._pan_dir        = -1   # now pan left
                    self._explore_pticks = 0
                else:
                    # Completed both directions — back to walk
                    self._explore_phase  = 'walk'
                    self._explore_pticks = 0

        return self.state, ad

    def _do_approach(self, target: dict, fw: int, fh: int):
        """Walk toward target, aiming camera at its bbox centre."""
        box = target.get('box', [fw//4, fh//4, 3*fw//4, 3*fh//4])
        dx, dy = bbox_to_mouse_delta(box, fw, fh)
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
        ad['look']        = {'dx': dx, 'dy': dy}
        ad['action']      = f'approach:{target.get("label","target")}'
        ad['observation'] = (f'Approaching {target.get("label","target")} '
                             f'conf={target.get("conf",0):.2f}')
        ad['confidence']  = target.get('conf', 0.5)

        # After enough ticks (roughly 2–3 seconds at 2 s/tick), switch to MINE
        if self._state_ticks >= APPROACH_TICKS:
            return self._goto(State.MINE,
                              self._begin_mine(target, fw, fh))
        return State.APPROACH, ad

    def _begin_mine(self, target: dict, fw: int, fh: int) -> dict:
        """Build the initial action_dict for entering MINE state (aim only, no click yet)."""
        self._mine_ticks        = 0
        self._mine_absent_ticks = 0
        self._mine_last_box     = target.get('box')
        self._mine_target       = {'label': target.get('label', '').lower(),
                                    'box':   target.get('box')}
        box = target.get('box', [fw//4, fh//4, 3*fw//4, 3*fh//4])
        dx, dy = bbox_to_mouse_delta(box, fw, fh)
        ad = _idle()
        ad['key']    = '2'                   # select pickaxe hotbar slot
        ad['look']   = {'dx': dx, 'dy': dy}  # aim toward ore
        ad['action'] = f'aim:{target.get("label","block")}'
        return ad

    def _do_mine(self, mine_obj, fw: int, fh: int):
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
            self._mine_ticks        = 0
            self._mine_timeout_count = 0
            self._mine_timeout_label = None
            self._mine_absent_ticks  = 0
            self._mine_last_box      = None
            return self._goto(State.COLLECT, self._begin_collect())

        self._mine_absent_ticks = 0
        self._mine_last_box     = mine_obj.get('box', self._mine_last_box)

        box = mine_obj.get('box', [fw//4, fh//4, 3*fw//4, 3*fh//4])
        dx, dy = bbox_to_mouse_delta(box, fw, fh)
        on_target = is_on_target(box, fw, fh)

        ad = _idle()
        ad['key']  = '2'                         # keep pickaxe selected
        ad['look'] = {'dx': dx, 'dy': dy}        # always correct aim

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
            return self._goto(State.APPROACH, _idle())

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

    # ── HUNT state ────────────────────────────────────────────────────────────

    def _begin_hunt(self, target: dict) -> dict:
        """Sprint toward first visible passive mob."""
        self._hunt_ticks  = 0
        self._hunt_target = target.get('label', 'animal')
        ad = _idle()
        ad['key']    = 'w'
        ad['action'] = f'hunt:approach:{self._hunt_target}'
        return ad

    def _do_hunt(self, animal_obj, fw: int, fh: int):
        """
        HUNT state: sprint toward animal, then left-click to kill.
        After ~3 hit ticks with no animal visible, go to COLLECT.
        """
        self._hunt_ticks += 1
        ad = _idle()

        if animal_obj is None:
            # Animal gone (dead or fled) — collect drops
            print(f'[FSM] HUNT: {self._hunt_target} gone after {self._hunt_ticks} ticks → COLLECT')
            return self._goto(State.COLLECT, self._begin_collect())

        box = animal_obj.get('box', [fw//4, fh//4, 3*fw//4, 3*fh//4])
        dx, dy = bbox_to_mouse_delta(box, fw, fh)
        label  = animal_obj.get('label', 'animal')

        if self._hunt_ticks < APPROACH_TICKS:
            # Sprint toward the mob
            ad['key']         = 'w'
            ad['look']        = {'dx': dx, 'dy': dy}
            ad['action']      = f'hunt:sprint:{label}'
            ad['observation'] = f'Hunting {label} — approaching'
            ad['confidence']  = animal_obj.get('conf', 0.6)
        else:
            # Attack (left-click) while aiming
            ad['look']        = {'dx': dx, 'dy': dy}
            ad['click']       = [50.0, 50.0]
            ad['action']      = f'hunt:attack:{label}'
            ad['observation'] = f'Hunting {label} — attacking ({self._hunt_ticks})'
            ad['confidence']  = animal_obj.get('conf', 0.7)

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

    def _goto(self, new_state: State, action: dict):
        """Transition to a new state (or stay if already there)."""
        if new_state != self.state:
            print(f'[FSM] {self.state.value} → {new_state.value}')
            if self.state == State.MINE and new_state != State.MINE:
                self._mine_target = None   # clear target lock on any MINE exit/timeout
            self.state        = new_state
            self._state_ticks = 0
        return self.state, action
