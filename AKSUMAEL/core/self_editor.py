# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0 — Self-Editor                           ║
# ║  Lets AKSUMAEL read its own source, propose edits,    ║
# ║  and apply them after a safety gate.                   ║
# ║                                                        ║
# ║  Flow:                                                 ║
# ║    1. observe_failure() — logs a recurring failure     ║
# ║    2. propose_fix() — LLM reads source + writes patch  ║
# ║    3. apply_pending() — applies safe pending edits     ║
# ║    4. Overseer reviews data/self_edits/applied/        ║
# ╚══════════════════════════════════════════════════════╝

import json
import os
import re
import time

import config
from core.llm_router import route_llm_call

REPO_ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PENDING_DIR     = os.path.join(REPO_ROOT, 'data', 'self_edits', 'pending')
APPLIED_DIR     = os.path.join(REPO_ROOT, 'data', 'self_edits', 'applied')
REJECTED_DIR    = os.path.join(REPO_ROOT, 'data', 'self_edits', 'rejected')

# Files AKSUMAEL is allowed to read for introspection
READABLE_PATHS  = {
    'core/runtime.py', 'core/fsm.py', 'core/llm_router.py',
    'core/overseer.py', 'core/code_skill_generator.py', 'core/self_editor.py',
    'skills/skill_system.py', 'skills/skill_evaluator.py',
    'memory/aurora_memory.py', 'memory/hud_reader.py',
    'config.py', 'tools/y_watchdog.py',
    'data/skills/',       # any skill JSON
    'data/skills/code/',  # any generated code skill (matches config.CODE_SKILLS_DIR)
}

# Files AKSUMAEL is allowed to EDIT via propose_fix (code skills + data only)
# Core source edits go to pending/ for human/overseer review, never auto-applied.
SELF_APPLY_PATHS = {
    'data/skills/code/',   # its own generated skills — full autonomy (matches config.CODE_SKILLS_DIR)
    'data/skills/',        # JSON skills — full autonomy
}

SELF_EDIT_PROMPT = """You are AKSUMAEL's self-improvement engine.

AKSUMAEL is an autonomous Minecraft agent. It has observed a recurring failure
and needs to fix its own code.

## Failure report
Skill / system: {skill_name}
Failure description: {failure_desc}
Consecutive failures: {fail_count}
Current goal: {goal}
What AKSUMAEL sees: {detections}
World state: {world_state}

## Source file to fix
Path: {file_path}
```python
{source}
```

## Your task
Write a corrected version of ONLY the function or section that needs to change.
Respond in this exact JSON format:
{{
  "reason": "one sentence explaining what was wrong and what you changed",
  "old_text": "exact text to replace (must appear verbatim in the source)",
  "new_text": "replacement text"
}}

Rules:
- old_text must be a literal substring of the source — no approximations.
- new_text must be valid Python.
- Do not change any other part of the file.
- Do not add imports that aren't already in the file.
- Keep changes minimal and surgical.
"""


def _safe_rel_path(path: str) -> str | None:
    """Normalise path relative to repo root. Returns None if outside repo."""
    abs_p = os.path.realpath(os.path.join(REPO_ROOT, path))
    if abs_p.startswith(os.path.realpath(REPO_ROOT)):
        return os.path.relpath(abs_p, REPO_ROOT)
    return None


def read_source(rel_path: str) -> str | None:
    """Read a source file for introspection. Returns None if not allowed."""
    rp = _safe_rel_path(rel_path)
    if rp is None:
        return None
    # Check if path falls under any readable root
    allowed = any(
        rp == p or rp.startswith(p.rstrip('/') + os.sep)
        for p in READABLE_PATHS
    )
    if not allowed:
        print(f'[SELF_EDIT] read denied: {rel_path}')
        return None
    full = os.path.join(REPO_ROOT, rp)
    if not os.path.exists(full):
        return None
    with open(full) as f:
        return f.read()


def list_source_files(subdir: str = '') -> list[str]:
    """List readable source files (relative paths)."""
    root = os.path.join(REPO_ROOT, subdir) if subdir else REPO_ROOT
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ('venv', '__pycache__', '.git', 'node_modules')]
        for fn in filenames:
            if fn.endswith('.py') or fn.endswith('.json'):
                rel = os.path.relpath(os.path.join(dirpath, fn), REPO_ROOT)
                rp2 = _safe_rel_path(rel)
                if rp2:
                    result.append(rp2)
    return sorted(result)


