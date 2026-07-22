# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.2.0 — LLM-Generated Code Skills          ║
# ║  Upgrades failing JSON key-sequences into Python       ║
# ║  functions that reason from what the bot SEES, not     ║
# ║  just button replays. Includes visual context (YOLO),  ║
# ║  world state, goal, and semantic helper functions.     ║
# ╚══════════════════════════════════════════════════════╝
#
# SAFETY NOTE: this executes LLM-generated Python. Gated behind
# config.ENABLE_CODE_SKILLS. Generated code runs in a restricted
# namespace — no imports, no file access, no subprocess.

import json
import os
import re
import time

import config
from core.llm_router import route_llm_call

# ── Prompt ────────────────────────────────────────────────────────────────────

CODE_SKILL_PROMPT = """You are writing a Python skill function for AKSUMAEL, an
autonomous Minecraft agent. AKSUMAEL learns by observing what it sees, not just
by replaying button presses.

## Skill to implement
Name: {name}
Goal when this skill fires: {goal}
Prior button-sequence that was attempted (for reference): {steps}

## What AKSUMAEL currently sees (YOLO detections)
{detections}

## World state
Y level: {y_level}
Position: {position}
Facing: {facing}

## Your job
Write ONE Python function with this EXACT signature:

    def run_skill(executor, world_model, objects, goal, h):
        ...
        return True   # success; False on failure

The function should achieve '{name}' by reasoning from the detections and world
state above, NOT just replaying the prior button sequence. Use the helper
functions in `h` (see below) to operate at a semantic level.

## Helper functions available via `h`
- `h.find(label)` → first detection dict matching label, or None
         detection = {{'label': str, 'conf': float, 'box': [x1,y1,x2,y2]}}
- `h.aim_at(detection)` → moves camera to center the detection's bounding box
- `h.mine(ticks=6)` → holds left-click for `ticks` × ~450ms (breaks blocks)
- `h.place()` → right-clicks once (places held block)
- `h.jump()` → presses space once
- `h.select_slot(n)` → presses hotbar key n (1-9)
- `h.look_down()` → aims camera straight down (+90 pitch)
- `h.look_up()` → aims camera straight up (−90 pitch)
- `h.look_level()` → resets pitch to horizontal
- `h.key(k)` → presses key k (e.g. 'w', 's', 'e')
- `h.wait(seconds)` → sleeps (keep under 2s per call)

## Rules
- Do NOT import anything.
- Do NOT use open/exec/eval/os/sys/subprocess/socket/__import__.
- No dunder attributes (__class__, __dict__, etc.).
- Keep under 60 lines.
- Return True only if the skill plausibly succeeded.
- Return ONLY the function definition — no explanation, no markdown fences.

## Example (find_and_chop_tree)
    def run_skill(executor, world_model, objects, goal, h):
        tree = h.find('oak_log') or h.find('birch_log') or h.find('spruce_log')
        if not tree:
            return False
        for _ in range(4):          # mine up to 4 logs
            tree = h.find('oak_log') or h.find('birch_log') or h.find('spruce_log')
            if not tree:
                break
            h.aim_at(tree)
            h.wait(0.2)
            h.mine(ticks=8)         # ~3.6s hold — enough for bare-hands
        return True
"""


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith('```'):
        lines = text.split('\n')
        lines = lines[1:] if lines[0].startswith('```') else lines
        if lines and lines[-1].strip().startswith('```'):
            lines = lines[:-1]
        text = '\n'.join(lines)
    return text.strip()


