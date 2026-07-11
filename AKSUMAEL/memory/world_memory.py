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
        # ── Extended tracking (v1.1) ──────────────────────────────
        self.wood_count    = 0          # logs chopped this session
        self.food_items    = []         # food item names in inventory
        self.animals_seen  = {}         # animal label → last seen tick
        self.near_water    = False      # True when water bbox detected this tick
        self.llm_calls     = 0          # total LLM API calls this session
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
                self.y_level       = d.get('y_level', 64)
                self.biome         = d.get('biome', 'unknown')
                self.wood_count    = d.get('wood_count', 0)
                self.food_items    = d.get('food_items', [])
                self.animals_seen  = d.get('animals_seen', {})
                self.near_water    = d.get('near_water', False)
                self.llm_calls     = d.get('llm_calls', 0)
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
            # Track wood chopped via event string
            if 'chop' in event.lower() or 'log' in event.lower():
                self.wood_count += 1

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
        if f3_data.get('y_level') is not None:
            self.y_level = f3_data['y_level']
            self.depth_estimate = self.y_level
        if f3_data.get('biome'):
            self.biome = f3_data['biome']
        self._ticks_since_f3 = 0

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
        summary = (
            f'[MEMORY] Lifetime: {self.total_ticks} ticks, {self.deaths} deaths. '
            f'Most seen: {top_str}. Recent: {recent}. '
            f'Y={self.y_level} biome={self.biome} ({y_range}). '
            f'{day_str} (tick {self.game_tick}/{config.MC_DAY_TICKS}). '
            f'Hunger: {self.hunger_level}/20.'
        )
        if self.pickaxe_uses > config.PICKAXE_DURABILITY:
            summary += (' WARNING: pickaxe may be near breaking. '
                        'Craft or switch tools.')
        return summary

    def is_daytime(self) -> bool:
        lo, hi = config.DAYTIME_SAFE_RANGE
        return lo <= self.game_tick <= hi

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
