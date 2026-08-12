# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Learning Loop Logger                      ║
# ║  Logs (belief_state, action, reward) tuples to disk  ║
# ║  for offline analysis and eventual policy training.  ║
# ╚══════════════════════════════════════════════════════╝
#
# Every action AKSUMAEL takes is now paired with its outcome (reward).
# These tuples are the raw material for learning — without them, the
# agent can never improve from experience. This module writes them to
# data/learning/learn_log.jsonl (one JSON object per line, appendable).
#
# Schema per record:
#   ts          — Unix timestamp
#   tick        — loop tick counter
#   goal        — active goal string at time of action
#   belief      — compact belief snapshot (detected objects, avg reward,
#                 inventory summary, position if known)
#   action      — the action_dict executed this tick
#   reward      — scalar reward computed after the action
#   reward_avg  — rolling average reward
#   source      — who chose the action: 'fsm', 'llm', 'skill', 'neural'
#
# The file is rotated when it exceeds MAX_LOG_MB. Old logs are renamed
# learn_log.{n}.jsonl so they remain available for analysis.

from __future__ import annotations

import json
import os
import time

LOG_PATH    = 'data/learning/learn_log.jsonl'
MAX_LOG_MB  = 50
LOG_EVERY_N = 5    # write every N ticks (not every tick — too noisy)

_tick_counter = 0


def _rotate():
    if not os.path.exists(LOG_PATH):
        return
    size_mb = os.path.getsize(LOG_PATH) / (1024 * 1024)
    if size_mb < MAX_LOG_MB:
        return
    # Find next available rotation index
    n = 1
    while os.path.exists(f'{LOG_PATH}.{n}'):
        n += 1
    os.rename(LOG_PATH, f'{LOG_PATH}.{n}')
    print(f'[LEARN_LOG] rotated to {LOG_PATH}.{n} ({size_mb:.1f} MB)')


def log_step(tick: int, goal: str, objects: list, action_dict: dict,
             reward: float, reward_avg: float,
             inventory: dict | None = None,
             pos: tuple | None = None,
             source: str = 'unknown') -> None:
    """Append one (belief, action, reward) tuple to the learning log.

    Designed to be called every LOG_EVERY_N ticks from the main loop
    after reward.compute() — see core/runtime.py. All arguments are
    already available at that point; no extra computation required.

    This is deliberately append-only and fire-and-forget: if the write
    fails (disk full, permissions) it prints once and moves on rather
    than crashing the tick loop."""
    global _tick_counter
    _tick_counter += 1
    if _tick_counter % LOG_EVERY_N != 0:
        return

    # Compact belief snapshot — just what's needed for offline analysis
    detected = sorted({o.get('label') for o in objects if o.get('label')})
    belief = {
        'goal':      goal or 'unknown',
        'detected':  detected,
        'reward_avg': round(reward_avg, 4),
    }
    if inventory:
        # Only non-zero items — keeps records small
        belief['inventory'] = {k: v for k, v in inventory.items() if v}
    if pos:
        belief['pos'] = pos

    record = {
        'ts':         round(time.time(), 2),
        'tick':       tick,
        'goal':       goal or 'unknown',
        'belief':     belief,
        'action':     action_dict,
        'reward':     round(reward, 4),
        'reward_avg': round(reward_avg, 4),
        'source':     source,
    }

    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        _rotate()
        with open(LOG_PATH, 'a') as f:
            f.write(json.dumps(record) + '\n')
    except Exception as e:
        print(f'[LEARN_LOG] write error: {e}')


def recent_outcomes(goal: str, n: int = 20) -> list[dict]:
    """Read the last n records for a given goal from the log.
    Used by the reflection gate to check if an action pattern has been
    consistently failing before committing to it again."""
    if not os.path.exists(LOG_PATH):
        return []
    matches = []
    try:
        with open(LOG_PATH) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if rec.get('goal') == goal:
                        matches.append(rec)
                except Exception:
                    continue
    except Exception:
        return []
    return matches[-n:]


def goal_avg_reward(goal: str, n: int = 50) -> float | None:
    """Average reward over the last n records for a goal.
    Returns None if no data exists yet."""
    recs = recent_outcomes(goal, n)
    if not recs:
        return None
    return sum(r['reward'] for r in recs) / len(recs)