def generate_code_skill(skill_name: str, steps: list, context: str = '',
                        objects: list = None, goal: str = '',
                        world_state: dict = None) -> str | None:
    """Ask the LLM to write a Python function implementing this skill.
    Now includes visual context (YOLO detections) and world state so the
    generated code can reason from what the bot sees, not just replay inputs.
    Returns function source as a string, or None on failure."""
    if not config.LOCAL_LLM_ENABLED:
        return None

    objects = objects or []
    world_state = world_state or {}

    # Format detections readably
    if objects:
        det_lines = [
            f"  {d.get('label','?')} (conf={d.get('conf',0):.2f}) at box={d.get('box','?')}"
            for d in objects[:12]  # cap at 12 to stay in token budget
        ]
        detections_str = '\n'.join(det_lines) or '  (none)'
    else:
        detections_str = '  (none visible)'

    prompt = CODE_SKILL_PROMPT.format(
        name=skill_name,
        goal=goal or context or 'unknown',
        steps=json.dumps(steps, default=str)[:600],
        detections=detections_str,
        y_level=world_state.get('y_level', '?'),
        position=world_state.get('position', '?'),
        facing=world_state.get('facing', '?'),
    )

    raw, _provider = route_llm_call(prompt, max_tokens=4096, timeout=90)
    if not raw:
        print('[CODE_SKILL] all LLM tiers failed')
        return None

    code = _strip_fences(raw)
    if 'def run_skill(' not in code:
        print('[CODE_SKILL] LLM response missing run_skill() — discarding')
        return None
    if not _is_safe_source(code):
        print('[CODE_SKILL] generated code failed safety check — discarding')
        return None
    return code


# ── Safety ────────────────────────────────────────────────────────────────────

_FORBIDDEN_PATTERNS = re.compile(
    r'\b(import|open|exec|eval|__import__|os\.|sys\.|subprocess|socket|globals|locals|getattr|setattr)\b'
)
_DUNDER_PATTERN = re.compile(r'__\w+__')


def _is_safe_source(code: str) -> bool:
    if _DUNDER_PATTERN.search(code):
        return False
    return not _FORBIDDEN_PATTERNS.search(code)


# ── Persistence ───────────────────────────────────────────────────────────────

def save_code_skill(skill_name: str, code: str) -> str:
    os.makedirs(config.CODE_SKILLS_DIR, exist_ok=True)
    safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in skill_name)
    path = os.path.join(config.CODE_SKILLS_DIR, f'{safe}.py')
    with open(path, 'w') as f:
        f.write(code)
    return path


# ── Sandbox helpers ───────────────────────────────────────────────────────────

class _SkillHelpers:
    """Semantic helper API exposed to generated code skills via the `h` param.
    Operates at task level (aim at detection, mine, place) rather than raw
    executor.execute() calls so skills can reason from what the bot sees."""

    def __init__(self, executor, screen_w: int = 1920, screen_h: int = 1080):
        self._ex = executor
        self._sw = screen_w
        self._sh = screen_h

    def find(self, label: str, objects: list = None) -> dict | None:
        if objects is None:
            return None
        label_l = label.lower()
        return next(
            (d for d in objects if label_l in str(d.get('label', '')).lower()),
            None
        )

    def aim_at(self, detection: dict):
        """Move camera to center a YOLO bounding box."""
        box = detection.get('box', [])
        if len(box) < 4:
            return
        cx = (box[0] + box[2]) / 2 / self._sw * 100
        cy = (box[1] + box[3]) / 2 / self._sh * 100
        self._ex.execute({'key': None, 'click': None,
                          'look': {'dx': int((cx - 50) * 3),
                                   'dy': int((cy - 50) * 3)},
                          'source': 'code_skill'})
        time.sleep(0.15)

    def mine(self, ticks: int = 6):
        """Hold left-click for ticks × MINE_HOLD_MS — uses mouse_hold for sustained breaking.
        delay_ms=20 overrides KEY_HOLD_MS so executor doesn't double-sleep."""
        hold_ms = getattr(config, 'MINE_HOLD_MS', 450)
        self._ex.execute({'key': None, 'mouse_hold': 'down',
                          'mouse_button_name': 'left', 'delay_ms': 20,
                          'source': 'code_skill'})
        time.sleep((hold_ms * ticks) / 1000.0)
        self._ex.execute({'key': None, 'mouse_hold': 'up',
                          'mouse_button_name': 'left', 'delay_ms': 20,
                          'source': 'code_skill'})

    def place(self):
        self._ex.execute({'key': None, 'mouse_button': 'right',
                          'source': 'code_skill'})

    def jump(self):
        self._ex.execute({'key': 'space', 'click': None,
                          'delay_ms': 20, 'source': 'code_skill'})
        time.sleep(0.05)

    def select_slot(self, n: int):
        self._ex.execute({'key': str(n), 'click': None,
                          'delay_ms': 50, 'source': 'code_skill'})
        time.sleep(0.05)

    def look_down(self):
        self._ex.execute({'key': None, 'look': {'dx': 0, 'dy': 1000},
                          'delay_ms': 20, 'source': 'code_skill'})
        time.sleep(0.05)

    def look_up(self):
        self._ex.execute({'key': None, 'look': {'dx': 0, 'dy': -1000},
                          'delay_ms': 20, 'source': 'code_skill'})
        time.sleep(0.05)

    def look_level(self):
        self._ex.execute({'key': None, 'look': {'dx': 0, 'dy': 0},
                          'delay_ms': 20, 'source': 'code_skill'})

    def key(self, k: str):
        self._ex.execute({'key': k, 'click': None,
                          'delay_ms': 20, 'source': 'code_skill'})
        time.sleep(0.05)

    def wait(self, seconds: float):
        time.sleep(min(seconds, 2.0))


