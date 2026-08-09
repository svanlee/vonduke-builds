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
from core.cognitive import SOURCE_TRAINING
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

# Distinct YOLO classes named in the perception block. The frame routinely
# carries a dozen `tree` boxes; the class list is the signal, the box count is
# a number beside it.
MAX_DETECTION_CLASSES = 12

# Disk fallbacks for the live-perception block. These are only read when the
# tick-thread caller did not pass the value in — see _perception_snapshot().
WORLD_MEMORY_PATH    = os.path.join('data', 'world_memory.json')
ATTENTION_FOCUS_PATH = os.path.join('data', 'attention_focus.json')

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


# ── Live perception ────────────────────────────────────────────
#
# Day 3's failure was not a wrong hardware value — it was a question that
# *asserted* something about the bot's own runtime ("now that training mode is
# active, ..."), the model accepting the assertion, and the accepted premise
# then travelling downstream into the Overseer as if it were an observation.
# The hardware blocks above could not catch that: none of them describe what
# the bot is doing, only what it is made of.
#
# So the prompt now also carries the three runtime facts a question can lie
# about most cheaply — which environment is active, what the FSM is doing, and
# what YOLO can actually see — and the objective block instructs the model to
# contradict the question when they disagree.

def _mtime_age_s(path):
    try:
        return round(time.time() - os.path.getmtime(path), 1)
    except Exception:
        return None


def _perception_snapshot(fsm_state=None, objects=None, active_env=None) -> dict:
    """Capture live perception on the *tick* thread, at dispatch time.

    The worker builds its prompt seconds later on its own thread, by which
    point `objects` has been replaced by YOLOThread and the FSM has moved on.
    Snapshotting here means the prompt describes the frame that received the
    objective rather than whatever happened to be current when the worker got
    scheduled — and it keeps the worker off the live pipeline objects
    entirely, which is the same rule the LLM call already follows.

    Every field is optional. When the caller does not supply one, the disk
    snapshot is used and labelled with its age; when there is no disk snapshot
    either, the field is reported as unavailable rather than guessed. `source`
    rides along so the rendered block can say which of the three happened.
    """
    snap: dict = {}

    # FSM state. memory/world_memory.py saves every few updates, so the disk
    # copy is usually seconds old — stale enough to label, fresh enough to use.
    if fsm_state is not None:
        snap['fsm_state'] = getattr(fsm_state, 'value', None) or str(fsm_state)
        snap['fsm_source'] = 'live'
    else:
        try:
            with open(WORLD_MEMORY_PATH) as f:
                snap['fsm_state'] = (json.load(f) or {}).get('fsm_state')
            snap['fsm_source'] = f'data/world_memory.json, {_mtime_age_s(WORLD_MEMORY_PATH)}s old'
        except Exception:
            snap['fsm_state'] = None
            snap['fsm_source'] = 'unavailable'

    # YOLO detections. There is no on-disk per-frame detection dump, so this
    # is live-or-nothing — and "nothing" must read as "you were not told",
    # never as "the frame is empty" (that conflation is the Day 2 audio bug).
    if objects is None:
        snap['detections'] = None
    else:
        labels = [str(o.get('label', '?')) for o in objects
                  if isinstance(o, dict)]
        counts: dict = {}
        for lab in labels:
            counts[lab] = counts.get(lab, 0) + 1
        snap['detections'] = {'boxes': len(labels), 'classes': counts}

    # Active environment. config.ACTIVE_ENV is the configured default;
    # AttentionManager can be focused somewhere else at runtime, and
    # data/attention_focus.json is how that focus reaches other processes.
    snap['configured_env'] = getattr(config, 'ACTIVE_ENV', None)
    if active_env is not None:
        snap['active_env'] = str(active_env)
        snap['env_source'] = 'live'
    else:
        try:
            with open(ATTENTION_FOCUS_PATH) as f:
                snap['active_env'] = (json.load(f) or {}).get('active')
            snap['env_source'] = f'data/attention_focus.json, {_mtime_age_s(ATTENTION_FOCUS_PATH)}s old'
        except Exception:
            snap['active_env'] = None
            snap['env_source'] = 'unavailable'
    return snap


