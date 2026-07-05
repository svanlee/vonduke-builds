# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Cognitive Architecture Stub        ║
# ║  Belief State · Goal Stack · Episodic Memory          ║
# ║  · Inner Monologue — each persisted as its own JSON   ║
# ╚══════════════════════════════════════════════════════╝

import json
import os
import time

COGNITIVE_DIR = 'data/cognitive'
MAX_EPISODES  = 50
MAX_THOUGHTS  = 50


def _load(path, default):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            print(f'[COGNITIVE] load error {path}: {e}')
    return default


def _save(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f'[COGNITIVE] save error {path}: {e}')


class BeliefState:
    """Current best guess about the world — one flat dict, overwritten each tick."""

    FILE = f'{COGNITIVE_DIR}/belief_state.json'

    def __init__(self):
        self.beliefs = _load(self.FILE, {})

    def update(self, tick: int, objects: list, action_dict: dict, reward: float):
        seen = {o.get('label'): o.get('conf') for o in objects if o.get('label')}
        self.beliefs.update({
            'tick':            tick,
            'updated':         time.time(),
            'objects_seen':    seen,
            'last_action':     action_dict.get('action'),
            'last_key':        action_dict.get('key'),
            'last_confidence': action_dict.get('confidence', 0.0),
            'last_reward':     reward,
        })
        _save(self.FILE, self.beliefs)

    def set(self, key: str, value):
        """Manual belief override (e.g. from a skill or user label)."""
        self.beliefs[key] = value
        _save(self.FILE, self.beliefs)

    def get(self, key: str, default=None):
        return self.beliefs.get(key, default)


class GoalStack:
    """LIFO stack of active goals. Bottom goal is the standing default and is never popped."""

    FILE = f'{COGNITIVE_DIR}/goal_stack.json'
    DEFAULT_GOAL = {'name': 'survive', 'priority': 0}

    def __init__(self):
        self.stack = _load(self.FILE, [dict(self.DEFAULT_GOAL)])

    def push(self, name: str, priority: float = 1.0, meta: dict = None):
        self.stack.append({
            'name': name, 'priority': priority,
            'meta': meta or {}, 'pushed': time.time(),
        })
        _save(self.FILE, self.stack)

    def pop(self):
        if len(self.stack) > 1:
            goal = self.stack.pop()
            _save(self.FILE, self.stack)
            return goal
        return None

    def peek(self) -> dict:
        return self.stack[-1]

    # --- reactive goal logic -------------------------------------------
    THREATS = {
        'creeper':     ('flee',        9.0),
        'fire_hazard': ('avoid_fire',  8.0),
    }
    OPPORTUNITIES = {
        'diamond_ore':  ('mine_diamond',  5.0),
        'emerald_ore':  ('mine_emerald',  4.0),
        'redstone_ore': ('mine_redstone', 3.0),
        'copper_ore':   ('mine_copper',   2.5),
        'coal_ore':     ('mine_coal',     2.0),
    }

    def update(self, tick: int, objects: list, action_dict: dict, reward: float):
        """Reactive layer: push goals for visible triggers, retire stale ones.
        Deliberative goals (LLM-pushed) are untouched — no 'trigger' meta."""
        labels = {o.get('label') for o in objects if o.get('label')}
        active = {g['name'] for g in self.stack}

        for table in (self.THREATS, self.OPPORTUNITIES):
            for label, (goal, pri) in table.items():
                if label in labels and goal not in active:
                    self._insert_by_priority({
                        'name': goal, 'priority': pri,
                        'meta': {'trigger': label, 'tick': tick},
                        'pushed': time.time(),
                    })

        # retire reactive goals whose trigger is no longer visible
        self.stack = [
            g for g in self.stack
            if 'trigger' not in g.get('meta', {})
            or g['meta']['trigger'] in labels
        ] or [dict(self.DEFAULT_GOAL)]

        _save(self.FILE, self.stack)

    def _insert_by_priority(self, goal: dict):
        """Insert so the stack stays sorted ascending by priority (top = highest)."""
        i = len(self.stack)
        while i > 1 and self.stack[i - 1]['priority'] > goal['priority']:
            i -= 1
        self.stack.insert(i, goal)


class EpisodicMemory:
    """Rolling log of (observation, action, reward) episodes, capped and persisted."""

    FILE = f'{COGNITIVE_DIR}/episodic_memory.json'

    def __init__(self):
        self.episodes = _load(self.FILE, [])

    def update(self, tick: int, objects: list, action_dict: dict, reward: float):
        self.episodes.append({
            'tick':        tick,
            'ts':          time.time(),
            'observation': action_dict.get('observation', ''),
            'action':      action_dict.get('action'),
            'objects':     [o.get('label') for o in objects if o.get('label')],
            'reward':      reward,
        })
        self.episodes = self.episodes[-MAX_EPISODES:]
        _save(self.FILE, self.episodes)

    def recent(self, n: int = 5) -> list:
        return self.episodes[-n:]


class InnerMonologue:
    """One self-generated thought per tick — a template-based stub, not an LLM call."""

    FILE = f'{COGNITIVE_DIR}/inner_monologue.json'

    def __init__(self):
        self.thoughts = _load(self.FILE, [])

    def update(self, tick: int, objects: list, action_dict: dict, reward: float):
        thought = self._compose(objects, action_dict, reward)
        self.thoughts.append({'tick': tick, 'ts': time.time(), 'thought': thought})
        self.thoughts = self.thoughts[-MAX_THOUGHTS:]
        _save(self.FILE, self.thoughts)

    def _compose(self, objects: list, action_dict: dict, reward: float) -> str:
        labels = [o.get('label') for o in objects if o.get('label')]
        seen   = f"I see {', '.join(labels)}." if labels else "I don't see anything notable."
        action = action_dict.get('action', 'wait')
        mood   = 'good' if reward > 0 else 'bad' if reward < 0 else 'neutral'
        return f"{seen} I chose to {action}. That felt {mood} (r={reward:+.2f})."

    def recent(self, n: int = 5) -> str:
        return '\n'.join(t['thought'] for t in self.thoughts[-n:])


class CognitiveArchitecture:
    """
    Aggregates Belief State, Goal Stack, Episodic Memory, and Inner Monologue.
    Call update() once per tick with the same signals already flowing through
    the runtime loop (objects, action_dict, reward) — each component persists
    itself to its own JSON file under data/cognitive/.
    """

    def __init__(self):
        self.belief    = BeliefState()
        self.goals     = GoalStack()
        self.episodic  = EpisodicMemory()
        self.monologue = InnerMonologue()

    def update(self, tick: int, objects: list, action_dict: dict, reward: float):
        self.belief.update(tick, objects, action_dict, reward)
        self.goals.update(tick, objects, action_dict, reward)
        self.episodic.update(tick, objects, action_dict, reward)
        self.monologue.update(tick, objects, action_dict, reward)
