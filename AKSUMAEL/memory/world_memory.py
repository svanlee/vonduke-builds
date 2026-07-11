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
        self.depth_estimate = 64   # surface default
        self.hunger_level  = 20    # 0-20 scale, estimated from hunger_bar bbox width
        self.game_tick     = 0     # wraps at config.MC_DAY_TICKS
        self.pickaxe_uses  = 0     # incremented each time a mine_* skill fires
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
            'last_saved':    time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        with open(MEMORY_FILE, 'w') as f:
            json.dump(d, f, indent=2)

    def update(self, objects: list, action: dict = None, event: str = None):
        """Call each tick with current detections."""
        self.total_ticks += 1
        self.game_tick = (self.game_tick + 1) % config.MC_DAY_TICKS
        for o in objects:
            label = o.get('label', 'unknown') if isinstance(o, dict) else getattr(o, 'label', 'unknown')
            if label and label not in ('unknown', ''):
                self.seen_objects[label] += 1

        if event:
            self.recent_events.append({'t': time.strftime('%H:%M:%S'), 'event': event})
            self.recent_events = self.recent_events[-MAX_RECENT:]

        observation = (action or {}).get('observation', '') if action else ''
        self._update_depth(observation)

        # Auto-save every 50 ticks
        if self.total_ticks % 50 == 0:
            self.save()

    def _update_depth(self, observation: str):
        """Rough depth estimate inferred from Claude's own observation text."""
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
        depth_range = ('diamond range' if self.depth_estimate < 16
                       else 'coal range' if self.depth_estimate < 40
                       else 'surface')
        day_str = 'DAY' if self.is_daytime() else 'NIGHT'
        summary = (
            f'[MEMORY] Lifetime: {self.total_ticks} ticks, {self.deaths} deaths. '
            f'Most seen: {top_str}. Recent: {recent}. '
            f'Estimated depth: Y~{self.depth_estimate} ({depth_range}). '
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

    def record_death(self):
        self.deaths += 1
        self.update([], event='died')

    def record_survey(self):
        self.surveys += 1
        self.update([], event='survey sweep')