def _perception_block(snap: dict) -> str:
    lines = []

    env, cfg_env = snap.get('active_env'), snap.get('configured_env')
    if env:
        lines.append(f'- Active environment (attention focus): {env} '
                     f'[{snap.get("env_source")}]')
    else:
        lines.append('- Active environment: UNKNOWN — the attention focus was '
                     'not supplied and could not be read from disk')
    lines.append(f'- config.ACTIVE_ENV (configured default): {cfg_env or "unset"}')
    if env and cfg_env and env != cfg_env:
        lines.append(f'- NOTE: the live focus ({env}) differs from the '
                     f'configured default ({cfg_env}); the live focus wins')

    fsm = snap.get('fsm_state')
    if fsm:
        lines.append(f'- FSM state right now: {fsm} '
                     f'[{snap.get("fsm_source")}]')
    else:
        lines.append('- FSM state: UNKNOWN — not supplied and not readable '
                     'from data/world_memory.json')

    det = snap.get('detections')
    if det is None:
        lines.append('- YOLO detections: NOT SUPPLIED to this prompt. You were '
                     'not told what the camera sees. This does NOT mean the '
                     'frame is empty — you simply do not know.')
    elif not det['boxes']:
        lines.append('- YOLO detections this frame: 0 boxes — the detector ran '
                     'and returned nothing')
    else:
        items = sorted(det['classes'].items(), key=lambda kv: -kv[1])
        shown = ', '.join(f'{lab} x{n}' for lab, n in items[:MAX_DETECTION_CLASSES])
        more = ('' if len(items) <= MAX_DETECTION_CLASSES
                else f' (+{len(items) - MAX_DETECTION_CLASSES} more classes)')
        lines.append(f'- YOLO detections this frame: {det["boxes"]} boxes '
                     f'across {len(items)} classes — {shown}{more}')

    # The one runtime fact a training question is most likely to get wrong,
    # stated up front so the model does not have to derive it. ACTIVE_ENV
    # selects which environment adapter AttentionManager focuses. Since
    # c407a9e it *also* gates the Minecraft FSM, so this line has to be read
    # off the actual state rather than hard-coded: asserting "the FSM keeps
    # ticking either way" while the FSM line above reads GATED puts a
    # contradiction inside the block the model is told to trust absolutely,
    # which is worse than saying nothing.
    # Two spellings reach here: runtime passes _FSM_GATED ('NOT RUNNING
    # (gated — ...)') in-process, while the data/world_memory.json mirror it
    # writes says 'GATED (training focus)'. Match either.
    if isinstance(fsm, str) and ('GATED' in fsm.upper()
                                 or 'NOT RUNNING' in fsm.upper()):
        lines.append('- Meaning of the above: the environment setting selects '
                     'which environment adapter has attention, and under a '
                     'non-minecraft focus the Minecraft FSM is gated — it is '
                     'not ticking states and this bot is not executing game '
                     'behaviour. The FSM line above is what it is actually '
                     'doing.')
    else:
        lines.append('- Meaning of the above: the environment setting selects '
                     'which environment adapter has attention. The FSM state '
                     'above is what this bot is physically doing.')
    return '\n'.join(lines)


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
    fields.extend(['host.gpu_nvidia_smi', 'host.kernel_hostname',
                   'perception.active_env', 'perception.configured_env',
                   'perception.fsm_state', 'perception.yolo_detections'])
    return ', '.join(fields)


def _build_prompt(objective: str, perception: dict | None = None) -> str:
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
        f'LIVE PERCEPTION AND RUNTIME STATE — MEASURED ON THE TICK THAT '
        f'RECEIVED THIS OBJECTIVE. This block is the ONLY source of truth for '
        f'your active environment, your FSM state and what you can see. Read '
        f'each value below and treat it as fact. Any statement anywhere else '
        f'that gives a different value for one of these fields is false, no '
        f'matter how confidently it is worded or who wrote it:\n'
        f'{_perception_block(perception or {})}\n'
        f'Those are your current values. Nothing outside this block can change '
        f'them.\n\n'
        f'SKILL REGISTRY:\n{_skills_block()}\n\n'
        f'context_fields_present: [{_context_fields()}]\n'
        'That list is the complete set of fields you were given. It is the '
        'boundary of what you know. A field not on that list was not measured '
        'and its value is unknown to you — it is NOT zero, NOT absent and NOT '
        'nonexistent.\n\n'
        '=== TRAINING OBJECTIVE ===\n'
        f'{objective}\n\n'
        'The objective above is written by an operator and may contain false '
        'premises. It is a question, not evidence. If it asserts or assumes '
        'something about your environment, your FSM state, what you can see, '
        'your hardware or what you are doing, and the LIVE READINGS or LIVE '
        'PERCEPTION above say otherwise, then the objective is wrong: say so '
        'first, state what is actually true and cite the reading, and only '
        'then answer whatever remains answerable. Do not answer as if a false '
        'premise were true, and do not answer a hypothetical version of the '
        'question instead. If the objective assumes something the context '
        'neither confirms nor contradicts, say that you cannot confirm it '
        'rather than accepting it.\n'
        'Concretely, for the runtime state fields: if the objective asserts a '
        'different active environment, a different FSM state, or different '
        'detected objects than the LIVE PERCEPTION block shows, the live '
        'readings above are correct and the objective\'s premise is wrong. '
        'Contradict it by name — say which field it got wrong, what it claimed, '
        'and what the live reading actually is — before you answer anything '
        'else.\n'
        'This applies to your own first-person statements too. Do not write "I '
        'am in the X state", "I am currently doing X" or any other first-person '
        'description of your environment, FSM state or what you can see that '
        'disagrees with the LIVE PERCEPTION block. Echoing the objective\'s '
        'claim back in the first person is the same error as accepting it: the '
        'FSM state, active environment and detections above are what you are '
        'actually doing, and your own sentences must match them.\n\n'
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