def propose_fix(skill_name: str, failure_desc: str, fail_count: int,
                file_path: str, goal: str = '', detections: list = None,
                world_state: dict = None) -> dict | None:
    """Ask the LLM to propose a surgical fix to a source file.
    Returns {'reason', 'old_text', 'new_text', 'file_path'} or None."""
    source = read_source(file_path)
    if source is None:
        print(f'[SELF_EDIT] cannot read {file_path}')
        return None

    detections = detections or []
    world_state = world_state or {}
    det_str = ', '.join(
        f"{d.get('label')}@{d.get('conf', 0):.2f}" for d in detections[:8]
    ) or 'none'

    prompt = SELF_EDIT_PROMPT.format(
        skill_name=skill_name,
        failure_desc=failure_desc,
        fail_count=fail_count,
        goal=goal,
        detections=det_str,
        world_state=json.dumps(world_state),
        file_path=file_path,
        source=source[:6000],   # cap to ~6k chars to stay in context
    )

    raw, provider = route_llm_call(prompt, max_tokens=4000, timeout=90)
    if not raw:
        print('[SELF_EDIT] LLM failed')
        return None

    # Parse JSON response
    try:
        # Strip fences
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if not m:
            print('[SELF_EDIT] no JSON in response')
            return None
        patch = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        print(f'[SELF_EDIT] JSON parse error: {e}')
        return None

    if not all(k in patch for k in ('reason', 'old_text', 'new_text')):
        print('[SELF_EDIT] incomplete patch response')
        return None

    patch['file_path'] = file_path
    patch['skill_name'] = skill_name
    patch['provider'] = provider
    patch['timestamp'] = time.time()
    return patch


def queue_patch(patch: dict) -> str:
    """Write a proposed patch to pending/ for review. Returns the patch path."""
    os.makedirs(PENDING_DIR, exist_ok=True)
    ts = int(patch.get('timestamp', time.time()))
    name = f"{ts}_{patch.get('skill_name', 'unknown')}.json"
    path = os.path.join(PENDING_DIR, name)
    with open(path, 'w') as f:
        json.dump(patch, f, indent=2)
    print(f'[SELF_EDIT] patch queued: {path}')
    return path


def _can_self_apply(file_path: str) -> bool:
    """Returns True if AKSUMAEL can apply this patch without human review."""
    rp = _safe_rel_path(file_path)
    if rp is None:
        return False
    return any(
        rp == p or rp.startswith(p.rstrip('/') + os.sep)
        for p in SELF_APPLY_PATHS
    )


def apply_patch(patch: dict, force: bool = False) -> bool:
    """Apply a patch to the source file.
    Auto-applies only to code_skills/ and skills/ JSON.
    Core source files go to pending/ and require force=True (overseer approval).
    Returns True on success."""
    file_path = patch.get('file_path', '')
    old_text  = patch.get('old_text', '')
    new_text  = patch.get('new_text', '')

    if not old_text or old_text == new_text:
        print('[SELF_EDIT] trivial or empty patch — skipping')
        return False

    auto = _can_self_apply(file_path)
    if not auto and not force:
        queue_patch(patch)
        print(f'[SELF_EDIT] {file_path} requires overseer approval — queued')
        return False

    full_path = os.path.join(REPO_ROOT, file_path)
    if not os.path.exists(full_path):
        print(f'[SELF_EDIT] file not found: {full_path}')
        return False

    with open(full_path) as f:
        src = f.read()

    if old_text not in src:
        print(f'[SELF_EDIT] old_text not found in {file_path} — patch stale?')
        os.makedirs(REJECTED_DIR, exist_ok=True)
        with open(os.path.join(REJECTED_DIR, f'{int(time.time())}.json'), 'w') as f:
            json.dump({**patch, 'reject_reason': 'old_text not found'}, f, indent=2)
        return False

    new_src = src.replace(old_text, new_text, 1)

    # Basic Python syntax check for .py files
    if full_path.endswith('.py'):
        try:
            compile(new_src, full_path, 'exec')
        except SyntaxError as e:
            print(f'[SELF_EDIT] syntax error in patch: {e} — rejecting')
            os.makedirs(REJECTED_DIR, exist_ok=True)
            with open(os.path.join(REJECTED_DIR, f'{int(time.time())}.json'), 'w') as f:
                json.dump({**patch, 'reject_reason': str(e)}, f, indent=2)
            return False

    with open(full_path, 'w') as f:
        f.write(new_src)

    os.makedirs(APPLIED_DIR, exist_ok=True)
    with open(os.path.join(APPLIED_DIR, f'{int(time.time())}_{patch.get("skill_name","?")}.json'), 'w') as f:
        json.dump(patch, f, indent=2)

    print(f'[SELF_EDIT] applied to {file_path}: {patch.get("reason", "")}')
    return True


def apply_pending(overseer_approved: bool = False) -> int:
    """Apply all pending patches. Core source patches only apply if overseer_approved=True."""
    if not os.path.exists(PENDING_DIR):
        return 0
    applied = 0
    for fn in sorted(os.listdir(PENDING_DIR)):
        if not fn.endswith('.json'):
            continue
        path = os.path.join(PENDING_DIR, fn)
        with open(path) as f:
            patch = json.load(f)
        if apply_patch(patch, force=overseer_approved):
            os.remove(path)
            applied += 1
    return applied
