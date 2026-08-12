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

# Reward thresholds for chosen/rejected pairing
CHOSEN_MIN   = 0.35   # ticks with reward >= this are "chosen"
REJECTED_MAX = 0.10   # ticks with reward <= this are "rejected"


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


def build_preferences(ticks: list[dict]) -> list[dict]:
    """Pair high-reward (chosen) with low-reward (rejected) ticks per goal."""
    by_goal: dict[str, dict[str, list]] = defaultdict(lambda: {"chosen": [], "rejected": []})

    for t in ticks:
        goal = t.get("goal", "unknown")
        reward = t.get("reward")
        belief = t.get("belief", {})
        action = t.get("action", {})
        if reward is None:
            continue
        entry = {
            "goal": goal,
            "belief": belief,
            "action": action,
            "reward": reward,
            "tick": t.get("tick"),
        }
        if reward >= CHOSEN_MIN:
            by_goal[goal]["chosen"].append(entry)
        elif reward <= REJECTED_MAX:
            by_goal[goal]["rejected"].append(entry)

    pairs = []
    for goal, groups in by_goal.items():
        chosen_list  = groups["chosen"]
        rejected_list = groups["rejected"]
        if not chosen_list or not rejected_list:
            continue
        # Pair up (min of both lists), round-robin
        n = min(len(chosen_list), len(rejected_list), 200)  # cap per goal
        for i in range(n):
            pairs.append({
                "goal": goal,
                "chosen": chosen_list[i],
                "rejected": rejected_list[i % len(rejected_list)],
                "reward_gap": round(chosen_list[i]["reward"] - rejected_list[i % len(rejected_list)]["reward"], 3),
            })
    return pairs


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
