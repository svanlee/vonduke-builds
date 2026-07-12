# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.1.0 — World Model                        ║
# ║  In-session history + cross-session persistence       ║
# ║  + spatial chunk memory (ores/threats/visited)        ║
# ╚══════════════════════════════════════════════════════╝

import json
import os
import time


WORLD_FILE = 'data/world_model.json'
MAX_PERSIST = 50   # entries kept on disk across sessions
MAX_SESSION = 20   # entries kept in RAM during a session

CHUNK_SIZE       = 16     # blocks per chunk side (Minecraft chunk size)
THREAT_TTL_TICKS = 60     # default threat time-to-live
MAX_ORES_PER_CHUNK = 8    # cap so a chunk entry can't grow unbounded


def _chunk_key(x: float, z: float) -> str:
    return f'{int(x // CHUNK_SIZE)}_{int(z // CHUNK_SIZE)}'


class WorldModel:
    def __init__(self):
        self.history     = []
        self.tick        = 0
        self.session_start = time.time()
        self.session_num   = 0

        # ── Spatial memory (new in v1.1) ────────────────────────
        self.position      = None   # (x, y, z) — latest F3 read
        self.chunks         = {}    # 'cx_cz' -> {ores_seen, threats_seen, resources, visited_at}
        self.base_location  = None  # (x, y, z) — last seen chest/crafting table
        self.spawn_point    = None  # (x, y, z)
        self.threats        = []    # [{label, bearing, confidence, seen_at_tick}]
        self.inventory       = {}   # latest known {item: count} mirror (convenience)

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
                self.chunks        = data.get('chunks', {})
                self.base_location = tuple(data['base_location']) if data.get('base_location') else None
                self.spawn_point   = tuple(data['spawn_point']) if data.get('spawn_point') else None
                print(f'[WORLD] session {self.session_num} | '
                      f'loaded {len(prev)} entries, {len(self.chunks)} known chunks '
                      f'from last session')
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
                'chunks':       self.chunks,
                'base_location': list(self.base_location) if self.base_location else None,
                'spawn_point':   list(self.spawn_point) if self.spawn_point else None,
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

    # ── Spatial memory ─────────────────────────────────────────
    def update_position(self, xyz: tuple):
        """Call with (x, y, z) from an F3 read. Marks the chunk visited."""
        if xyz is None or any(v is None for v in xyz):
            return
        self.position = tuple(xyz)
        if self.spawn_point is None:
            self.spawn_point = self.position
        self.mark_visited(self.position)

    def _chunk(self, x: float, z: float) -> dict:
        key = _chunk_key(x, z)
        c = self.chunks.get(key)
        if c is None:
            c = {'ores_seen': {}, 'threats_seen': [], 'resources': {}, 'visited_at': None}
            self.chunks[key] = c
        return c

    def mark_ore(self, label: str, xyz: tuple):
        """Record that `label` ore was seen at world position xyz."""
        if not label or xyz is None or any(v is None for v in xyz):
            return
        x, y, z = xyz
        c = self._chunk(x, z)
        ores = c['ores_seen']
        ores[label] = {'pos': [round(x, 1), round(y, 1), round(z, 1)], 'tick': self.tick}
        if len(ores) > MAX_ORES_PER_CHUNK:
            # Drop the oldest entry
            oldest = min(ores, key=lambda k: ores[k].get('tick', 0))
            if oldest != label:
                ores.pop(oldest, None)

    def mark_visited(self, xyz: tuple):
        if xyz is None or any(v is None for v in xyz):
            return
        x, y, z = xyz
        c = self._chunk(x, z)
        c['visited_at'] = self.tick

    def mark_base(self, xyz: tuple):
        """Call when a chest/crafting table is confirmed at the given position."""
        if xyz is None or any(v is None for v in xyz):
            return
        self.base_location = tuple(round(v, 1) for v in xyz)

    def nearest_ore(self, label: str):
        """Return the [x, y, z] of the nearest remembered `label` ore, or None."""
        candidates = []
        for c in self.chunks.values():
            hit = c.get('ores_seen', {}).get(label)
            if hit:
                candidates.append(hit['pos'])
        if not candidates:
            return None
        if self.position is None:
            return candidates[0]
        px, py, pz = self.position

        def _dist(p):
            return ((p[0] - px) ** 2 + (p[2] - pz) ** 2) ** 0.5
        return min(candidates, key=_dist)

    def get_chunk_summary(self, radius: int = 3) -> str:
        """Text summary of ores/resources known in chunks near the current
        position — cheap context for the LLM/curriculum without dumping the
        whole spatial memory."""
        if not self.chunks:
            return '[WORLD] No spatial memory yet.'
        if self.position is None:
            known = {}
            for c in self.chunks.values():
                for label in c.get('ores_seen', {}):
                    known[label] = known.get(label, 0) + 1
            if not known:
                return f'[WORLD] {len(self.chunks)} chunks visited, no ores logged yet.'
            parts = ', '.join(f'{k}×{v}' for k, v in sorted(known.items(), key=lambda x: -x[1])[:6])
            return f'[WORLD] {len(self.chunks)} chunks visited. Known ore locations: {parts}.'

        px, pz = self.position[0], self.position[2]
        cx, cz = int(px // CHUNK_SIZE), int(pz // CHUNK_SIZE)
        nearby_ores = {}
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                c = self.chunks.get(f'{cx + dx}_{cz + dz}')
                if not c:
                    continue
                for label, hit in c.get('ores_seen', {}).items():
                    nearby_ores.setdefault(label, hit['pos'])
        if not nearby_ores:
            return f'[WORLD] {len(self.chunks)} chunks visited. Nothing remembered nearby.'
        parts = ', '.join(f'{label} at {pos}' for label, pos in list(nearby_ores.items())[:5])
        return f'[WORLD] Remembered nearby: {parts}.'

    # ── Threats ────────────────────────────────────────────────
    def mark_threat(self, label: str, bearing: float = 0.0, confidence: float = 0.0):
        self.threats.append({
            'label': label, 'bearing': bearing,
            'confidence': confidence, 'seen_at_tick': self.tick,
        })

    def retire_stale_threats(self, current_tick: int = None, ttl: int = THREAT_TTL_TICKS):
        now = current_tick if current_tick is not None else self.tick
        before = len(self.threats)
        self.threats = [t for t in self.threats if now - t.get('seen_at_tick', 0) < ttl]
        return before - len(self.threats)
