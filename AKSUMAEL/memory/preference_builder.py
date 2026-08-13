"""
memory/preference_builder.py — Build DPO/preference training data from learn_log.

Reads learn_log.jsonl and pairs high-reward ticks with low-reward ticks for the
same goal, producing a preference dataset suitable for Direct Preference Optimization
or reward model training.

Usage:
    python3 -m memory.preference_builder          # writes to data/learning/preferences.jsonl
    python3 -m memory.preference_builder --stats  # print summary only
"""

import json
import pathlib
import sys
from collections import defaultdict

LEARN_LOG = pathlib.Path("data/learning/learn_log.jsonl")
PREF_OUT   = pathlib.Path("data/learning/preferences.jsonl")

# Reward thresholds for chosen/rejected pairing (absolute mode)
CHOSEN_MIN   = 0.35   # ticks with reward >= this are "chosen"
REJECTED_MAX = 0.10   # ticks with reward <= this are "rejected"

# Minimum pairs per goal in percentile mode
PERCENTILE_QUANTILE = 0.25  # top/bottom 25% of rewards per goal
MIN_REWARD_GAP = 0.05       # minimum gap between chosen and rejected reward


def load_ticks(path: pathlib.Path) -> list[dict]:
    ticks = []
    if not path.exists():
        return ticks
    for line in path.read_text().strip().splitlines():
        try:
            ticks.append(json.loads(line))
        except Exception:
            pass
    return ticks


def _ticks_to_entries(ticks: list[dict]) -> dict:
    """Group ticks by goal, return dict of goal → list of (reward, entry)."""
    by_goal: dict = defaultdict(list)
    for t in ticks:
        goal = t.get("goal", "unknown")
        reward = t.get("reward")
        if reward is None:
            continue
        by_goal[goal].append({
            "goal": goal,
            "belief": t.get("belief", {}),
            "action": t.get("action", {}),
            "reward": reward,
            "tick": t.get("tick"),
        })
    return by_goal


def build_preferences(ticks: list[dict]) -> list[dict]:
    """Pair high-reward (chosen) with low-reward (rejected) ticks per goal.

    Primary mode: absolute thresholds (CHOSEN_MIN / REJECTED_MAX).
    Fallback mode: if fewer than 20 total pairs, use per-goal percentile
    thresholds (top/bottom quartile) for richer training signal.
    """
    by_goal = _ticks_to_entries(ticks)

    def _make_pairs(chosen_list, rejected_list, cap=200):
        if not chosen_list or not rejected_list:
            return []
        n = min(len(chosen_list), len(rejected_list), cap)
        return [
            {
                "goal": chosen_list[i]["goal"],
                "chosen": chosen_list[i],
                "rejected": rejected_list[i % len(rejected_list)],
                "reward_gap": round(
                    chosen_list[i]["reward"] - rejected_list[i % len(rejected_list)]["reward"], 3
                ),
            }
            for i in range(n)
        ]

    # --- Absolute threshold mode ---
    abs_pairs = []
    for goal, entries in by_goal.items():
        chosen  = [e for e in entries if e["reward"] >= CHOSEN_MIN]
        rejected = [e for e in entries if e["reward"] <= REJECTED_MAX]
        abs_pairs.extend(_make_pairs(chosen, rejected))

    if len(abs_pairs) >= 20:
        return abs_pairs

    # --- Percentile fallback: top/bottom quartile per goal ---
    pct_pairs = list(abs_pairs)  # keep any absolute pairs we found
    seen_goals = {p["goal"] for p in pct_pairs}

    for goal, entries in by_goal.items():
        if len(entries) < 4:
            continue
        rewards = sorted(e["reward"] for e in entries)
        n = len(rewards)
        q_low  = rewards[int(n * PERCENTILE_QUANTILE)]
        q_high = rewards[int(n * (1 - PERCENTILE_QUANTILE))]
        if q_high - q_low < MIN_REWARD_GAP:
            continue  # not enough contrast in this goal's data
        chosen   = [e for e in entries if e["reward"] >= q_high]
        rejected = [e for e in entries if e["reward"] <= q_low]
        new_pairs = _make_pairs(chosen, rejected, cap=50)
        # Filter out duplicates for goals already covered by absolute mode
        if goal in seen_goals:
            continue
        pct_pairs.extend(new_pairs)
        seen_goals.add(goal)

    return pct_pairs


def build_and_save(stats_only: bool = False) -> dict:
    ticks = load_ticks(LEARN_LOG)
    if not ticks:
        return {"error": "learn_log.jsonl not found or empty"}

    pairs = build_preferences(ticks)

    summary = {
        "total_ticks": len(ticks),
        "preference_pairs": len(pairs),
        "goals_covered": len({p["goal"] for p in pairs}),
    }

    if not stats_only and pairs:
        PREF_OUT.parent.mkdir(parents=True, exist_ok=True)
        with open(PREF_OUT, "w") as f:
            for p in pairs:
                f.write(json.dumps(p) + "\n")
        summary["written_to"] = str(PREF_OUT)
        print(f"[PREF] wrote {len(pairs)} preference pairs → {PREF_OUT}")

    return summary


if __name__ == "__main__":
    stats_only = "--stats" in sys.argv
    result = build_and_save(stats_only=stats_only)
    print(json.dumps(result, indent=2))
