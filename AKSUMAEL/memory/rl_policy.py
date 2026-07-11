"""
Simple Q-learning policy over skill selection.
State: tuple of top-3 detected YOLO class names (sorted).
Action: skill name.
Reward: from memory/reward.py accumulator.

This sits on top of the existing skill system — when multiple skills could fire,
the RL policy picks among them based on learned Q-values.
"""

import json, os, math, random
from collections import defaultdict
from pathlib import Path

Q_PATH = Path("data/q_table.json")
ALPHA = 0.1   # learning rate
GAMMA = 0.9   # discount
EPSILON = 0.15  # exploration rate

class RLPolicy:
    def __init__(self):
        self.q: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._load()
        self._last_state = None
        self._last_action = None

    def _load(self):
        if Q_PATH.exists():
            raw = json.loads(Q_PATH.read_text())
            for state, actions in raw.items():
                self.q[state].update(actions)

    def save(self):
        Q_PATH.write_text(json.dumps({k: dict(v) for k, v in self.q.items()}, indent=2))

    def _state_key(self, objects: list[dict]) -> str:
        """Encode current perception as a compact state string."""
        names = sorted({o.get("label", o.get("class", "unknown")) for o in objects})[:3]
        return ",".join(names) if names else "empty"

    def choose_skill(self, candidates: list[str], objects: list[dict]) -> str:
        """Epsilon-greedy skill selection from candidates."""
        if not candidates:
            return None
        state = self._state_key(objects)
        self._last_state = state

        if random.random() < EPSILON or not any(self.q[state].get(s, 0) for s in candidates):
            chosen = random.choice(candidates)
        else:
            chosen = max(candidates, key=lambda s: self.q[state].get(s, 0))

        self._last_action = chosen
        return chosen

    def update(self, reward: float, next_objects: list[dict]):
        """TD update after receiving reward."""
        if self._last_state is None or self._last_action is None:
            return
        next_state = self._state_key(next_objects)
        next_max = max(self.q[next_state].values(), default=0.0)
        old_q = self.q[self._last_state][self._last_action]
        self.q[self._last_state][self._last_action] = old_q + ALPHA * (reward + GAMMA * next_max - old_q)

    def stats(self) -> str:
        total = sum(len(v) for v in self.q.values())
        return f"Q-table: {len(self.q)} states, {total} entries"
