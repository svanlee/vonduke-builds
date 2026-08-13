"""
jarvis/planner.py — Autonomous planning loop for AKSUMAEL.

The planner runs on a separate thread from the overseer and implements
a full OODA (Observe → Orient → Decide → Act) cycle using ALL available
data sources. It's what makes AKSUMAEL proactively pursue goals rather
than waiting for voice input or system failures.

Architecture:
    JarvisPlanner (thread)
        ↓ every PLAN_INTERVAL seconds
        ↓ Observe: read bot state, AURORA, learn_log, skill performance, hive
        ↓ Orient: distill into situation summary
        ↓ Decide: LLM picks next best action (inject goal, switch domain, alert)
        ↓ Act: execute decision, record to AURORA

This is the difference between a reactive bot and an autonomous agent.
"""

from __future__ import annotations

import json
import pathlib
import threading
import time
from typing import Optional

BASE_DIR = pathlib.Path(__file__).parent.parent

# Planning cycle interval — every 3 minutes when stable
PLAN_INTERVAL = 180

# Minimum time between LLM plan calls (avoid token burn on stale state)
MIN_PLAN_INTERVAL = 120

PLAN_LOG = BASE_DIR / "data" / "planner.log"


def _log(msg: str):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    line = f"[{ts}] [PLANNER] {msg}"
    print(line)
    try:
        PLAN_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(PLAN_LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _observe() -> dict:
    """Collect a full situation snapshot from all data sources."""
    obs = {"ts": time.time()}

    # 1. Bot state
    try:
        state_path = BASE_DIR / "data" / "state.json"
        if state_path.exists():
            with open(state_path) as f:
                obs["bot_state"] = json.load(f)
    except Exception:
        obs["bot_state"] = {}

    # 2. Current goal
    try:
        goals_path = BASE_DIR / "data" / "goals.json"
        if goals_path.exists():
            with open(goals_path) as f:
                gdata = json.load(f)
            obs["current_goal"] = gdata.get("current") if isinstance(gdata, dict) else None
        else:
            obs["current_goal"] = None
    except Exception:
        obs["current_goal"] = None

    # 3. Recent learn_log (last 20 ticks)
    try:
        lpath = BASE_DIR / "data" / "learning" / "learn_log.jsonl"
        if lpath.exists():
            lines = lpath.read_text().strip().splitlines()[-20:]
            ticks = [json.loads(l) for l in lines if l]
            if ticks:
                rewards = [t.get("reward", 0) for t in ticks if t.get("reward") is not None]
                obs["recent_avg_reward"] = round(sum(rewards) / len(rewards), 3) if rewards else 0
                obs["total_ticks"] = ticks[-1].get("tick", 0) if ticks else 0
                obs["recent_goal"] = ticks[-1].get("goal") if ticks else None
    except Exception:
        pass

    # 4. AURORA episode count
    try:
        import sqlite3, os as _os
        conn = sqlite3.connect(_os.path.join("data", "aurora.db"))
        obs["aurora_episodes"] = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        # Best performing goal from vault
        rows = conn.execute(
            "SELECT name, attributes FROM entities WHERE type='goal' ORDER BY last_seen DESC LIMIT 10"
        ).fetchall()
        best_goal = None
        best_reward = -999
        for name, attrs_json in rows:
            try:
                attrs = json.loads(attrs_json or "{}")
                r = attrs.get("avg_reward")
                if r is not None and r > best_reward:
                    best_reward, best_goal = r, name
            except Exception:
                pass
        obs["best_goal_by_reward"] = best_goal
        obs["best_goal_reward"] = best_reward if best_goal else None
        conn.close()
    except Exception:
        pass

    # 5. Preference pairs count
    try:
        pref_path = BASE_DIR / "data" / "learning" / "preferences.jsonl"
        if pref_path.exists():
            obs["preference_pairs"] = sum(1 for _ in pref_path.open())
    except Exception:
        pass

    # 6. Training domain data (scan day_*.md recency)
    try:
        training_dir = BASE_DIR / "data" / "training"
        if training_dir.exists():
            md_files = list(training_dir.glob("day_*.md"))
            obs["training_domain_files"] = len(md_files)
            if md_files:
                newest = max(md_files, key=lambda f: f.stat().st_mtime)
                obs["newest_training_domain"] = newest.stem
    except Exception:
        pass

    # 7. LoRA model suggestion (fast local pre-screen if adapter exists)
    try:
        lora_path = BASE_DIR / "data" / "learning" / "lora_adapter"
        if lora_path.exists():
            current_goal = obs.get("current_goal") or obs.get("recent_goal") or "explore"
            belief_str = json.dumps(obs.get("bot_state", {}))[:200]
            from memory.lora_trainer import infer as _lora_infer
            suggestion = _lora_infer(current_goal, belief=belief_str, max_new_tokens=40)
            if suggestion and not suggestion.startswith("[infer error"):
                obs["lora_suggestion"] = suggestion.strip()[:100]
    except Exception:
        pass  # LoRA not available yet — that's fine

    # 8. LoRA training status
    try:
        lora_stats_path = BASE_DIR / "data" / "learning" / "lora_stats.json"
        if lora_stats_path.exists():
            stats = json.loads(lora_stats_path.read_text())
            obs["lora_trained_at"] = stats.get("trained_at")
            obs["lora_n_pairs"] = stats.get("n_pairs")
    except Exception:
        pass

    return obs


def _orient(obs: dict) -> str:
    """Format observation into a compact situation summary for the LLM."""
    parts = [
        f"tick={obs.get('total_ticks', '?')}",
        f"goal={obs.get('current_goal', 'none')}",
        f"recent_reward={obs.get('recent_avg_reward', '?')}",
        f"aurora_episodes={obs.get('aurora_episodes', 0)}",
        f"best_known_goal={obs.get('best_goal_by_reward')}({obs.get('best_goal_reward')})",
        f"preference_pairs={obs.get('preference_pairs', 0)}",
        f"training_domains={obs.get('training_domain_files', 0)}",
    ]
    if obs.get("lora_suggestion"):
        parts.append(f"lora_suggests={obs['lora_suggestion']}")
    if obs.get("lora_trained_at"):
        parts.append(f"lora_trained={obs['lora_trained_at']}(n={obs.get('lora_n_pairs')})")
    return " | ".join(parts)


class JarvisPlanner(threading.Thread):
    """
    OODA planning loop: Observe → Orient → Decide → Act, every 3 minutes.

    The planner is separate from the overseer (which reacts to events).
    The planner proactively decides what to do next based on the full
    system state, without waiting for something to go wrong.
    """

    def __init__(self, speak_fn=None):
        super().__init__(name="JarvisPlanner", daemon=True)
        self._stop_event = threading.Event()
        self._speak = speak_fn
        self._brain = None
        self._last_plan_ts: float = 0.0
        self._plan_count: int = 0

    def _get_brain(self):
        if self._brain is None:
            from jarvis.brain import get_brain
            self._brain = get_brain()
        return self._brain

    def _plan(self):
        """One OODA cycle."""
        now = time.time()
        if now - self._last_plan_ts < MIN_PLAN_INTERVAL:
            return  # too soon

        obs = _observe()
        situation = _orient(obs)
        self._plan_count += 1

        lora_hint = (
            f" Local LoRA model suggests: '{obs.get('lora_suggestion')}' — consider if it aligns with best_known_goal."
            if obs.get("lora_suggestion") else ""
        )
        prompt = (
            f"[JARVIS PLANNER — OODA cycle #{self._plan_count}] "
            f"Situation: {situation}.{lora_hint} "
            f"You are the autonomous decision-maker for AKSUMAEL. "
            f"Based on this state and your AURORA memory, decide ONE action to take: "
            f"(a) inject a specific goal if the current goal is underperforming, "
            f"(b) activate a high-reward skill from list_skills, "
            f"(c) build_preferences if pairs < 100, "
            f"(d) switch_domain if one domain is overused, or "
            f"(e) run self_eval and report findings. "
            f"Pick the highest-impact action, execute it with the right tool, "
            f"then respond in ONE sentence describing what you did and why."
        )

        response = None
        try:
            brain = self._get_brain()
            response = brain.respond(prompt)
            if response:
                _log(f"plan #{self._plan_count}: {response}")
        except Exception as e:
            _log(f"plan LLM error: {e}")

        # Record to AURORA
        try:
            from memory import aurora_memory
            aurora_memory.record(
                env="planner",
                action=f"ooda_cycle_{self._plan_count}: {situation[:200]}",
                outcome=(response or "no_response")[:300],
                metadata={"plan_count": self._plan_count, "obs": obs},
            )
        except Exception as _ae:
            _log(f"aurora record error: {_ae}")

        self._last_plan_ts = now

    def run(self):
        _log("planner started")
        # Wait 90s for bot to stabilize, then start planning
        self._stop_event.wait(90)
        while not self._stop_event.is_set():
            try:
                self._plan()
            except Exception as e:
                _log(f"planner tick error: {e}")
            self._stop_event.wait(PLAN_INTERVAL)
        _log("planner stopped")

    def stop(self):
        self._stop_event.set()


# ── Singleton ─────────────────────────────────────────────────────────────────

_planner: Optional[JarvisPlanner] = None


def get_planner(speak_fn=None) -> JarvisPlanner:
    global _planner
    if _planner is None:
        _planner = JarvisPlanner(speak_fn=speak_fn)
    return _planner


def start_planner(speak_fn=None) -> JarvisPlanner:
    """Start the planner if not already running. Call once at bot startup."""
    p = get_planner(speak_fn=speak_fn)
    if not p.is_alive():
        p.start()
        _log("planner thread launched")
    return p
