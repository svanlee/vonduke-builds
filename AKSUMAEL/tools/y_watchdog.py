#!/usr/bin/env python3
"""
Y-level watchdog — runs indefinitely, polling data/world_memory.json every
30s. If the bot has fallen underground (Y < 55) it re-injects dig_up
(authority 4, so a transient eat/hunger interrupt doesn't silently drop it
— see the 2026-07-21 session notes on authority gating) and triggers a
debug snapshot. If Y is fine but the goal has drifted to something that
only makes sense underground, it redirects back to find_and_chop_tree.

Started via nohup, not systemd — kill by PID (see /tmp/aksumael_watchdog.pid)
or `pkill -f tools/y_watchdog.py`.
"""
import json
import os
import time
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
WORLD_MEM_PATH = REPO / 'data' / 'world_memory.json'
GOALS_PATH = REPO / 'data' / 'goals.json'
INJECTED_GOALS_PATH = REPO / 'data' / 'injected_goals.json'
SNAPSHOT_TRIGGER_PATH = REPO / 'data' / '.snapshot_trigger'
PID_PATH = pathlib.Path('/tmp/aksumael_watchdog.pid')

Y_THRESHOLD = 55
POLL_SEC = 30
UNDERGROUND_GOALS = {'dig_up', 'mine_up', 'escape_underground',
                      'mine_coal_ore', 'mine_diamond_ore'}


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _inject_goal(goal: str, reason: str, authority: int = 3):
    payload = {'queue': [{'goal': goal, 'reason': reason,
                           'received_at': int(time.time()), 'authority': authority}]}
    INJECTED_GOALS_PATH.write_text(json.dumps(payload))


def main():
    PID_PATH.write_text(str(os.getpid()))
    print(f'[WATCH] started pid={os.getpid()} threshold=Y<{Y_THRESHOLD} poll={POLL_SEC}s', flush=True)

    while True:
        world_mem = _read_json(WORLD_MEM_PATH)
        goals = _read_json(GOALS_PATH)
        y = (world_mem or {}).get('y_level', 99)
        current_goal = (goals or {}).get('current', '')

        print(f'[WATCH] Y={y} goal={current_goal}', flush=True)

        if isinstance(y, (int, float)) and y < Y_THRESHOLD:
            _inject_goal('dig_up', f'watchdog: Y={y}', authority=4)
            SNAPSHOT_TRIGGER_PATH.touch()
            print(f'[WATCH] Y={y} < {Y_THRESHOLD} — injected dig_up, snapshot triggered', flush=True)
        elif current_goal in UNDERGROUND_GOALS:
            _inject_goal('find_and_chop_tree', f'watchdog: Y={y} ok but goal={current_goal} drifted underground')
            print(f'[WATCH] Y={y} ok but goal={current_goal} drifted underground — injected find_and_chop_tree', flush=True)

        time.sleep(POLL_SEC)


if __name__ == '__main__':
    main()
