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
            'last_saved':    time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        with open(MEMORY_FILE, 'w') as f:
            json.dump(d, f, indent=2)

    def update(self, objects: list, action: dict = None, event: str = None):
        """Call each tick with current detections."""
        self.total_ticks += 1
        for o in objects:
            label = o.get('label', 'unknown') if isinstance(o, dict) else getattr(o, 'label', 'unknown')
            if label and label not in ('unknown', ''):
                self.seen_objects[label] += 1

        if event:
            self.recent_events.append({'t': time.strftime('%H:%M:%S'), 'event': event})
            self.recent_events = self.recent_events[-MAX_RECENT:]

        # Auto-save every 50 ticks
        if self.total_ticks % 50 == 0:
            self.save()

    def context_summary(self) -> str:
        """One-paragraph summary to inject into Claude's prompt."""
        top = self.seen_objects.most_common(5)
        top_str = ', '.join(f'{k}({v})' for k, v in top) if top else 'nothing yet'
        recent = '; '.join(e['event'] for e in self.recent_events[-3:]) if self.recent_events else 'none'
        return (
            f'[MEMORY] Lifetime: {self.total_ticks} ticks, {self.deaths} deaths. '
            f'Most seen: {top_str}. Recent: {recent}.'
        )

    def record_death(self):
        self.deaths += 1
        self.update([], event='died')

    def record_survey(self):
        self.surveys += 1
        self.update([], event='survey sweep')
