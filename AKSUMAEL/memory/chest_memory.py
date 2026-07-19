"""
Chest memory — remembers where chests are and what was last seen inside
them, so AKSUMAEL doesn't have to stumble across the same chest by luck
every session. Shares data/aurora.db with memory/aurora_memory.py.
"""
import json
import os
import sqlite3
import time

DB_PATH = os.path.join('data', 'aurora.db')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    x             REAL    NOT NULL,
    y             REAL    NOT NULL,
    z             REAL    NOT NULL,
    last_seen     TEXT    NOT NULL,
    contents_json TEXT
);
"""

# A chest already recorded within this many blocks of a new sighting is
# treated as the same chest (F3 position readings aren't pixel-exact, and
# we don't want a dozen near-duplicate rows for one physical chest).
_SAME_CHEST_RADIUS = 2.0


def _connect():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _find_existing(conn, x: float, y: float, z: float):
    """Return the row id of a known chest within _SAME_CHEST_RADIUS of
    (x, y, z), or None."""
    rows = conn.execute('SELECT id, x, y, z FROM chests').fetchall()
    for row_id, cx, cy, cz in rows:
        if ((cx - x) ** 2 + (cy - y) ** 2 + (cz - z) ** 2) ** 0.5 <= _SAME_CHEST_RADIUS:
            return row_id
    return None


def record_chest(x: float, y: float, z: float, contents: dict = None) -> int:
    """Record a chest at (x, y, z), or update the existing one nearby.
    Returns the chest's row id."""
    try:
        conn = _connect()
        now = time.strftime('%Y-%m-%dT%H:%M:%S')
        payload = json.dumps(contents) if contents else None
        existing_id = _find_existing(conn, x, y, z)
        if existing_id is not None:
            conn.execute(
                'UPDATE chests SET x=?, y=?, z=?, last_seen=?, contents_json=? WHERE id=?',
                (x, y, z, now, payload, existing_id))
            row_id = existing_id
        else:
            cur = conn.execute(
                'INSERT INTO chests (x, y, z, last_seen, contents_json) VALUES (?, ?, ?, ?, ?)',
                (x, y, z, now, payload))
            row_id = cur.lastrowid
        conn.commit()
        conn.close()
        return row_id
    except Exception as e:
        print(f'[CHEST_MEM] record error: {e}')
        return -1


def update_chest_contents(chest_id: int, contents: dict):
    """Overwrite the last-known contents of a previously recorded chest."""
    try:
        conn = _connect()
        conn.execute(
            'UPDATE chests SET contents_json=?, last_seen=? WHERE id=?',
            (json.dumps(contents), time.strftime('%Y-%m-%dT%H:%M:%S'), chest_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f'[CHEST_MEM] update error: {e}')


def get_nearest_chest(current_pos: tuple) -> dict | None:
    """Return {id, x, y, z, last_seen, contents, distance} for the closest
    known chest to current_pos=(x, y, z), or None if no chest is known."""
    try:
        px, py, pz = current_pos
        conn = _connect()
        rows = conn.execute(
            'SELECT id, x, y, z, last_seen, contents_json FROM chests').fetchall()
        conn.close()
        if not rows:
            return None
        best = None
        best_dist = None
        for row_id, x, y, z, last_seen, contents_json in rows:
            dist = ((x - px) ** 2 + (y - py) ** 2 + (z - pz) ** 2) ** 0.5
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best = {
                    'id': row_id, 'x': x, 'y': y, 'z': z,
                    'last_seen': last_seen,
                    'contents': json.loads(contents_json) if contents_json else {},
                    'distance': dist,
                }
        return best
    except Exception as e:
        print(f'[CHEST_MEM] get_nearest_chest error: {e}')
        return None


def all_chests() -> list:
    """Return every known chest, nearest-agnostic — mainly for debugging/CLI."""
    try:
        conn = _connect()
        rows = conn.execute(
            'SELECT id, x, y, z, last_seen, contents_json FROM chests').fetchall()
        conn.close()
        return [{
            'id': r[0], 'x': r[1], 'y': r[2], 'z': r[3],
            'last_seen': r[4],
            'contents': json.loads(r[5]) if r[5] else {},
        } for r in rows]
    except Exception as e:
        print(f'[CHEST_MEM] all_chests error: {e}')
        return []
