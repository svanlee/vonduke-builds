# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Inner Monologue                    ║
# ║  Self-generated thought, persisted as JSON, fed back  ║
# ║  into the vision-LLM prompt as planning context.      ║
# ╚══════════════════════════════════════════════════════╝
#
# Formerly also housed BeliefState, GoalStack, and EpisodicMemory
# "cognitive architecture" stubs. Removed: none of the three were ever
# read back by anything (grep confirmed no caller outside this file and
# test_cognitive.py touched cognitive.belief / cognitive.goals /
# cognitive.episodic) — each was a pure write-to-JSON-every-tick no-op.
# GoalStack's reactive threat/opportunity rules (creeper -> flee,
# diamond_ore -> mine_diamond, ...) duplicated what core/fsm.py already
# does directly and faster from the same YOLO detections, and its own
# EpisodicMemory duplicated (under a confusingly identical name) the
# real, actually-used core/episode_memory.EpisodeMemory. Real goal
# tracking lives in memory/goals.GoalStack; real episode memory lives in
# core/episode_memory.EpisodeMemory. This file now only does the one
# thing that ever fed back into a decision: the inner monologue.

import json
import os
import time

import config
from core.llm_router import route_llm_call

COGNITIVE_DIR = 'data/cognitive'
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


class InnerMonologue:
    """One self-generated thought, gated to fire an LLM call only every
    config.MONOLOGUE_EVERY_N_TICKS ticks (cheap haiku call, ~50 tokens) —
    between LLM ticks, update() is a no-op-cost template fallback so the
    thought log doesn't go stale. The most recent real thought is fed back
    into the next vision-LLM planning call as extra context."""

    FILE = f'{COGNITIVE_DIR}/inner_monologue.json'

    def __init__(self):
        self.thoughts = _load(self.FILE, [])
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
    """Thin wrapper around InnerMonologue, called once per tick with the
    same signals already flowing through the runtime loop (objects,
    action_dict, reward, goal, recent_episodes)."""

    def __init__(self):
        self.monologue = InnerMonologue()

    def update(self, tick: int, objects: list, action_dict: dict, reward: float,
               goal: str = None, recent_episodes: list = None):
        self.monologue.update(tick, objects, action_dict, reward,
                               goal=goal, recent_episodes=recent_episodes)
