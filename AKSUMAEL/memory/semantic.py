# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Semantic Memory                           ║
# ║  Facts AKSUMAEL knows, seeded + self-taught, for LLM  ║
# ║  context (see memory/context.py).                     ║
# ╚══════════════════════════════════════════════════════╝

import os
import sqlite3
import time

DB_PATH = 'data/memory.db'

# Seeded once, the first time this table is empty — basic Minecraft
# knowledge AKSUMAEL starts with rather than having to learn from scratch.
SEED_FACTS = {
    'diamond_ore':     'Found deep underground, typically Y level below 16. Needs an iron or better pickaxe to mine.',
    'creeper':         'A hostile mob that hisses then explodes when close — best to flee or attack from range.',
    'crafting_table':  'Needed to craft anything beyond the 2x2 inventory grid. Made from 4 planks.',
    'furnace':         'Smelts ore into ingots and cooks raw food. Needs fuel such as coal or wood.',
    'hunger':          'Drops over time and with sprinting/actions. Below 20% forces eating before anything else.',
    'wooden_pickaxe':  'The first pickaxe tier. Needed to mine stone and coal ore.',
    'stone_pickaxe':   'Second pickaxe tier. Needed to mine iron ore.',
    'iron_pickaxe':    'Third pickaxe tier. Needed to mine diamond and redstone ore.',
    'zombie':          'A slow hostile mob that attacks in melee. Burns in daylight if unarmored.',
    'night':           'Hostile mobs spawn more freely in the dark. Shelter or a well-lit area is safer.',
}


class SemanticMemory:
    """Concept -> {description, confidence, last_updated}, seeded from
    SEED_FACTS on first use and grown as mesh-llm surfaces new knowledge."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._connect().close()
        self._seed()

    def _connect(self):
        os.makedirs(os.path.dirname(self.db_path) or '.', exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS facts (
                concept      TEXT PRIMARY KEY,
                description  TEXT NOT NULL,
                confidence   REAL NOT NULL,
                last_updated REAL NOT NULL
            )
        ''')
        return conn

    def _seed(self):
        conn = self._connect()
        try:
            (count,) = conn.execute('SELECT COUNT(*) FROM facts').fetchone()
            if count == 0:
                now = time.time()
                conn.executemany(
                    'INSERT INTO facts (concept, description, confidence, last_updated) '
                    'VALUES (?, ?, ?, ?)',
                    [(c, d, 1.0, now) for c, d in SEED_FACTS.items()])
                conn.commit()
        finally:
            conn.close()

    def learn(self, concept: str, description: str, confidence: float = 1.0):
        conn = self._connect()
        try:
            conn.execute(
                'INSERT INTO facts (concept, description, confidence, last_updated) '
                'VALUES (?, ?, ?, ?) '
                'ON CONFLICT(concept) DO UPDATE SET '
                'description=excluded.description, confidence=excluded.confidence, '
                'last_updated=excluded.last_updated',
                (concept, description, confidence, time.time()))
            conn.commit()
        finally:
            conn.close()

    def recall(self, concept: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute(
                'SELECT concept, description, confidence, last_updated FROM facts '
                'WHERE concept = ?', (concept,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return {'concept': row[0], 'description': row[1],
                'confidence': row[2], 'last_updated': row[3]}

    def all_facts(self, top_n: int = 10) -> str:
        """Summary string for LLM context — highest-confidence, most
        recently updated facts first."""
        conn = self._connect()
        try:
            rows = conn.execute(
                'SELECT concept, description FROM facts '
                'ORDER BY confidence DESC, last_updated DESC LIMIT ?', (top_n,)).fetchall()
        finally:
            conn.close()
        if not rows:
            return ''
        return '; '.join(f'{c}: {d}' for c, d in rows)
