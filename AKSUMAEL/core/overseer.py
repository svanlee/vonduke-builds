# core/overseer.py
"""
Aksūmal Overseer — local mesh-llm as the strategic brain.

Called every OVERSEER_INTERVAL ticks from runtime.py.
Receives a snapshot of current agent state, returns a high-level directive.
Runs in a background thread so the game loop is never blocked.

Directive schema:
  {"action": "continue"}                          — keep current goal
  {"action": "override_goal", "goal": "...", "reason": "..."}  — inject new goal
  {"action": "alert", "message": "..."}           — log/narrate something notable
  {"action": "flee", "reason": "..."}             — emergency, trigger FLEE
"""

import json
import re
import threading
import time
import config
from core.identity import AKSUMAEL_IDENTITY
from core.llm_router import call_claude_direct
from core.capture import push_monologue_line

OVERSEER_INTERVAL = 10        # ticks between overseer calls
# Text-only prompt (see _call_overseer — no frame is attached), so the
# 8-45s vision-call latency doesn't apply here. Still uses the shared
# config.LOCAL_LLM_TIMEOUT rather than a tighter value since this call runs
# on its own background thread (maybe_call) and never blocks the tick loop.
OVERSEER_TIMEOUT  = config.LOCAL_LLM_TIMEOUT

# Hard wall-clock cap, independent of OVERSEER_INTERVAL — the tick-count
# gate alone assumes a roughly steady tick rate, but tick duration in this
# codebase is variable (it can spike well above LOOP_INTERVAL_SEC), so
# OVERSEER_INTERVAL ticks can pass in well under a minute of wall time.
# This is the actual backstop against saturating the local mesh-llm server.
OVERSEER_MAX_PER_MINUTE = 6

_last_directive   = {"action": "continue"}
_last_called_tick = 0
_lock             = threading.Lock()
_thread           = None
_busy             = False
_call_timestamps  = []        # monotonic times of recent calls, for the per-minute cap


def get_last_directive() -> dict:
    with _lock:
        return dict(_last_directive)


def _build_prompt(snapshot: dict) -> str:
    """Build the overseer prompt from the current agent snapshot."""
    return f"""{AKSUMAEL_IDENTITY}
You are the Aksūmal Overseer — the strategic intelligence for an autonomous robot agent.

{snapshot.get('hardware', '')}

Current agent state:
- Environment: {snapshot.get('env', 'minecraft')}
- FSM state: {snapshot.get('fsm_state', 'UNKNOWN')}
- Ticks in this state: {snapshot.get('state_ticks', '?')}
- Current goal: {snapshot.get('current_goal', 'none')}
- Health: {snapshot.get('health_pct', '?')} | Hunger: {snapshot.get('hunger_pct', '?')}
- Position: {snapshot.get('position', 'unknown')}
- Facing: {snapshot.get('heading', 'unknown')}
- Objects detected: {snapshot.get('objects', [])}
- Last 5 actions: {snapshot.get('recent_actions', [])}
- Inventory summary: {snapshot.get('inventory', {})}
- Reward trend (last 10): {snapshot.get('reward_avg', '?')}
{snapshot.get('memory_context', '')}

Your job: assess whether the agent is making progress toward its goal or is stuck/misaligned. Return a JSON directive (no markdown, just JSON):

If the agent is making progress: {{"action": "continue"}}
If the agent needs a new goal: {{"action": "override_goal", "goal": "<goal_string>", "reason": "<why>"}}
If something notable should be logged: {{"action": "alert", "message": "<what you observe>"}}
If the agent is in danger (health < 0.3 or confirmed hostile mob): {{"action": "flee", "reason": "<why>"}}

Valid goal strings: find_and_chop_tree, mine_stone, mine_iron, mine_diamonds, craft_wood_pickaxe, craft_stone_pickaxe, craft_iron_pickaxe, explore, rebuild_fort, return_to_base

Respond with ONLY the JSON directive, nothing else."""


def _extract_directive(raw: str) -> dict | None:
    """Parse a directive out of a raw mesh-llm response. Handles markdown
    code fences (leading and/or trailing) and, failing a clean parse, falls
    back to pulling the first {...} object out of the text — mesh-llm's
    vision responses sometimes wrap the JSON in prose or truncate after it."""
    text = raw.strip()
    if text.startswith('```'):
        text = text[3:]
        if text.startswith('json'):
            text = text[4:]
        text = text.rsplit('```', 1)[0]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _call_overseer(tick: int, snapshot: dict):
    global _last_directive, _busy
    try:
        prompt = _build_prompt(snapshot)
        # Text-only, deliberately: mesh-llm's loaded vision model
        # (Qwen3.5-4B-Vision) has a hard-baked GUI/accessibility-tree
        # grounding behavior that fires on almost any image + JSON-request
        # combo, regardless of system-prompt instructions telling it not
        # to — confirmed live 2026-07-21, it returned
        # [{"label": "['StaticText']", "bbox": [...]}] for this exact
        # prompt with a frame attached (a prior attempt to suppress this
        # with a system message only got it to swap '[interactive]' for
        # '[StaticText]', see git history). The snapshot's `objects` field
        # already carries YOLO's detections as text, which is enough
        # visual grounding for a strategic go/no-go call — attaching the
        # raw frame added no information the model could reliably parse.
        raw = call_claude_direct(prompt, max_tokens=300,
                                  timeout=OVERSEER_TIMEOUT)
        if not raw:
            print(f'[Overseer] tick {tick} call failed — no response '
                  f'(check local mesh-llm server at {config.LOCAL_LLM_URL})')
            return
        directive = _extract_directive(raw)
        if directive is None:
            print(f'[OVERSEER] tick {tick} unparseable response, skipping: {raw!r}')
            return
        if 'action' not in directive:
            return
        with _lock:
            _last_directive = directive
        action = directive.get('action', 'continue')
        if action != 'continue':
            msg = directive.get('reason') or directive.get('message') or directive.get('goal', '')
            print(f'[OVERSEER] directive={action} | {msg}')
            push_monologue_line(f'[Overseer] {action}: {msg}' if msg else f'[Overseer] {action}')
    except Exception as e:
        print(f'[OVERSEER] error: {e}')
    finally:
        _busy = False


def maybe_call(tick: int, snapshot: dict):
    """Called every tick from runtime.py. Fires the overseer every OVERSEER_INTERVAL
    ticks, subject to a hard OVERSEER_MAX_PER_MINUTE wall-clock cap."""
    global _last_called_tick, _thread, _busy
    if tick - _last_called_tick < OVERSEER_INTERVAL:
        return
    if _busy:
        return
    now = time.monotonic()
    with _lock:
        global _call_timestamps
        _call_timestamps = [t for t in _call_timestamps if now - t < 60.0]
        if len(_call_timestamps) >= OVERSEER_MAX_PER_MINUTE:
            return
        _call_timestamps.append(now)
    _last_called_tick = tick
    _busy = True
    print(f'[Overseer] tick {tick} fired')
    _thread = threading.Thread(target=_call_overseer, args=(tick, snapshot), daemon=True)
    _thread.start()
