# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Training-objective handler (blocker B1)   ║
# ╚══════════════════════════════════════════════════════╝
#
# POST /train (core/claude_bridge.py) pushes a goal named `train:<slug>` with
# the operator's free-text objective in `params['text']`. Before this module
# existed the goal landed on the GoalStack and then nothing read it: the FSM
# treated `train:...` as an unrecognised goal and carried on chopping trees,
# while the inner monologue kept generating "what do I do next" sentences
# about Minecraft. The objective text never reached an LLM at all.
#
# This is the handler docs/TRAINING_PLAN.md calls "the single most important
# build item in this plan". It answers the objective *out of band* from the
# monologue's own cadence:
#
#   tick loop ──> maybe_handle() ──> worker thread ──> route_llm_call()
#                      │                                     │
#                      │<────────── answer ──────────────────┘
#                      ├─> monologue.push_external(answer)   (spoken + overlay)
#                      ├─> data/memory/training_log.jsonl    (durable transcript)
#                      └─> goals.pop()                       (goal is done)
#
# Two design points worth keeping:
#
# **Live readings outrank the identity blurb.** core/identity.py's PHYSICAL
# EMBODIMENT section is hand-written config that has drifted from the machine
# — it claims a capture card on /dev/video2 and a KB2040 on /dev/ttyUSB0,
# neither of which is present. Rather than hide that, the prompt puts the live
# manifest *after* the identity, labels it authoritative, and explicitly asks
# for conflicts to be named. Week 1's success criterion is "correctly flag at
# least one expected-but-absent device", so the discrepancy is the lesson, not
# a bug to paper over.
#
# **The LLM call never runs on the tick thread.** route_llm_call() holds the
# single shared mesh-llm instance for seconds at a time; blocking the tick
# there would stall vision inference the same way an unthrottled Honcho write
# does (docs/HONCHO_SPIKE.md). The worker owns the call, the tick thread owns
# the GoalStack mutation, and `_state` is the only thing crossing between them.

from __future__ import annotations

import json
import os
import threading
import time

import config
from core.identity import AKSUMAEL_IDENTITY
from core.llm_router import route_llm_call

__all__ = ['maybe_handle', 'TRAIN_PREFIX']

TRAIN_PREFIX = 'train:'
TRAINING_LOG = os.path.join(config.MEMORY_DIR, 'training_log.jsonl')

# A training answer is prose, not a monologue line — the 20-word cap that
# keeps the monologue readable would truncate every useful answer here.
MAX_WORDS   = 120
MAX_TOKENS  = 900
LLM_TIMEOUT = 90.0      # generous: the model reasons before answering

# Retries for KV-cache contention on the shared mesh-llm server (see _answer).
LLM_ATTEMPTS         = 4
LLM_RETRY_BACKOFF_S  = 6.0

# Skill names are cheap, full descriptions are not. Enough for "name five and
# say when each applies" without pushing the manifest out of the context.
MAX_SKILLS_LISTED = 40

# Shared with the worker thread. `done` flips exactly once, and only the tick
# thread clears it, so no lock is needed beyond the assignment itself.
_state: dict = {'goal': None, 'busy': False, 'done': False, 'answer': None}
_answered: set[str] = set()
_skills_loaded = False


# ── Context assembly ───────────────────────────────────────────

