# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Skill Evaluator                            ║
# ║  Tracks per-skill outcome history (attempts/successes/ ║
# ║  consecutive failures) so other systems — e.g. the     ║
# ║  code-skill generator — can react to a skill actually  ║
# ║  struggling, not just its first use.                   ║
# ╚══════════════════════════════════════════════════════╝

import os
import sqlite3
import time

DB_PATH = 'data/skill_evaluator.db'


class SkillEvaluator:
    """skill_name -> {attempts, successes, consecutive_failures, last_updated}.
    A fresh sqlite3 connection is opened per call (no connection is shared
    across threads), so this is safe to call from multiple threads/tick
    loops concurrently; `timeout` makes concurrent writers wait out a lock
    instead of raising."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._connect().close()

    def _connect(self):
        os.makedirs(os.path.dirname(self.db_path) or '.', exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS skill_outcomes (
                skill_name           TEXT PRIMARY KEY,
                attempts             INTEGER NOT NULL DEFAULT 0,
                successes            INTEGER NOT NULL DEFAULT 0,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                last_updated         REAL
            )
        ''')
        return conn

    def record(self, skill_name: str, success: bool):
        """Record one resolved skill execution outcome."""
        conn = self._connect()
        try:
            row = conn.execute(
                'SELECT attempts, successes, consecutive_failures '
                'FROM skill_outcomes WHERE skill_name = ?', (skill_name,)).fetchone()
            attempts  = (row[0] if row else 0) + 1
            successes = (row[1] if row else 0) + (1 if success else 0)
            streak    = 0 if success else (row[2] if row else 0) + 1
            conn.execute(
                'INSERT INTO skill_outcomes '
                '(skill_name, attempts, successes, consecutive_failures, last_updated) '
                'VALUES (?, ?, ?, ?, ?) '
                'ON CONFLICT(skill_name) DO UPDATE SET '
                'attempts=excluded.attempts, successes=excluded.successes, '
                'consecutive_failures=excluded.consecutive_failures, '
                'last_updated=excluded.last_updated',
                (skill_name, attempts, successes, streak, time.time()))
            conn.commit()
        finally:
            conn.close()

    def get_success_rate(self, skill_name: str) -> float:
        """Returns successes/attempts, or 0.0 if the skill has no recorded attempts."""
        conn = self._connect()
        try:
            row = conn.execute(
                'SELECT attempts, successes FROM skill_outcomes WHERE skill_name = ?',
                (skill_name,)).fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return 0.0
        return row[1] / row[0]

    def get_consecutive_failures(self, skill_name: str) -> int:
        """Returns the current run of back-to-back failures for this skill
        (reset to 0 on any recorded success), or 0 if never recorded."""
        conn = self._connect()
        try:
            row = conn.execute(
                'SELECT consecutive_failures FROM skill_outcomes WHERE skill_name = ?',
                (skill_name,)).fetchone()
        finally:
            conn.close()
        return row[0] if row else 0
