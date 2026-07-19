# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Procedural Memory                         ║
# ║  Tracks what skills/approaches actually work, for     ║
# ║  LLM context (see memory/context.py).                 ║
# ╚══════════════════════════════════════════════════════╝

import os
import sqlite3
import time

DB_PATH = 'data/memory.db'


class ProceduralMemory:
    """skill_name -> {attempts, successes, last_used, notes}. success_rate
    is derived (successes / attempts), not stored, so it's always
    consistent with the raw counts."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._connect().close()

    def _connect(self):
        os.makedirs(os.path.dirname(self.db_path) or '.', exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS procedures (
                skill_name TEXT PRIMARY KEY,
                attempts   INTEGER NOT NULL DEFAULT 0,
                successes  INTEGER NOT NULL DEFAULT 0,
                last_used  REAL,
                notes      TEXT
            )
        ''')
        return conn

    def record_outcome(self, skill_name: str, success: bool, notes: str = ""):
        conn = self._connect()
        try:
            row = conn.execute(
                'SELECT attempts, successes FROM procedures WHERE skill_name = ?',
                (skill_name,)).fetchone()
            attempts  = (row[0] if row else 0) + 1
            successes = (row[1] if row else 0) + (1 if success else 0)
            conn.execute(
                'INSERT INTO procedures (skill_name, attempts, successes, last_used, notes) '
                'VALUES (?, ?, ?, ?, ?) '
                'ON CONFLICT(skill_name) DO UPDATE SET '
                'attempts=excluded.attempts, successes=excluded.successes, '
                'last_used=excluded.last_used, '
                'notes=CASE WHEN excluded.notes != "" THEN excluded.notes ELSE procedures.notes END',
                (skill_name, attempts, successes, time.time(), notes))
            conn.commit()
        finally:
            conn.close()

    def best_skills(self, top_n: int = 10) -> list:
        conn = self._connect()
        try:
            rows = conn.execute(
                'SELECT skill_name, attempts, successes, last_used, notes '
                'FROM procedures').fetchall()
        finally:
            conn.close()
        skills = []
        for name, attempts, successes, last_used, notes in rows:
            rate = successes / attempts if attempts else 0.0
            skills.append({'skill_name': name, 'attempts': attempts,
                            'successes': successes, 'success_rate': rate,
                            'last_used': last_used, 'notes': notes})
        skills.sort(key=lambda s: s['success_rate'], reverse=True)
        return skills[:top_n]

    def summary(self, top_n: int = 5) -> str:
        """Summary string for LLM context."""
        skills = self.best_skills(top_n)
        if not skills:
            return ''
        return '; '.join(
            f"{s['skill_name']} ({s['success_rate']:.0%} success, {s['attempts']} tries)"
            for s in skills)
