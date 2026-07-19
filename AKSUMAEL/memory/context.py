# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Memory Context                            ║
# ║  Assembles episodic + semantic + procedural memory    ║
# ║  into one compact string prepended to every LLM call. ║
# ╚══════════════════════════════════════════════════════╝
#
# core/runtime.py and core/overseer.py run in-process and can pass live
# values (fsm_state, goal, health/hunger, monologue) straight in. axon/hub.py
# runs as its own separate process (see axon/hub.py's module docstring) with
# none of those objects — any argument left as None falls back to reading
# the same cross-process files runtime.py already writes every tick
# (data/world_memory.json, data/goals.json, data/cognitive/inner_monologue.json).

import json
import os

from memory.episodic   import EpisodicMemory
from memory.semantic   import SemanticMemory
from memory.procedural import ProceduralMemory

WORLD_MEMORY_PATH = 'data/world_memory.json'
GOALS_PATH        = 'data/goals.json'
MONOLOGUE_PATH    = 'data/cognitive/inner_monologue.json'


def _read_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _disk_monologue(n: int = 3) -> str:
    thoughts = _read_json(MONOLOGUE_PATH, [])
    return '\n'.join(t.get('thought', '') for t in thoughts[-n:] if t.get('thought'))


class MemoryContext:
    def __init__(self, episodic: EpisodicMemory = None, semantic: SemanticMemory = None,
                 procedural: ProceduralMemory = None):
        self.episodic   = episodic or EpisodicMemory()
        self.semantic   = semantic or SemanticMemory()
        self.procedural = procedural or ProceduralMemory()

    def build_context_for_llm(self, fsm_state: str = None, goal: str = None,
                               health_pct: float = None, hunger_pct: float = None,
                               recent_monologue: str = None) -> str:
        """Concise context block: last 5 episodes, top facts, top
        procedures, current FSM state/goal/health/hunger, recent monologue."""
        need_disk_world = fsm_state is None or goal is None or health_pct is None or hunger_pct is None
        world = _read_json(WORLD_MEMORY_PATH, {}) if need_disk_world else {}

        fsm_state  = fsm_state or world.get('fsm_state', 'UNKNOWN')
        goal       = goal or _read_json(GOALS_PATH, {}).get('current', 'unknown')
        health_pct = health_pct if health_pct is not None else world.get('health_pct', 1.0)
        hunger_pct = hunger_pct if hunger_pct is not None else world.get('hunger_pct', 1.0)
        if recent_monologue is None:
            recent_monologue = _disk_monologue()

        lines = [
            f'[STATUS] state={fsm_state} goal={goal} '
            f'health={health_pct:.0%} hunger={hunger_pct:.0%}'
        ]

        episodes = self.episodic.recent(5)
        if episodes:
            ep_str = '; '.join(
                f"{e['fsm_state'] or '?'}/{e['goal'] or '?'} -> {e['outcome'] or '?'}"
                for e in episodes)
            lines.append(f'[RECENT EVENTS] {ep_str}')

        facts = self.semantic.all_facts()
        if facts:
            lines.append(f'[KNOWN FACTS] {facts}')

        procs = self.procedural.summary()
        if procs:
            lines.append(f'[SKILLS] {procs}')

        if recent_monologue:
            lines.append(f'[RECENT THOUGHTS] {recent_monologue}')

        return '\n'.join(lines)
