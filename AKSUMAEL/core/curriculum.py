# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.1.0 — Curriculum Generator               ║
# ║  Decides what AKSUMAEL should attempt next when the   ║
# ║  goal stack goes idle, biased toward the frontier of  ║
# ║  the tech tree and away from recently-failed goals.   ║
# ╚══════════════════════════════════════════════════════╝

import json
import os
import urllib.error
import urllib.request

import config
from core.planner import Planner

RETIRED_GOALS_LOG = os.path.join(config.MEMORY_DIR, 'retired_goals.jsonl')

_CURRICULUM_PROMPT = """You are the curriculum planner for AKSUMAEL, a Minecraft
automation agent. Given its current state, suggest exactly ONE short goal
string (snake_case, e.g. "mine_iron" or "build_shelter") for it to attempt
next. Rules:
- Must be achievable with the current inventory/tools.
- Should be at the frontier of capability — not trivial, not impossible.
- Prefer goals that unlock new tech-tree progress.
- Avoid these recently-failed goals: {avoided}

Inventory: {inventory}
World memory: {world_summary}

Respond with ONLY the goal string, nothing else."""


class CurriculumGenerator:
    def __init__(self, planner: Planner = None):
        self.planner = planner or Planner()
        self._last_run_tick = 0

    # ── Public API ───────────────────────────────────────────────
    def suggest_next_goal(self, inventory: dict, world=None,
                           skill_library: list = None, past_goals: list = None) -> str | None:
        """Return the next goal AKSUMAEL should attempt, or None if nothing
        useful can be determined. Deterministic tech-tree frontier first
        (free); falls back to one cheap LLM call only when the deterministic
        pick was recently tried and failed, or the tree is fully cleared."""
        avoided = set(self._recent_failed_goals())
        candidate = self.planner.next_achievable(inventory, world)

        if candidate is not None and candidate not in avoided:
            return candidate

        return self._ask_llm(inventory, world, avoided) or candidate

    def run_every_n_ticks(self, tick: int, goals, inventory, world, n: int = None) -> str | None:
        """Call once per runtime tick. Only actually does anything every `n`
        ticks (default config.CURRICULUM_INTERVAL_TICKS), and only when the
        goal stack looks idle (current goal is the standing 'explore' goal
        with nothing queued) — otherwise AKSUMAEL is already busy."""
        n = n or config.CURRICULUM_INTERVAL_TICKS
        if tick - self._last_run_tick < n:
            return None
        self._last_run_tick = tick

        if goals.current_goal() != 'explore' or len(goals.stack) > 0:
            return None

        items = getattr(inventory, 'items', inventory) or {}
        goal = self.suggest_next_goal(items, world)
        if goal:
            print(f'[CURRICULUM] goal stack idle — suggesting: {goal}')
            goals.push(goal)
        return goal

    # ── Internals ────────────────────────────────────────────────
    def _recent_failed_goals(self, n: int = 10) -> list:
        if not os.path.exists(RETIRED_GOALS_LOG):
            return []
        try:
            with open(RETIRED_GOALS_LOG) as f:
                lines = f.readlines()[-50:]
            failed = []
            for line in lines:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get('reason', '').startswith('timeout'):
                    failed.append(entry.get('goal'))
            return failed[-n:]
        except Exception as e:
            print(f'[CURRICULUM] failed-goal read error: {e}')
            return []

    def _ask_llm(self, inventory: dict, world, avoided: set) -> str | None:
        if not config.ANTHROPIC_API_KEY:
            return None
        world_summary = world.get_chunk_summary() if world is not None else 'unknown'
        inv_str = ', '.join(f'{k}:{v}' for k, v in list((inventory or {}).items())[:10]) or 'empty'
        prompt = _CURRICULUM_PROMPT.format(
            avoided=', '.join(avoided) or 'none',
            inventory=inv_str,
            world_summary=world_summary,
        )
        payload = json.dumps({
            'model': config.CLAUDE_MODEL,
            'max_tokens': 30,
            'messages': [{'role': 'user', 'content': prompt}],
        }).encode('utf-8')
        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': config.ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            text_block = next((b for b in data.get('content', []) if b.get('type') == 'text'), None)
            if text_block is None:
                return None
            goal = text_block['text'].strip().strip('."\'').lower().replace(' ', '_')
            return goal or None
        except urllib.error.HTTPError as e:
            print(f'[CURRICULUM] Claude HTTP {e.code}')
        except Exception as e:
            print(f'[CURRICULUM] suggestion error: {e}')
        return None
