"""
AURORA — cross-environment episode memory.
SQLite store shared between all AKSUMAEL environments (Minecraft, Vehicle, etc.)

Also holds the vault: a small knowledge graph (entities/facts/relationships)
alongside the episode log, so callers can accumulate structured world
knowledge (named blocks/mobs/locations/items and what's known about them)
instead of just a flat episode history.
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

CREATE TABLE IF NOT EXISTS entities (
    name       TEXT PRIMARY KEY,
    type       TEXT,
    attributes TEXT    NOT NULL DEFAULT '{}',
    first_seen TEXT    NOT NULL,
    last_seen  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity        TEXT    NOT NULL,
    predicate     TEXT    NOT NULL,
    value         TEXT,
    confidence    REAL    NOT NULL DEFAULT 1.0,
    tick_observed INTEGER,
    timestamp     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_entity ON facts(entity);

CREATE TABLE IF NOT EXISTS relationships (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_a      TEXT    NOT NULL,
    relation      TEXT    NOT NULL,
    entity_b      TEXT    NOT NULL,
    tick_observed INTEGER,
    timestamp     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rel_entity_a ON relationships(entity_a);
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


# ── Vault: entities / facts / relationships ────────────────────────────

def remember_entity(name: str, type: str = None, attributes: dict = None):
    """Upsert an entity. `attributes` is merged into whatever's already
    stored (new keys win on conflict) rather than replacing it wholesale,
    so repeated sightings accumulate detail instead of erasing it."""
    try:
        conn = _connect()
        now = time.strftime('%Y-%m-%dT%H:%M:%S')
        row = conn.execute('SELECT attributes, type FROM entities WHERE name=?', (name,)).fetchone()
        if row:
            merged = json.loads(row[0]) if row[0] else {}
            merged.update(attributes or {})
            conn.execute(
                'UPDATE entities SET type=?, attributes=?, last_seen=? WHERE name=?',
                (type or row[1], json.dumps(merged), now, name))
        else:
            conn.execute(
                'INSERT INTO entities (name, type, attributes, first_seen, last_seen) '
                'VALUES (?, ?, ?, ?, ?)',
                (name, type, json.dumps(attributes or {}), now, now))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f'[AURORA] remember_entity error: {e}')


def remember_fact(entity: str, predicate: str, value, confidence: float = 1.0, tick: int = None):
    """Record a single (entity, predicate, value) fact observation."""
    try:
        conn = _connect()
        conn.execute(
            'INSERT INTO facts (entity, predicate, value, confidence, tick_observed, timestamp) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (entity, predicate, json.dumps(value) if not isinstance(value, str) else value,
             confidence, tick, time.strftime('%Y-%m-%dT%H:%M:%S')))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f'[AURORA] remember_fact error: {e}')


def remember_relation(entity_a: str, relation: str, entity_b: str, tick: int = None):
    """Record a single (entity_a, relation, entity_b) relationship observation."""
    try:
        conn = _connect()
        conn.execute(
            'INSERT INTO relationships (entity_a, relation, entity_b, tick_observed, timestamp) '
            'VALUES (?, ?, ?, ?, ?)',
            (entity_a, relation, entity_b, tick, time.strftime('%Y-%m-%dT%H:%M:%S')))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f'[AURORA] remember_relation error: {e}')


def recall_entity(name: str) -> dict:
    """Return the latest known {'type', 'attributes', 'first_seen', 'last_seen'}
    for an entity, or None if it's never been remembered."""
    try:
        conn = _connect()
        row = conn.execute(
            'SELECT type, attributes, first_seen, last_seen FROM entities WHERE name=?', (name,)
        ).fetchone()
        conn.close()
        if not row:
            return None
        return {'name': name, 'type': row[0], 'attributes': json.loads(row[1]) if row[1] else {},
                'first_seen': row[2], 'last_seen': row[3]}
    except Exception as e:
        print(f'[AURORA] recall_entity error: {e}')
        return None


def recall_facts(entity: str, limit: int = 50) -> list:
    """Return all recorded facts about an entity, most recent first."""
    try:
        conn = _connect()
        rows = conn.execute(
            'SELECT predicate, value, confidence, tick_observed, timestamp FROM facts '
            'WHERE entity=? ORDER BY id DESC LIMIT ?', (entity, limit)
        ).fetchall()
        conn.close()
        return [dict(zip(['predicate', 'value', 'confidence', 'tick_observed', 'timestamp'], r))
                for r in rows]
    except Exception as e:
        print(f'[AURORA] recall_facts error: {e}')
        return []


def context_for_llm(max_tokens: int = 500) -> str:
    """Format the most recently-touched entities and their facts/relations
    into a compact block suitable for injecting into an LLM system prompt.
    Budgets by characters (~4 chars/token) since that's cheap to check
    without a tokenizer on hand; trims whole entities off the end rather
    than truncating mid-entity."""
    budget = max_tokens * 4
    try:
        conn = _connect()
        entities = conn.execute(
            'SELECT name, type, attributes FROM entities ORDER BY last_seen DESC LIMIT 30'
        ).fetchall()
        lines = []
        for name, etype, attrs_json in entities:
            attrs = json.loads(attrs_json) if attrs_json else {}
            attr_str = ', '.join(f'{k}={v}' for k, v in attrs.items())
            line = f'- {name} ({etype or "unknown"})' + (f': {attr_str}' if attr_str else '')
            facts = conn.execute(
                'SELECT predicate, value FROM facts WHERE entity=? ORDER BY id DESC LIMIT 3',
                (name,)
            ).fetchall()
            if facts:
                line += ' [' + '; '.join(f'{p}={v}' for p, v in facts) + ']'
            lines.append(line)
        conn.close()

        out = []
        used = 0
        for line in lines:
            if used + len(line) + 1 > budget:
                break
            out.append(line)
            used += len(line) + 1
        return '\n'.join(out)
    except Exception as e:
        print(f'[AURORA] context_for_llm error: {e}')
        return ''
