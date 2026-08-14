# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Episodic Memory (consolidated)            ║
# ║  Two complementary stores in one module:              ║
# ║  • EpisodicMemory — SQLite FSM-event log (fine-grain) ║
# ║  • EpisodeMemory  — JSONL goal-attempt log (coarse)   ║
# ╚══════════════════════════════════════════════════════╝
#
# EpisodicMemory feeds memory/context.py's LLM context builder.
# EpisodeMemory tracks completed goal attempts for JARVIS-1
# similarity retrieval (retrieve_similar / context_snippet).
# Previously lived in core/episode_memory.py — consolidated here
# 2026-08-14 to have one episodic-memory module.

import json
import os
import sqlite3
import time

import config

DB_PATH = 'data/memory.db'


class EpisodicMemory:
    """One row per recorded event: {timestamp, fsm_state, goal, action,
    outcome, observations}. Shared data/memory.db file — a short connect
    timeout absorbs the rare lock contention between the main runtime
    process and axon/hub.py's separate process both writing/reading it."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._connect().close()

    def _connect(self):
        os.makedirs(os.path.dirname(self.db_path) or '.', exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS episodes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    REAL NOT NULL,
                fsm_state    TEXT,
                goal         TEXT,
                action       TEXT,
                outcome      TEXT,
                observations TEXT
            )
        ''')
        return conn

    def record(self, episode: dict):
        conn = self._connect()
        try:
            conn.execute(
                'INSERT INTO episodes (timestamp, fsm_state, goal, action, outcome, observations) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (episode.get('timestamp', time.time()),
                 episode.get('fsm_state'),
                 episode.get('goal'),
                 episode.get('action'),
                 episode.get('outcome'),
                 json.dumps(episode.get('observations') or [])))
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _row_to_dict(row) -> dict:
        ts, fsm_state, goal, action, outcome, observations = row
        try:
            obs = json.loads(observations) if observations else []
        except json.JSONDecodeError:
            obs = []
        return {'timestamp': ts, 'fsm_state': fsm_state, 'goal': goal,
                'action': action, 'outcome': outcome, 'observations': obs}

    def recent(self, n: int = 20) -> list:
        conn = self._connect()
        try:
            rows = conn.execute(
                'SELECT timestamp, fsm_state, goal, action, outcome, observations '
                'FROM episodes ORDER BY id DESC LIMIT ?', (n,)).fetchall()
        finally:
            conn.close()
        episodes = [self._row_to_dict(r) for r in rows]
        episodes.reverse()   # oldest -> newest
        return episodes

    def search(self, keyword: str) -> list:
        conn = self._connect()
        like = f'%{keyword}%'
        try:
            rows = conn.execute(
                'SELECT timestamp, fsm_state, goal, action, outcome, observations '
                'FROM episodes '
                'WHERE goal LIKE ? OR action LIKE ? OR outcome LIKE ? OR observations LIKE ? '
                'ORDER BY id DESC', (like, like, like, like)).fetchall()
        finally:
            conn.close()
        return [self._row_to_dict(r) for r in rows]


# ── Goal-attempt memory (JARVIS-1 pattern) ─────────────────────────────────
EPISODES_FILE = os.path.join(config.MEMORY_DIR, 'episodes.jsonl')


class EpisodeMemory:
    """Rolling window of completed goal attempts: {goal, plan, outcome,
    inventory_before, inventory_after, position, tick}. Persisted as JSONL
    (append-friendly, tolerant of partial writes) and capped in RAM to
    config.EPISODE_MEMORY_MAX.

    Formerly in core/episode_memory.py — moved here 2026-08-14 to
    consolidate both episodic stores in one module.  Import path is now
    ``from memory.episodic import EpisodeMemory`` (or via ``from core.episode_memory
    import EpisodeMemory`` which re-exports from here for backward compat).
    """

    def __init__(self):
        self.episodes = []
        self._load()

    def _load(self):
        if not os.path.exists(EPISODES_FILE):
            return
        try:
            with open(EPISODES_FILE) as f:
                lines = f.readlines()
            for line in lines[-config.EPISODE_MEMORY_MAX:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    self.episodes.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            print(f'[EPISODE] load error: {e}')

    def record(self, goal: str, plan: list, outcome: str,
               inv_before: dict, inv_after: dict, position=None, tick: int = 0):
        """outcome: 'success' | 'failure' | 'timeout' (freeform string ok)."""
        episode = {
            'goal':             goal,
            'plan':             plan or [],
            'outcome':          outcome,
            'inventory_before': dict(inv_before or {}),
            'inventory_after':  dict(inv_after or {}),
            'position':         list(position) if position else None,
            'tick':             tick,
            'ts':               time.time(),
        }
        self.episodes.append(episode)
        self.episodes = self.episodes[-config.EPISODE_MEMORY_MAX:]
        try:
            os.makedirs(config.MEMORY_DIR, exist_ok=True)
            with open(EPISODES_FILE, 'a') as f:
                f.write(json.dumps(episode) + '\n')
        except Exception as e:
            print(f'[EPISODE] save error: {e}')
        return episode

    # ── Retrieval ────────────────────────────────────────────────
    @staticmethod
    def _jaccard(a: dict, b: dict) -> float:
        ka, kb = set(a.keys()), set(b.keys())
        if not ka and not kb:
            return 1.0
        union = ka | kb
        if not union:
            return 0.0
        return len(ka & kb) / len(union)

    def retrieve_similar(self, current_goal: str, current_inventory: dict,
                          top_k: int = None) -> list:
        """Return the top_k most relevant past episodes for `current_goal`
        given `current_inventory`, ranked by:
          - same goal (hard filter — falls back to all episodes if none match)
          - Jaccard similarity on inventory item keys
          - successful outcomes preferred (tiebreak boost)
        """
        top_k = top_k or config.EPISODE_RETRIEVE_TOP_K
        if not self.episodes:
            return []

        same_goal = [e for e in self.episodes if e.get('goal') == current_goal]
        pool = same_goal if same_goal else self.episodes

        def _score(e):
            sim = self._jaccard(current_inventory or {}, e.get('inventory_before', {}))
            bonus = 0.25 if e.get('outcome') == 'success' else 0.0
            return sim + bonus

        ranked = sorted(pool, key=_score, reverse=True)
        return ranked[:top_k]

    def context_snippet(self, current_goal: str, current_inventory: dict,
                         top_k: int = None) -> str:
        """Render retrieved episodes as a short text block for LLM context —
        'Last time you tried X, you did Y and it worked/failed'."""
        similar = self.retrieve_similar(current_goal, current_inventory, top_k)
        if not similar:
            return ''
        lines = ['[EPISODES] Past attempts at similar goals:']
        for e in similar:
            plan_str = ' -> '.join(e.get('plan') or []) or 'no plan recorded'
            lines.append(
                f"  - goal={e.get('goal')} outcome={e.get('outcome')} "
                f"plan=[{plan_str}]"
            )
        return '\n'.join(lines)
