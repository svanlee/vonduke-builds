"""
AURORA — cross-environment episode memory.
SQLite store shared between all AKSUMAEL environments (Minecraft, Vehicle, etc.)
"""
import json
import os
import sqlite3
import time

DB_PATH = os.path.join('data', 'aurora.db')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    env       TEXT    NOT NULL,
    timestamp TEXT    NOT NULL,
    location  TEXT,
    action    TEXT,
    outcome   TEXT,
    notes     TEXT,
    metadata  TEXT
);
CREATE INDEX IF NOT EXISTS idx_env_time ON episodes(env, timestamp);
"""


def _connect():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def record(env: str, action: str, outcome: str,
           location: str = None, notes: str = None, metadata: dict = None):
    """Record a single episode to the AURORA store."""
    try:
        conn = _connect()
        conn.execute(
            'INSERT INTO episodes (env, timestamp, location, action, outcome, notes, metadata) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (env, time.strftime('%Y-%m-%dT%H:%M:%S'),
             location, action, outcome, notes,
             json.dumps(metadata) if metadata else None)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f'[AURORA] record error: {e}')


def recent(env: str = None, limit: int = 20) -> list:
    """Retrieve recent episodes, optionally filtered by env."""
    try:
        conn = _connect()
        if env:
            rows = conn.execute(
                'SELECT env, timestamp, location, action, outcome, notes FROM episodes '
                'WHERE env=? ORDER BY id DESC LIMIT ?', (env, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT env, timestamp, location, action, outcome, notes FROM episodes '
                'ORDER BY id DESC LIMIT ?', (limit,)
            ).fetchall()
        conn.close()
        return [dict(zip(['env','timestamp','location','action','outcome','notes'], r)) for r in rows]
    except Exception as e:
        print(f'[AURORA] recent error: {e}')
        return []


def stats(env: str = None) -> dict:
    """Return outcome counts."""
    try:
        conn = _connect()
        q = 'SELECT outcome, COUNT(*) FROM episodes'
        params = ()
        if env:
            q += ' WHERE env=?'
            params = (env,)
        q += ' GROUP BY outcome'
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return dict(rows)
    except Exception as e:
        print(f'[AURORA] stats error: {e}')
        return {}
