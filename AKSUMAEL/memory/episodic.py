# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Episodic Memory                           ║
# ║  SQLite log of what happened at each FSM transition,  ║
# ║  for LLM context (see memory/context.py).             ║
# ╚══════════════════════════════════════════════════════╝

import json
import os
import sqlite3
import time

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