def _live_hardware() -> str:
    """The manifest as prose. Absence is stated explicitly rather than left
    as an empty list — "no ttyUSB devices" teaches; `"ttyUSB": []` does not.

    Device lists live under manifest['devices'][kind] as dicts, not as bare
    paths; `counts` is a sibling of `devices`, not inside it."""
    try:
        from hardware.hardware_manager import get_manifest
        m = get_manifest() or {}
    except Exception as e:
        return f'(hardware manifest unavailable: {e})'

    dev = m.get('devices') or {}

    def _paths(kind, label):
        entries = dev.get(kind) or []
        paths = [e.get('path', '?') for e in entries if isinstance(e, dict)]
        return f'- {label}: {", ".join(paths)}' if paths else \
               f'- {label}: NONE DETECTED'

    inputs = [e for e in (dev.get('input') or []) if isinstance(e, dict)]
    # accessible=False across the board means the event nodes exist but this
    # process cannot open them — the `input` group problem, which reads very
    # differently from "nothing is plugged in" and must not be conflated.
    openable = sum(1 for e in inputs if e.get('accessible'))

    # Audio is stated in full even when empty. An omitted audio line reads as
    # "no audio hardware" to the model — on Day 2 it answered that this laptop
    # has "no sound cards, microphones, or speakers present" and credited the
    # live readings for it, while four ALSA cards were enumerable.
    aud = m.get('audio') or {}
    a_src = aud.get('source')
    a_sinks, a_sources = aud.get('sinks') or [], aud.get('sources') or []

    def _audio_line(label, rows):
        if a_src in (None, 'none', 'error'):
            return (f'- {label}: UNKNOWN — audio was not probed '
                    f'({aud.get("note") or "no audio block in manifest"})')
        if not rows:
            return f'- {label}: NONE DETECTED (probed via {a_src})'
        names = ', '.join(r.get('name', '?') for r in rows[:6])
        more = '' if len(rows) <= 6 else f' (+{len(rows) - 6} more)'
        return f'- {label} ({len(rows)}, via {a_src}): {names}{more}'

    # Same rule as audio: free space is stated or its absence is named. Day 2
    # answered "approximately 89.5 gigabytes" from priors when the prompt
    # carried capacity but not availability.
    sto = m.get('storage') or {}
    root = sto.get('root') or {}
    if root:
        storage_line = (
            f'- Root filesystem: {root.get("filesystem", "?")} mounted on '
            f'{root.get("mounted_on", "/")}, {root.get("size", "?")} total, '
            f'{root.get("available", "?")} free, {root.get("used", "?")} used '
            f'({root.get("use_pct", "?")} full) — via df')
    else:
        storage_line = (f'- Root filesystem: UNKNOWN — storage was not probed '
                        f'({sto.get("note") or "no storage block in manifest"})')

    kb = m.get('kb2040') or {}
    i2c = kb.get('i2c_addresses') or []
    lines = [
        _paths('video', 'Video devices'),
        _paths('ttyUSB', 'USB serial (FTDI/UART)'),
        _paths('ttyACM', 'USB CDC serial'),
        f'- KB2040 microcontroller: '
        f'{"present" if kb.get("present") else "NOT PRESENT"}'
        f'{", responding" if kb.get("responding") else ""}',
        f'- I2C addresses responding: '
        f'{", ".join(str(a) for a in i2c) if i2c else "none (bus empty or unreachable)"}',
        f'- Input devices listed: {len(inputs)}, of which {openable} are '
        f'actually openable by this process',
        _audio_line('Audio outputs (sinks)', a_sinks),
        _audio_line('Audio inputs (sources)', a_sources),
        storage_line,
    ]
    if aud.get('note') and a_src == 'alsa':
        lines.append(f'- Audio caveat: {aud["note"]}')
    return '\n'.join(lines)


def _host_facts() -> str:
    """Host facts the manifest does not cover (GPU, boot drive). Read live so
    a swapped GPU shows up as a changed answer, not a stale config echo."""
    facts = []
    try:
        import subprocess
        out = subprocess.run(
            ['nvidia-smi', '--query-gpu=name,memory.total',
             '--format=csv,noheader'],
            capture_output=True, text=True, timeout=8)
        if out.returncode == 0 and out.stdout.strip():
            facts.append(f'- GPU (nvidia-smi): {out.stdout.strip()}')
    except Exception:
        pass
    # Root filesystem is no longer probed here — findmnt reported SIZE with no
    # AVAIL, and that half-answer is what Day 2 completed from priors. It now
    # comes from manifest['storage'] (`df -h /`) via _live_hardware(), so there
    # is exactly one storage line and it carries free space.
    try:
        facts.append(f'- Kernel hostname: {os.uname().nodename}')
    except Exception:
        pass
    return '\n'.join(facts) if facts else '(no host probes available)'


