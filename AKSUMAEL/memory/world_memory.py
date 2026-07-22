# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — World Memory                       ║
# ║  Persistent log of what AKSUMAEL has seen and done   ║
# ╚══════════════════════════════════════════════════════╝
#
# Injected as context into Claude's prompt each tick.

import json
import os
import time
import collections

import config

MEMORY_FILE = 'data/world_memory.json'
MAX_RECENT  = 20   # keep last N observations in memory

# Fall detection: an F3 Y read that dropped more than this since the
# previous F3 read means we just fell into a hole/shaft/lava pocket.
FALL_Y_DROP_THRESHOLD = 3


class WorldMemory:
    def __init__(self):
        self.seen_objects  = collections.Counter()   # label → total times seen
        self.recent_events = []                       # last N notable events
        self.session_start = time.time()
        self.total_ticks   = 0
        self.deaths        = 0
        self.surveys       = 0
        self.depth_estimate = 64   # surface default, keyword-heuristic fallback
        self.hunger_level  = 20    # 0-20 scale, estimated from hunger_bar bbox width
        self.game_tick     = 0     # wraps at config.MC_DAY_TICKS
        self.pickaxe_uses  = 0     # incremented each time a mine_* skill fires
        self.y_level       = 64    # real Y-level from F3 OCR, surface default
        self.biome         = 'unknown'   # real biome from F3 OCR
        self._ticks_since_f3 = 9999   # ticks since last successful F3 read
        # ── Spawn location (set once at session start from F3 photo) ──
        self.spawn_x    = -6.0
        self.spawn_y    = 67.0
        self.spawn_z    = -3.0
        # ── Extended tracking (v1.1) ──────────────────────────────
        self.wood_count    = 0          # logs chopped this session
        self.food_items    = []         # food item names in inventory
        self.animals_seen  = {}         # animal label → last seen tick
        self.near_water    = False      # True when water bbox detected this tick
        self.llm_calls     = 0          # total LLM API calls this session
        # ── Scan / Pathfinder (ephemeral — not persisted) ────────────
        self.scan_threats  = []         # [{bearing, label, identified, img_path, tick}]
        self._last_scan_tick = 0
        self.scan_path     = {}         # most recent pathfinder output
        # Tree sightings, keyed by nothing — just a pruned list of
        # {x, z, timestamp} so EXPLORE can head toward a remembered tree
        # instead of scanning blind when none is currently on screen.
        self.known_trees   = []
        # ── Fall detection (ephemeral — not persisted, see update_f3) ──
        self.fall_detected = False
        # ── Fort/base coordinates (v1.2) — optional, set externally (e.g. by
        # a future base-building routine) or read from disk if present.
        # rebuild_fort's coordinate resolver (core/fsm.py) checks these
        # before falling back to spawn_x/y/z.
        self.fort_location = None   # [x, y, z] or None
        self.base_coords   = None   # [x, y, z] or None
        self.home          = None   # [x, y, z] or None
        # ── F3 extended fields (v1.3) — see update_f3() ────────────
        self.pos_x         = None
        self.pos_z         = None
        self.facing        = None       # 'north'/'south'/'east'/'west' or None
        self.fps           = None
        self.chunk_x       = None
        self.chunk_z       = None
        self.light_level   = None       # 0-15 client light at targeted block, from F3
        self.day_count     = None       # vanilla day counter, from F3
        self.mob_spawn_risk = False     # True once light_level is known and < 8
        self.last_scan_direction = None # 'left_to_right' | 'right_to_left' — EXPLORE sweep
        # ── HUD pixel read (v1.3) — see memory/hud_reader.py ───────
        self.health_pct    = 1.0
        self.hunger_pct    = 1.0
        # ── FSM state (v1.4) — mirrored here (not just kept in the FSM
        # object) so axon/hub.py, which runs as its own separate process
        # (see axon/hub.py module docstring), can read AKSUMAEL's current
        # state off disk for voice Q&A context (memory/context.py).
        self.fsm_state     = 'UNKNOWN'
        self._load()

    def _load(self):
        if os.path.exists(MEMORY_FILE):
            try:
                d = json.load(open(MEMORY_FILE))
                self.seen_objects  = collections.Counter(d.get('seen_objects', {}))
                self.recent_events = d.get('recent_events', [])[-MAX_RECENT:]
                self.total_ticks   = d.get('total_ticks', 0)
                self.deaths        = d.get('deaths', 0)
                self.surveys       = d.get('surveys', 0)
                self.depth_estimate = d.get('depth_estimate', 64)
                self.hunger_level  = d.get('hunger_level', 20)
                self.game_tick     = d.get('game_tick', 0)
                self.pickaxe_uses  = d.get('pickaxe_uses', 0)
                _loaded_y = d.get('y_level', 64)
                # Same bounds check as update_f3() below — a session that
                # crashed/restarted while y_level was corrupted (e.g. by a
                # bad F3 OCR read) would otherwise reload the garbage value
                # straight back in (2026-07-21).
                self.y_level = _loaded_y if -128 <= _loaded_y <= 512 else 64
                self.biome         = d.get('biome', 'unknown')
                self.wood_count    = d.get('wood_count', 0)
                self.food_items    = d.get('food_items', [])
                self.animals_seen  = d.get('animals_seen', {})
                self.near_water    = d.get('near_water', False)
                self.llm_calls     = d.get('llm_calls', 0)
                self.known_trees   = d.get('known_trees', [])
                self.fort_location = d.get('fort_location')
                self.base_coords   = d.get('base_coords')
                self.home          = d.get('home')
                self.facing        = d.get('facing')
                self.fps           = d.get('fps')
                self.chunk_x       = d.get('chunk_x')
                self.chunk_z       = d.get('chunk_z')
                self.light_level   = d.get('light_level')
                self.day_count     = d.get('day_count')
                self.fsm_state     = d.get('fsm_state', 'UNKNOWN')
            except Exception:
                pass

    def save(self):
        os.makedirs('data', exist_ok=True)
        d = {
            'seen_objects':  dict(self.seen_objects),
            'recent_events': self.recent_events[-MAX_RECENT:],
            'total_ticks':   self.total_ticks,
            'deaths':        self.deaths,
            'surveys':       self.surveys,
            'depth_estimate': self.depth_estimate,
            'hunger_level':  self.hunger_level,
            'game_tick':     self.game_tick,
            'pickaxe_uses':  self.pickaxe_uses,
            'y_level':       self.y_level,
            'biome':         self.biome,
            'wood_count':    self.wood_count,
            'food_items':    self.food_items,
            'animals_seen':  self.animals_seen,
            'near_water':    self.near_water,
            'llm_calls':     self.llm_calls,
            'known_trees':   self.known_trees,
            'fort_location': self.fort_location,
            'base_coords':   self.base_coords,
            'home':          self.home,
            'facing':        self.facing,
            'fps':           self.fps,
            'chunk_x':       self.chunk_x,
            'chunk_z':       self.chunk_z,
            'light_level':   self.light_level,
            'day_count':     self.day_count,
            'fsm_state':     self.fsm_state,
            'last_saved':    time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        with open(MEMORY_FILE, 'w') as f:
            json.dump(d, f, indent=2)

    # Food item labels that indicate edible inventory items
    _FOOD_LABELS = frozenset({
        'cooked_beef', 'cooked_porkchop', 'cooked_chicken', 'cooked_mutton',
        'cooked_salmon', 'cooked_cod', 'bread', 'apple', 'carrot', 'potato',
        'baked_potato', 'salmon', 'cod', 'rabbit', 'cooked_rabbit',
        'melon_slice', 'pumpkin_pie', 'golden_apple',
    })

    # Passive animal labels
    _ANIMAL_LABELS = frozenset({'cow', 'sheep', 'pig', 'chicken'})

    def update(self, objects: list, action: dict = None, event: str = None):
        """Call each tick with current detections."""
        self.total_ticks += 1
        self.game_tick = (self.game_tick + 1) % config.MC_DAY_TICKS

        # Reset per-tick water flag
        self.near_water = False

        for o in objects:
            label = o.get('label', 'unknown') if isinstance(o, dict) else getattr(o, 'label', 'unknown')
            if label and label not in ('unknown', ''):
                self.seen_objects[label] += 1

                # Track water proximity
                if label == 'water':
                    self.near_water = True

                # Track animals seen (by tick number)
                if label in self._ANIMAL_LABELS:
                    self.animals_seen[label] = self.total_ticks

        # Track food items detected as objects (e.g., ground drops)
        food_seen = [
            (o.get('label') if isinstance(o, dict) else getattr(o, 'label', None))
            for o in objects
            if (o.get('label') if isinstance(o, dict) else getattr(o, 'label', None))
            in self._FOOD_LABELS
        ]
        if food_seen:
            # Merge into food_items list, dedup, cap at 20 entries
            self.food_items = list(dict.fromkeys(self.food_items + food_seen))[-20:]

        if event:
            self.recent_events.append({'t': time.strftime('%H:%M:%S'), 'event': event})
            self.recent_events = self.recent_events[-MAX_RECENT:]

        observation = (action or {}).get('observation', '') if action else ''
        self._ticks_since_f3 += 1
        self._update_depth(observation)

        # Auto-save every 50 ticks
        if self.total_ticks % 50 == 0:
            self.save()

    def update_f3(self, f3_data: dict):
        """Call with the dict returned by vision.f3_reader.read_f3()."""
        if not f3_data or not f3_data.get('f3_active'):
            return
        _new_y = f3_data.get('y_level')
        # Belt-and-suspenders: f3_reader.py already bounds-checks Y before
        # setting f3_active, but a corrupted/out-of-range Y silently poisons
        # y_level forever otherwise (nothing downstream re-validates it) —
        # it can satisfy a skill's max_y_level precondition as "already
        # surfaced" and fool anything watching Y for "reached the surface"
        # (2026-07-21: an OCR misread put y_level at 27824942).
        if _new_y is not None and -128 <= _new_y <= 512:
            prev_y = self.y_level
            self.y_level = _new_y
            self.depth_estimate = self.y_level
            if prev_y - self.y_level > FALL_Y_DROP_THRESHOLD:
                self.fall_detected = True
                print(f'[FALL] Y dropped {prev_y} -> {self.y_level} '
                      f'(>{FALL_Y_DROP_THRESHOLD}) — flagging fall')
        if f3_data.get('biome'):
            self.biome = f3_data['biome']
        # Store extended F3 data
        if f3_data.get('x') is not None:
            self.pos_x = round(f3_data['x'], 1)
        if f3_data.get('z') is not None:
            self.pos_z = round(f3_data['z'], 1)
        if f3_data.get('facing'):
            self.facing = f3_data['facing']
        if f3_data.get('fps') is not None:
            self.fps = f3_data['fps']
        if f3_data.get('chunk_x') is not None:
            self.chunk_x = f3_data['chunk_x']
        if f3_data.get('chunk_z') is not None:
            self.chunk_z = f3_data['chunk_z']
        if f3_data.get('light_level') is not None:
            self.light_level = f3_data['light_level']
            # Mobs can spawn on any block with light < 8 — treat this as a
            # standing risk flag rather than a one-tick event.
            self.mob_spawn_risk = self.light_level < 8
        if f3_data.get('day_count') is not None:
            self.day_count = f3_data['day_count']
        self._ticks_since_f3 = 0
        print(f"[F3] pos=({getattr(self,'pos_x','?')},{self.y_level},{getattr(self,'pos_z','?')}) "
              f"facing={getattr(self,'facing','?')} biome={self.biome} "
              f"chunk=({getattr(self,'chunk_x','?')},{getattr(self,'chunk_z','?')}) "
              f"fps={getattr(self,'fps','?')} light={self.light_level} day={self.day_count} "
              f"mob_risk={self.mob_spawn_risk}")

    # Real F3 reads stay authoritative for this many ticks before the
    # keyword heuristic is trusted to take back over.
    F3_FRESH_TICKS = 60

    def _update_depth(self, observation: str):
        """Depth estimate: trust a recent F3 OCR read, else fall back to a
        rough guess inferred from Claude's own observation text."""
        if self._ticks_since_f3 <= self.F3_FRESH_TICKS:
            return
        obs = (observation or '').lower()
        if any(w in obs for w in ('cave', 'underground', 'dark')):
            self.depth_estimate = max(0, self.depth_estimate - 5)
        elif any(w in obs for w in ('sky', 'surface', 'sun')):
            self.depth_estimate = 64

    def context_summary(self) -> str:
        """One-paragraph summary to inject into Claude's prompt."""
        top = self.seen_objects.most_common(5)
        top_str = ', '.join(f'{k}({v})' for k, v in top) if top else 'nothing yet'
        recent = '; '.join(e['event'] for e in self.recent_events[-3:]) if self.recent_events else 'none'
        day_str = 'DAY' if self.is_daytime() else 'NIGHT'
        y_range = ('diamond range' if self.y_level < 16
                   else 'coal range' if self.y_level < 40
                   else 'surface')
        pos_x   = getattr(self, 'pos_x',  None)
        pos_z   = getattr(self, 'pos_z',  None)
        facing  = getattr(self, 'facing', 'unknown')
        pos_str = f'XZ=({pos_x},{pos_z})' if pos_x is not None else 'XZ=unknown'
        spawn_str = f'Spawn=({self.spawn_x},{self.spawn_y},{self.spawn_z})'
        light_str = (f'{self.light_level}/15' if self.light_level is not None else 'unknown')
        risk_str  = ' MOB SPAWN RISK (light<8)!' if self.mob_spawn_risk else ''
        summary = (
            f'[MEMORY] Lifetime: {self.total_ticks} ticks, {self.deaths} deaths. '
            f'Most seen: {top_str}. Recent: {recent}. '
            f'Pos: {pos_str} Y={self.y_level} facing={facing} biome={self.biome} ({y_range}). '
            f'{spawn_str}. '
            f'{day_str} (tick {self.game_tick}/{config.MC_DAY_TICKS}, day {self.day_count}). '
            f'Light: {light_str}.{risk_str} '
            f'Hunger: {self.hunger_level}/20. Health: {self.health_pct:.0%}.'
        )
        if self.pickaxe_uses > config.PICKAXE_DURABILITY:
            summary += (' WARNING: pickaxe may be near breaking. '
                        'Craft or switch tools.')
        return summary

    def is_daytime(self) -> bool:
        lo, hi = config.DAYTIME_SAFE_RANGE
        return lo <= self.game_tick <= hi

    KNOWN_TREE_MAX_AGE_SEC  = 600   # prune sightings older than 10 minutes
    KNOWN_TREE_DEDUP_RADIUS = 3.0   # skip a new entry this close to an existing one

    def _prune_known_trees(self):
        cutoff = time.time() - self.KNOWN_TREE_MAX_AGE_SEC
        self.known_trees = [t for t in self.known_trees if t.get('timestamp', 0) >= cutoff]

    def record_tree_sighting(self, x: float, z: float):
        """Remember a tree's last-known position (from F3/belief-state XZ)
        so EXPLORE can navigate back to it later if none is on screen."""
        self._prune_known_trees()
        for t in self.known_trees:
            if ((t['x'] - x) ** 2 + (t['z'] - z) ** 2) ** 0.5 < self.KNOWN_TREE_DEDUP_RADIUS:
                t['timestamp'] = time.time()   # refresh instead of duplicating
                return
        self.known_trees.append({'x': x, 'z': z, 'timestamp': time.time()})

    def nearest_known_tree(self, x: float, z: float) -> dict:
        """Closest still-fresh remembered tree to (x, z), or None."""
        self._prune_known_trees()
        if not self.known_trees:
            return None
        return min(self.known_trees,
                   key=lambda t: (t['x'] - x) ** 2 + (t['z'] - z) ** 2)

    def set_hunger_fraction(self, frac: float):
        """frac: current hunger_bar bbox width / expected full-bar width."""
        self.hunger_level = max(0, min(20, round(frac * 20)))

    def record_pickaxe_use(self):
        self.pickaxe_uses += 1

    def record_wood_chopped(self):
        """Call when a log block is broken (MINE state target disappears)."""
        self.wood_count += 1
        self.update([], event='chopped log')

    def update_food_inventory(self, inventory_items: dict):
        """Sync food_items list from full inventory dict {label: count}."""
        self.food_items = [
            label for label, count in inventory_items.items()
            if count > 0 and label in self._FOOD_LABELS
        ]

    def record_death(self):
        self.deaths += 1
        self.update([], event='died')

    def record_survey(self):
        self.surveys += 1
        self.update([], event='survey sweep')

    def record_llm_call(self):
        self.llm_calls += 1

    # ── Scan / Pathfinder ────────────────────────────────────────

    SCAN_THREAT_TTL = 120   # ticks before a scan entry expires

    def record_scan(self, identified_threats: list, path: dict):
        """Store the latest scan results. Expire stale entries."""
        now = self.total_ticks
        # Expire old threats
        self.scan_threats = [
            t for t in self.scan_threats
            if now - t.get('tick', 0) < self.SCAN_THREAT_TTL
        ]
        # Add fresh ones
        for t in identified_threats:
            self.scan_threats.append({**t, 'tick': now})
        self.scan_path = path
        self._last_scan_tick = now

    def scan_summary(self) -> str:
        """One-line scan context for LLM history injection."""
        if not self.scan_threats:
            return '[SCAN] No active threats logged.'
        labels = [t['label'] for t in self.scan_threats]
        unique = list(dict.fromkeys(labels))   # ordered dedup
        path   = self.scan_path
        action = path.get('action', '?') if path else '?'
        reason = path.get('reason', '') if path else ''
        age    = self.total_ticks - self._last_scan_tick
        return (
            f'[SCAN] Threats: {", ".join(unique)}. '
            f'Pathfinder: {action} — {reason}. '
            f'(scanned {age} ticks ago)'
        )
