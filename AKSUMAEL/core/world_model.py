# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — World Model                        ║
# ║  In-session history + cross-session persistence     ║
# ╚══════════════════════════════════════════════════════╝

import json
import os
import time


WORLD_FILE = 'data/world_model.json'
MAX_PERSIST = 50   # entries kept on disk across sessions
MAX_SESSION = 20   # entries kept in RAM during a session


class WorldModel:
    def __init__(self):
        self.history     = []
        self.tick        = 0
        self.session_start = time.time()
        self.session_num   = 0
        self._load()

    # ── Persistence ────────────────────────────────────────────
    def _load(self):
        os.makedirs('data', exist_ok=True)
        if os.path.exists(WORLD_FILE):
            try:
                with open(WORLD_FILE) as f:
                    data = json.load(f)
                self.session_num = data.get('session_num', 0) + 1
                # Seed history from last session's tail
                prev = data.get('history', [])[-10:]
                self.history = prev
                print(f'[WORLD] session {self.session_num} | '
                      f'loaded {len(prev)} entries from last session')
            except Exception as e:
                print(f'[WORLD] load error: {e}')
                self.session_num = 1
        else:
            self.session_num = 1
            print(f'[WORLD] session {self.session_num} (first run)')

    def save(self):
        try:
            os.makedirs('data', exist_ok=True)
            data = {
                'session_num':  self.session_num,
                'last_saved':   time.time(),
                'history':      self.history[-MAX_PERSIST:],
            }
            with open(WORLD_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f'[WORLD] save error: {e}')

    # ── Runtime ────────────────────────────────────────────────
    def update(self, state: dict):
        self.tick += 1
        entry = {
            'tick':    self.tick,
            'session': self.session_num,
            'ts':      time.time(),
        }
        entry.update(state)
        # Slim down objects list for storage (just labels + conf)
        if 'objects' in entry:
            entry['objects'] = [
                {'label': o.get('label'), 'conf': o.get('conf')}
                for o in entry['objects']
            ]
        self.history.append(entry)
        if len(self.history) > MAX_SESSION:
            self.history.pop(0)

    def recent_summary(self, n: int = 3) -> str:
        """Return last N entries as a readable string for LLM context."""
        recent = self.history[-n:]
        lines  = []
        for e in recent:
            objs   = e.get('objects', [])
            labels = ', '.join(o['label'] for o in objs if o.get('label')) or 'nothing'
            action = e.get('action', 'none')
            lines.append(f"tick {e.get('tick','?')}: saw [{labels}], did: {action}")
        return '\n'.join(lines) if lines else 'No history yet.'

    def cross_session_summary(self, n: int = 5) -> str:
        """Return a summary including entries from previous sessions."""
        prev = [e for e in self.history if e.get('session', 0) < self.session_num]
        if not prev:
            return 'No previous session data.'
        recent_prev = prev[-n:]
        lines = [f'Previous session snippets (session {self.session_num - 1}):']
        for e in recent_prev:
            objs   = e.get('objects', [])
            labels = ', '.join(o['label'] for o in objs if o.get('label')) or 'nothing'
            lines.append(f"  saw [{labels}], did: {e.get('action','none')}")
        return '\n'.join(lines)

    def object_frequency(self) -> dict:
        """Count how often each object label has appeared this session."""
        freq = {}
        for e in self.history:
            if e.get('session') != self.session_num:
                continue
            for o in e.get('objects', []):
                l = o.get('label')
                if l:
                    freq[l] = freq.get(l, 0) + 1
        return dict(sorted(freq.items(), key=lambda x: x[1], reverse=True))
