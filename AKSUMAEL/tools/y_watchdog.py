#!/usr/bin/env python3
"""
AKSUMAEL Health Watchdog — runs every 30s, handles:

  1. Y-level rescue: if Y < 55 inject dig_up (authority 4)
  2. Goal-loop detection: if same goal dominates for > MAX_GOAL_STUCK_MINUTES
     without Y moving, break the loop with a fallback goal
  3. Overseer self-edit approval: periodically approve pending self-edits
     that live in data/self_edits/pending/ (safe ones auto-applied by
     self_editor.apply_patch; core edits get overseer sign-off here)
  4. Underground goal drift: redirect to find_and_chop_tree if Y ok but
     goal is underground-only

Started via nohup or systemd — kill by PID (/tmp/aksumael_watchdog.pid)
or `pkill -f tools/y_watchdog.py`.
"""
import json
import os
import pathlib
import sys
import time
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

WORLD_MEM_PATH       = REPO / 'data' / 'world_memory.json'
GOALS_PATH           = REPO / 'data' / 'goals.json'
INJECTED_GOALS_PATH  = REPO / 'data' / 'injected_goals.json'
SNAPSHOT_TRIGGER     = REPO / 'data' / '.snapshot_trigger'
PENDING_EDITS_DIR    = REPO / 'data' / 'self_edits' / 'pending'
PID_PATH             = pathlib.Path('/tmp/aksumael_watchdog.pid')

Y_THRESHOLD           = 55
POLL_SEC              = 30
MAX_GOAL_STUCK_POLLS  = 10   # ~5 min at 30s — if goal hasn't changed AND Y hasn't moved
OVERSEER_EDIT_EVERY   = 20   # polls (~10 min) between checking pending self-edits
WM_STALE_THRESHOLD    = 45   # s — if world_memory is older than this, skip Y-rescue
                              # (bot is blocked in a long code skill; Y value is stale)
                              # Set to 45s (< 1 watchdog poll) so any blocking code skill
                              # immediately stales the value and suppresses injection.
DIG_UP_COOLDOWN_SEC   = 300  # s — min gap between consecutive dig_up injections.
                              # Prevents the stack from accumulating dig_ups while the
                              # pillar-up code skill runs (185s) — one injection is enough.

UNDERGROUND_GOALS = {'dig_up', 'mine_up', 'escape_underground',
                     'mine_coal_ore', 'mine_diamond_ore'}

FALLBACK_GOALS = ['find_and_chop_tree', 'explore', 'sleep']


# ── helpers ───────────────────────────────────────────────────────────────────

def _read_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _inject_goal(goal: str, reason: str, authority: int = 3):
    payload = {'queue': [{'goal': goal, 'reason': reason,
                          'received_at': int(time.time()), 'authority': authority}]}
    INJECTED_GOALS_PATH.write_text(json.dumps(payload))
    print(f'[WATCH] → injected goal={goal!r} authority={authority} reason={reason}', flush=True)


def _approve_pending_edits():
    """Apply any pending self-edits that have been queued for overseer review.
    Only runs every OVERSEER_EDIT_EVERY polls — expensive because it imports
    self_editor and reads source files."""
    if not PENDING_EDITS_DIR.exists():
        return
    pending = sorted(PENDING_EDITS_DIR.glob('*.json'))
    if not pending:
        return
    try:
        from core import self_editor
        count = self_editor.apply_pending(overseer_approved=True)
        if count:
            print(f'[WATCH] overseer approved {count} pending self-edit(s)', flush=True)
    except Exception as e:
        print(f'[WATCH] self_editor approval error: {e}', flush=True)


# ── main loop ─────────────────────────────────────────────────────────────────

def main():
    PID_PATH.write_text(str(os.getpid()))
    print(f'[WATCH] started pid={os.getpid()} Y_threshold={Y_THRESHOLD} poll={POLL_SEC}s',
          flush=True)

    # Stuck-goal tracking
    goal_history: list[str] = []    # ring buffer of recent goals (len = MAX_GOAL_STUCK_POLLS)
    y_history:    list[float] = []  # parallel Y ring buffer

    poll_n = 0
    _last_dig_up_inject = 0.0  # epoch of most recent dig_up injection

    while True:
        world_mem    = _read_json(WORLD_MEM_PATH) or {}
        goals        = _read_json(GOALS_PATH) or {}
        y            = world_mem.get('y_level', 99)
        current_goal = goals.get('current', 'idle')

        print(f'[WATCH] poll={poll_n} Y={y} goal={current_goal}', flush=True)

        # ── 1. Y-rescue ─────────────────────────────────────────────────────
        wm_age = time.time() - (WORLD_MEM_PATH.stat().st_mtime
                                if WORLD_MEM_PATH.exists() else 0)
        if isinstance(y, (int, float)) and y < Y_THRESHOLD:
            if y < 5:
                # OCR misread (e.g. "63" parsed as "3") — bedrock-level Y impossible
                # for a standing bot; skip injection entirely to avoid false dig_up
                print(f'[WATCH] Y={y} looks like OCR error (< 5) — skipping dig_up',
                      flush=True)
            elif wm_age > WM_STALE_THRESHOLD:
                print(f'[WATCH] Y={y} < {Y_THRESHOLD} but world_memory is '
                      f'{wm_age:.0f}s stale (bot in long code skill) — skipping',
                      flush=True)
            else:
                _now = time.time()
                _since = _now - _last_dig_up_inject
                if _since < DIG_UP_COOLDOWN_SEC:
                    print(f'[WATCH] Y={y} < {Y_THRESHOLD} but dig_up injected '
                          f'{_since:.0f}s ago (cooldown {DIG_UP_COOLDOWN_SEC}s) — skipping',
                          flush=True)
                else:
                    _inject_goal('dig_up', f'watchdog: Y={y} < {Y_THRESHOLD}', authority=4)
                    SNAPSHOT_TRIGGER.touch()
                    _last_dig_up_inject = _now

        # ── 2. Underground goal drift ────────────────────────────────────────
        elif current_goal in UNDERGROUND_GOALS:
            _inject_goal('find_and_chop_tree',
                         f'watchdog: Y={y} ok but goal={current_goal}', authority=3)

        # ── 3. Stuck-goal detection ──────────────────────────────────────────
        # Build ring buffers
        goal_history.append(current_goal)
        y_history.append(float(y) if isinstance(y, (int, float)) else 99.0)
        if len(goal_history) > MAX_GOAL_STUCK_POLLS:
            goal_history.pop(0)
            y_history.pop(0)

        if len(goal_history) >= MAX_GOAL_STUCK_POLLS:
            dominant_goal, freq = Counter(goal_history).most_common(1)[0]
            y_range = max(y_history) - min(y_history)
            if freq >= MAX_GOAL_STUCK_POLLS - 1 and y_range < 3:
                # Same goal for nearly all recent polls AND Y hasn't moved → stuck
                fallback = next((g for g in FALLBACK_GOALS if g != dominant_goal),
                                'find_and_chop_tree')
                _inject_goal(fallback,
                             f'watchdog: goal={dominant_goal!r} stuck '
                             f'{freq}/{MAX_GOAL_STUCK_POLLS} polls y_range={y_range:.1f}',
                             authority=3)
                # Reset history so we don't immediately trigger again
                goal_history.clear()
                y_history.clear()

        # ── 4. Overseer self-edit approval ──────────────────────────────────
        if poll_n % OVERSEER_EDIT_EVERY == 0 and poll_n > 0:
            _approve_pending_edits()

        poll_n += 1
        time.sleep(POLL_SEC)


if __name__ == '__main__':
    main()