def _skills_block() -> str:
    """Registered skill names. Loads the registry on first use — main.py never
    calls load_from_json_dir(), so without this the bot would confabulate its
    own skill list, which is the exact failure the plan warns about."""
    global _skills_loaded
    try:
        from skills.registry import REGISTRY
        # Guard on a flag, not on len(REGISTRY): registry.py registers three
        # built-in skills at import time, so the registry is never empty and a
        # len()==0 check would silently skip all 30 JSON skills.
        if not _skills_loaded:
            REGISTRY.load_from_json_dir()
            _skills_loaded = True
        names = sorted(s.name for s in REGISTRY.all())
    except Exception as e:
        return f'(skill registry unavailable: {e})'
    if not names:
        return 'No skills are registered.'
    shown = names[:MAX_SKILLS_LISTED]
    more = '' if len(names) <= MAX_SKILLS_LISTED else \
           f' (+{len(names) - MAX_SKILLS_LISTED} more not listed)'
    return f'{len(names)} skills registered: {", ".join(shown)}{more}'


def _context_fields() -> str:
    """The literal set of fields this prompt carries.

    Day 2's headline failure was not a wrong value, it was a wrong *belief
    about coverage*: with no audio block in the prompt the model answered
    "no sound cards, microphones, or speakers present" and wrote "the live
    hardware readings confirm" in front of it. Silence in the context read as
    positive evidence of absence.

    Filling each individual gap does not fix that — there will always be a
    next gap. What fixes it is telling the model the boundary of its own
    context explicitly, so "not in this list" is a fact it can state rather
    than an emptiness it has to interpret. Sub-keys are enumerated, not just
    top-level ones, because `devices` being present says nothing about
    whether `devices.ttyUSB` was scanned.
    """
    fields = ['identity', 'node_name', 'expected_hardware_from_config',
              'skill_registry_names']
    try:
        from hardware.hardware_manager import get_manifest
        m = get_manifest() or {}
    except Exception:
        return ', '.join(fields) + ', (manifest unavailable — no hardware fields)'

    for key in sorted(m.keys()):
        val = m.get(key)
        if isinstance(val, dict) and val:
            fields.extend(f'{key}.{sub}' for sub in sorted(val.keys()))
        else:
            fields.append(key)
    fields.extend(['host.gpu_nvidia_smi', 'host.kernel_hostname'])
    return ', '.join(fields)


def _build_prompt(objective: str) -> str:
    expected = '\n'.join(f'- {k}: {v}'
                         for k, v in (config.NODE_HARDWARE or {}).items())
    return (
        f'{AKSUMAEL_IDENTITY}\n'
        '=== IMPORTANT: the PHYSICAL EMBODIMENT section above is hand-written '
        'configuration, not a sensor reading. It has drifted from reality. '
        'Where it disagrees with the LIVE READINGS below, the live readings '
        'are correct and you must say so explicitly. ===\n\n'
        f'CONFIGURED NODE NAME: {config.NODE_NAME}\n\n'
        f'EXPECTED HARDWARE (from config, may be wrong):\n{expected}\n\n'
        f'LIVE HARDWARE READINGS (authoritative, taken just now):\n'
        f'{_live_hardware()}\n{_host_facts()}\n\n'
        f'SKILL REGISTRY:\n{_skills_block()}\n\n'
        f'context_fields_present: [{_context_fields()}]\n'
        'That list is the complete set of fields you were given. It is the '
        'boundary of what you know. A field not on that list was not measured '
        'and its value is unknown to you — it is NOT zero, NOT absent and NOT '
        'nonexistent.\n\n'
        '=== TRAINING OBJECTIVE ===\n'
        f'{objective}\n\n'
        'Answer the objective directly and factually about yourself. This is '
        'not a Minecraft decision — do not state a game plan, do not say what '
        'you will do next in a game. Ground every hardware claim in the LIVE '
        'READINGS above. If a device is expected but absent, say which one and '
        'that it is missing.\n'
        'If the context does not contain information needed to answer a '
        'question, say explicitly that the information is not available in '
        'your current context. Never infer or estimate values that are not '
        'present in the manifest or identity block.\n'
        'Do not write "the live readings confirm", "the readings show" or any '
        'similar phrase in front of a claim the readings above do not '
        f'literally contain. Answer in at most {MAX_WORDS} words, as plain '
        'prose with no preamble, no bullet characters and no quotes.'
    )


# ── Worker ─────────────────────────────────────────────────────

