# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Cognitive Architecture Stub        ║
# ║  Belief State · Goal Stack · Episodic Memory          ║
# ║  · Inner Monologue — each persisted as its own JSON   ║
# ╚══════════════════════════════════════════════════════╝

import json
import os
import time

import config
from core.llm_router import route_llm_call

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
    """One self-generated thought, gated to fire an LLM call only every
    config.MONOLOGUE_EVERY_N_TICKS ticks (cheap haiku call, ~50 tokens) —
    between LLM ticks, update() is a no-op-cost template fallback so the
    thought log doesn't go stale. The most recent real thought is fed back
    into the next vision-LLM planning call as extra context."""

    FILE = f'{COGNITIVE_DIR}/inner_monologue.json'

    def __init__(self, goal_stack=None):
        self.thoughts = _load(self.FILE, [])
        self._goal_stack = goal_stack   # optional — for goal/failure context
        self.claude_call_count = 0      # session total, for health reporting

    def update(self, tick: int, objects: list, action_dict: dict, reward: float,
               goal: str = None, recent_episodes: list = None):
        if tick % max(1, config.MONOLOGUE_EVERY_N_TICKS) == 0:
            thought = self._generate_llm(objects, action_dict, reward, goal, recent_episodes)
            if thought is None:
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

    def _generate_llm(self, objects: list, action_dict: dict, reward: float,
                       goal: str = None, recent_episodes: list = None) -> str | None:
        if not config.LOCAL_LLM_ENABLED:
            return None
        labels = [o.get('label') for o in objects if o.get('label')]
        fails  = ''
        if recent_episodes:
            bad = [e.get('goal') for e in recent_episodes[-3:] if e.get('outcome') != 'success']
            if bad:
                fails = f' Recent failures: {", ".join(bad)}.'
        prompt = (
            'You are the inner monologue of a Minecraft AI. In ONE short sentence '
            '(max 20 words), think out loud about what to do next. '
            f'Current goal: {goal or "explore"}. Visible: {", ".join(labels) or "nothing"}. '
            f'Last reward: {reward:+.2f}.{fails} '
            'Respond with only the sentence, no quotes, no preamble.'
        )
        # Generous budget — the model 'thinks' before answering, which can
        # burn several hundred tokens before the actual sentence.
        raw, provider = route_llm_call(prompt, max_tokens=800, timeout=45)
        if provider == 'claude':
            self.claude_call_count += 1
        return raw.strip() if raw else None

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

    def update(self, tick: int, objects: list, action_dict: dict, reward: float,
               goal: str = None, recent_episodes: list = None):
        self.belief.update(tick, objects, action_dict, reward)
        self.goals.update(tick, objects, action_dict, reward)
        self.episodic.update(tick, objects, action_dict, reward)
        self.monologue.update(tick, objects, action_dict, reward,
                               goal=goal, recent_episodes=recent_episodes)
