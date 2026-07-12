# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.1.0 — LLM-Generated Code Skills          ║
# ║  Turns a mined key-sequence skill into a more robust  ║
# ║  Python function (retries, edge cases) written by the ║
# ║  LLM. Off by default — see config.ENABLE_CODE_SKILLS. ║
# ╚══════════════════════════════════════════════════════╝
#
# SAFETY NOTE: this executes LLM-generated Python. It is gated behind
# config.ENABLE_CODE_SKILLS (default False) and runs with a restricted
# builtins namespace exposing only what a skill legitimately needs
# (executor, world_model, objects, time.sleep). Review generated skills in
# data/skills/code/ before trusting them on a rig you care about.

import json
import os
import re
import time
import urllib.error
import urllib.request

import config

CODE_SKILL_PROMPT = """You write short, defensive Python functions for a Minecraft
automation agent called AKSUMAEL.

Skill name: {name}
Recorded action steps (key/click sequence that worked): {steps}
Context: {context}

Write a single Python function with this EXACT signature:

def run_skill(executor, world_model, objects):
    ...
    return True   # or False on failure

Rules:
- `executor.execute(action_dict)` sends one input action, e.g.
  executor.execute({{'key': '2', 'click': None, 'gamepad': None, 'source': 'code_skill'}})
  executor.execute({{'key': None, 'click': [50, 50], 'gamepad': None, 'source': 'code_skill'}})
- `objects` is the current list of YOLO detections: [{{'label': str, 'conf': float, 'box': [x1,y1,x2,y2]}}, ...]
- `world_model` is a WorldModel instance — you may read world_model.position but do not call save()/load().
- Use `time.sleep(seconds)` for pacing between actions (small values, <1s each).
- Handle the case where the expected object is no longer in `objects` — retry a few times, then return False.
- Do NOT import anything. Do NOT use open()/exec()/eval()/os/sys/subprocess/socket/__import__.
- Keep it under 40 lines. Return only the function body — no explanation, no markdown fences.
"""


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith('```'):
        lines = text.split('\n')
        if lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith('```'):
            lines = lines[:-1]
        text = '\n'.join(lines)
    return text.strip()


def generate_code_skill(skill_name: str, steps: list, context: str = '') -> str | None:
    """Ask the LLM to write a Python function implementing this skill.
    Returns the function source as a string, or None on failure."""
    if not config.ANTHROPIC_API_KEY:
        return None

    prompt = CODE_SKILL_PROMPT.format(name=skill_name, steps=json.dumps(steps)[:800],
                                       context=context[:400])
    payload = json.dumps({
        'model': config.CLAUDE_MODEL,
        'max_tokens': 600,
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
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        text_block = next((b for b in data.get('content', []) if b.get('type') == 'text'), None)
        if text_block is None:
            return None
        code = _strip_fences(text_block['text'])
        if 'def run_skill(' not in code:
            print('[CODE_SKILL] LLM response missing run_skill() — discarding')
            return None
        if not _is_safe_source(code):
            print('[CODE_SKILL] generated code failed the safety check — discarding')
            return None
        return code
    except urllib.error.HTTPError as e:
        print(f'[CODE_SKILL] Claude HTTP {e.code}')
    except Exception as e:
        print(f'[CODE_SKILL] generation error: {e}')
    return None


_FORBIDDEN_PATTERNS = re.compile(
    r'\b(import|open|exec|eval|__import__|os\.|sys\.|subprocess|socket|globals|locals|getattr|setattr)\b'
)


def _is_safe_source(code: str) -> bool:
    """Reject anything that looks like it's trying to escape the sandbox."""
    return not _FORBIDDEN_PATTERNS.search(code)


def save_code_skill(skill_name: str, code: str) -> str:
    os.makedirs(config.CODE_SKILLS_DIR, exist_ok=True)
    safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in skill_name)
    path = os.path.join(config.CODE_SKILLS_DIR, f'{safe}.py')
    with open(path, 'w') as f:
        f.write(code)
    return path


_RESTRICTED_BUILTINS = {
    'range': range, 'len': len, 'min': min, 'max': max, 'abs': abs,
    'enumerate': enumerate, 'zip': zip, 'sorted': sorted, 'list': list,
    'dict': dict, 'set': set, 'tuple': tuple, 'str': str, 'int': int,
    'float': float, 'bool': bool, 'print': print, 'True': True, 'False': False,
    'None': None,
}


def load_code_skill(skill_name: str):
    """Load a saved code skill and return its run_skill(executor, world_model,
    objects) function, executed with a restricted builtins namespace. Returns
    None if the file doesn't exist, fails the safety check, or fails to load."""
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


def run_code_skill(skill_name: str, executor, world_model, objects, timeout: float = 10.0) -> bool:
    """Run a saved code skill with a hard timeout. Returns False on any
    failure, timeout, or exception (never raises)."""
    fn = load_code_skill(skill_name)
    if fn is None:
        return False

    import threading
    result = {'ok': False}

    def _target():
        try:
            result['ok'] = bool(fn(executor, world_model, objects))
        except Exception as e:
            print(f'[CODE_SKILL] {skill_name} raised: {e}')
            result['ok'] = False

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        print(f'[CODE_SKILL] {skill_name} timed out after {timeout}s')
        return False
    return result['ok']