# ── Restricted builtins ───────────────────────────────────────────────────────

_RESTRICTED_BUILTINS = {
    'range': range, 'len': len, 'min': min, 'max': max, 'abs': abs,
    'enumerate': enumerate, 'zip': zip, 'sorted': sorted, 'list': list,
    'dict': dict, 'set': set, 'tuple': tuple, 'str': str, 'int': int,
    'float': float, 'bool': bool, 'print': print, 'True': True, 'False': False,
    'None': None,
}


def load_code_skill(skill_name: str):
    """Load a saved code skill and return its run_skill function.
    Returns None if file doesn't exist, fails safety check, or errors."""
    if not config.ENABLE_CODE_SKILLS:
        return None
    safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in skill_name)
    path = os.path.join(config.CODE_SKILLS_DIR, f'{safe}.py')
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            code = f.read()
        if not _is_safe_source(code):
            print(f'[CODE_SKILL] {skill_name} failed safety check on load — skipping')
            return None
        namespace = {'__builtins__': _RESTRICTED_BUILTINS, 'time': time}
        exec(compile(code, f'<code_skill:{skill_name}>', 'exec'), namespace)
        return namespace.get('run_skill')
    except Exception as e:
        print(f'[CODE_SKILL] load error for {skill_name}: {e}')
        return None


def run_code_skill(skill_name: str, executor, world_model, objects,
                   goal: str = '', timeout: float = 30.0) -> bool:
    """Run a saved code skill with a hard timeout.
    Passes semantic helpers so the skill can reason from detections."""
    fn = load_code_skill(skill_name)
    if fn is None:
        return False

    import threading
    h = _SkillHelpers(executor)
    # Bind objects into helpers so find() works without the caller passing them
    _find_orig = h.find
    h.find = lambda label: _find_orig(label, objects)

    result = {'ok': False}
    _stop = threading.Event()

    def _target():
        try:
            result['ok'] = bool(fn(executor, world_model, objects, goal, h))
        except Exception as e:
            print(f'[CODE_SKILL] {skill_name} raised: {e}')
            result['ok'] = False
        finally:
            _stop.set()

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        print(f'[CODE_SKILL] {skill_name} timed out after {timeout}s')
        # Release any held buttons so daemon thread doesn't fight the main loop
        try:
            executor.release_all()
        except Exception:
            pass
        return False
    return result['ok']