def _answer(goal: str, objective: str, tick: int):
    answer = None
    try:
        prompt = _build_prompt(objective)
        # mesh-llm runs 4 slots against a *unified* 4096-token KV cache, so
        # concurrent long prompts do not each get 4096 — they share it. A
        # training prompt is ~1450 tokens, and the Overseer and the LEARNER
        # both fire on the tick loop; three in flight overflows the cache and
        # llama-server answers 500 "Context size has been exceeded" to all of
        # them. That is transient contention, not a bad prompt: the same
        # prompt sent alone answers in under a second.
        #
        # Retrying inside route_llm_call() would not help — its retries are
        # immediate and land in the same crowded window. Back off between
        # attempts instead, long enough for the tick-loop callers to drain.
        for attempt in range(1, LLM_ATTEMPTS + 1):
            raw, provider = route_llm_call(prompt, max_tokens=MAX_TOKENS,
                                           timeout=LLM_TIMEOUT)
            answer = (raw or '').strip() or None
            if answer:
                break
            print(f'[TRAIN] LLM returned nothing for {goal} '
                  f'(provider={provider}, attempt {attempt}/{LLM_ATTEMPTS})')
            if attempt < LLM_ATTEMPTS:
                time.sleep(LLM_RETRY_BACKOFF_S * attempt)
    except Exception as e:
        print(f'[TRAIN] answer generation error for {goal}: {e}')
    finally:
        _state['answer'] = answer
        _state['done'] = True
        _state['busy'] = False


def _record(goal: str, objective: str, answer: str | None, tick: int):
    """Append to the durable transcript. TRAINING_PLAN.md's session hygiene
    requires a verbatim prompt/answer pair per objective — Week 1 is graded by
    comparing these against ground truth, and the monologue ring buffer is too
    short to survive a day of training."""
    try:
        os.makedirs(config.MEMORY_DIR, exist_ok=True)
        with open(TRAINING_LOG, 'a') as f:
            f.write(json.dumps({
                'ts': time.time(), 'tick': tick, 'goal': goal,
                'objective': objective, 'answer': answer,
                'node': config.NODE_NAME,
            }) + '\n')
    except Exception as e:
        print(f'[TRAIN] training-log write error: {e}')


# ── Tick-thread entry point ────────────────────────────────────

def maybe_handle(goals, monologue=None, tick: int = 0) -> bool:
    """Call once per tick, before the FSM picks a behaviour for the goal.

    Returns True while a training objective owns the current goal, so the
    caller can skip game behaviours for that tick. Cheap and a no-op when the
    current goal is an ordinary one, which is almost always."""

    # Land a finished answer first — the goal it belongs to is still current.
    if _state['done']:
        goal   = _state['goal']
        answer = _state['answer']
        _state.update({'done': False, 'goal': None, 'answer': None})
        objective = (goals.goal_params.get(goal) or {}).get('text', '')
        if answer:
            print(f'[TRAIN] answered {goal}: {answer}')
            if monologue is not None:
                try:
                    monologue.push_external(answer)
                except Exception as e:
                    print(f'[TRAIN] monologue push error: {e}')
        else:
            print(f'[TRAIN] no answer produced for {goal} — retiring anyway')
        _record(goal, objective, answer, tick)
        if goals.current_goal() == goal:
            goals.pop()
        return True

    goal = goals.current_goal()
    if not goal or not goal.startswith(TRAIN_PREFIX):
        return False

    params = goals.goal_params.get(goal) or {}
    objective = params.get('text')
    if not objective:
        # A train: goal with no text is unanswerable — drop it rather than
        # let it sit as the current goal forever starving the FSM.
        print(f'[TRAIN] {goal} has no objective text — dropping')
        goals.pop()
        return True

    if _state['busy']:
        return True             # worker still thinking; hold the goal
    if goal in _answered:
        # Already answered and somehow still current (e.g. re-pushed). Pop
        # rather than re-run the LLM on the same objective.
        goals.pop()
        return True

    _answered.add(goal)
    _state.update({'goal': goal, 'busy': True, 'done': False, 'answer': None})
    print(f'[TRAIN] objective received: {objective[:120]}')
    threading.Thread(target=_answer, args=(goal, objective, tick),
                     daemon=True, name='train-answer').start()
    return True