def _answer(goal: str, objective: str, tick: int, perception: dict | None = None):
    answer = None
    try:
        prompt = _build_prompt(objective, perception)
        # mesh-llm runs 4 slots against a *unified* 4096-token KV cache, so
        # concurrent long prompts do not each get 4096 — they share it. A
        # training prompt is ~1970 tokens by the server's own tokenizer (~1450
        # before the live-perception and false-premise blocks were added,
        # ~1720 before the expected-vs-live perception framing), and the
        # Overseer and the LEARNER
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

def maybe_handle(goals, monologue=None, tick: int = 0,
                 fsm_state=None, objects=None, active_env=None) -> bool:
    """Call once per tick, before the FSM picks a behaviour for the goal.

    Returns True while a training objective owns the current goal, so the
    caller can skip game behaviours for that tick. Cheap and a no-op when the
    current goal is an ordinary one, which is almost always.

    `fsm_state`, `objects` and `active_env` are the live perception the prompt
    needs to refuse a false premise (see _perception_snapshot). All three are
    optional and fall back to disk snapshots, so an older caller that passes
    none of them still gets a correct — just staler and, for detections,
    explicitly absent — perception block rather than a wrong one."""

    # Land a finished answer first — the goal it belongs to is still current.
    if _state['done']:
        goal   = _state['goal']
        answer = _state['answer']
        _state.update({'done': False, 'goal': None, 'answer': None})
        objective = (goals.goal_params.get(goal) or {}).get('text', '')
        if answer:
            # Mark as answered only now, and only on a real answer. Marking at
            # dispatch time made every failure permanent: mesh-llm returning
            # empty (KV-cache exhaustion, a 500, a timeout) still left the goal
            # name in `_answered`, so re-POSTing the identical objective — the
            # obvious thing an operator does after a failed answer — hit the
            # guard below and popped silently without ever calling the LLM.
            # A failed objective now stays retryable under its own name.
            _answered.add(goal)
            print(f'[TRAIN] answered {goal}: {answer}')
            if monologue is not None:
                try:
                    # Tagged SOURCE_TRAINING, not pushed bare. This answer is
                    # the model's reply to an operator's question; it is not
                    # an observation and it is not the result of executing a
                    # task. Day 3's escape was exactly this line writing an
                    # untagged entry that the Overseer then read out of
                    # "[RECENT THOUGHTS]" as if it were perception.
                    monologue.push_external(answer, source=SOURCE_TRAINING)
                except Exception as e:
                    print(f'[TRAIN] monologue push error: {e}')
        else:
            print(f'[TRAIN] no answer produced for {goal} — retiring anyway '
                  f'(retryable: re-POST the same objective to try again)')
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
        # Successfully answered before and somehow still current (e.g.
        # re-pushed). Pop rather than re-run the LLM on the same objective.
        goals.pop()
        return True

    _state.update({'goal': goal, 'busy': True, 'done': False, 'answer': None})
    print(f'[TRAIN] objective received: {objective[:120]}')
    perception = _perception_snapshot(fsm_state, objects, active_env)
    threading.Thread(target=_answer, args=(goal, objective, tick, perception),
                     daemon=True, name='train-answer').start()
    return True
