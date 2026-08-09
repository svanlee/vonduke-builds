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
import re
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
_state: dict = {'goal': None, 'busy': False, 'done': False, 'answer': None,
                'objective_sent': None}
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


def _perception_snapshot(fsm_state=None, objects=None, active_env=None,
                         vision_source=None, camera_device_available=None) -> dict:
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

    # What the detections above are detections *of*. Since the screen-grab
    # fallback landed, "YOLO ran and saw N boxes" no longer implies the frame
    # was the game: with the capture card gone the live frame is a grab of
    # this machine's Linux desktop. Stating the source keeps the model from
    # reading window chrome as terrain — the failure already seen once, where
    # an empty game frame got narrated as a Ubuntu system tray.
    snap['vision_source'] = vision_source or None

    # The distinction the detection count cannot carry on its own: 0 boxes
    # means either "the camera works and the scene was empty" or "there was
    # no camera device at all". Those are opposite epistemic situations —
    # the first is an observation, the second is the absence of one — and
    # collapsing them is what lets an answer confabulate a described scene
    # out of a missing device. True only when a real capture device is open
    # and delivering; a desktop screen grab is False, because there is still
    # no *game* camera. None means the caller did not say, which must read
    # as "you do not know", never as False.
    snap['camera_device_available'] = camera_device_available

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

    src = snap.get('vision_source')
    cam = snap.get('camera_device_available')
    card = f'/dev/video{getattr(config, "CAMERA_INDEX", 2)}'

    lines.append(f'- camera_device_available: '
                 f'{"true" if cam else "false" if cam is not None else "NOT SUPPLIED"}')

    if cam is None:
        lines.append('- Vision: you were NOT told whether a camera device is '
                     'present. You therefore cannot tell an empty scene from '
                     'a missing camera. Do not guess which it is.')
    elif cam:
        lines.append(f'- Vision source: {src or "a capture device"} — open and '
                     f'delivering frames. Detections below describe the live '
                     f'game feed.')
    elif src and src.startswith('screenshot'):
        # Frames exist, so "no visual input" would be false — but they are
        # this machine's desktop, which is not the game and not evidence
        # about it. The failure to prevent is describing window chrome as
        # terrain, which has happened before (Day 5 r-3: an empty game frame
        # narrated as a Ubuntu system tray).
        lines.append(f'- CAMERA OFFLINE — the capture card ({card}) is missing, '
                     f'so there is NO game visual input. The only frames '
                     f'reaching this bot are {src}: a grab of its own Linux '
                     f'desktop. Minecraft runs on a separate PC and reaches '
                     f'this bot only through the capture card. Do not describe '
                     f'visual state in the game, and do not mention anything '
                     f'seen on screen as though it were the world. For any '
                     f'question about what the bot can see in-game, report: '
                     f'no visual data available.')
    else:
        lines.append(f'- CAMERA OFFLINE — no visual input. {card} (capture '
                     f'card) missing, and no usable fallback. Do not describe '
                     f'visual state or mention anything seen on screen. '
                     f'Report: no visual data available.')

    det = snap.get('detections')
    if det is None:
        lines.append('- YOLO detections: NOT SUPPLIED to this prompt. You were '
                     'not told what the camera sees. This does NOT mean the '
                     'frame is empty — you simply do not know.')
    elif not det['boxes']:
        # 0 boxes is the ambiguous reading camera_device_available exists to
        # resolve, so spell out which of the two it is rather than leaving
        # the inference to the model.
        if cam is None:
            lines.append('- YOLO detections this frame: 0 boxes — the detector '
                         'ran and returned nothing. Because '
                         'camera_device_available was not supplied, you cannot '
                         'tell whether the scene was empty or whether there '
                         'was no camera at all. Do not assert either.')
        elif cam:
            lines.append('- YOLO detections this frame: 0 boxes — the detector '
                         'ran against the live game feed and returned nothing. '
                         'With a camera present this IS an observation: the '
                         'visible scene contained no recognised objects.')
        else:
            lines.append('- YOLO detections this frame: 0 boxes. With '
                         'camera_device_available false this is NOT an '
                         'observation of an empty scene — there is no game '
                         'feed to be empty. It records the absence of input, '
                         'not the absence of objects.')
    elif not cam:
        # Non-zero boxes with no camera means the detector ran on desktop
        # pixels. Left unlabelled these read as game objects, which is worse
        # than 0 boxes because they look like positive evidence.
        items = sorted(det['classes'].items(), key=lambda kv: -kv[1])
        shown = ', '.join(f'{lab} x{n}' for lab, n in items[:MAX_DETECTION_CLASSES])
        lines.append(f'- YOLO detections this frame: {det["boxes"]} boxes '
                     f'({shown}). IGNORE THESE as game state. There is no '
                     f'camera device, so the detector ran on non-game pixels; '
                     f'its labels are Minecraft class names fired against '
                     f'desktop content, not objects in a world. They are not '
                     f'evidence that any of these things exist.')
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


# ── Prior-answer withholding ───────────────────────────────────
#
# Day 5's a-3 objective ends in the bot's own a-1 answer, quoted verbatim, and
# asks it to revise that assessment against figures the readings do not carry.
# It failed three times with a byte-identical 508-char reply: the a-1 answer
# copied back word for word, never mentioning the new figures. Same hash at
# temperature 0.2 across three different prompt revisions — including one that
# said "never repeat that answer back" in as many words — so this is not
# sampling noise and it is not an instruction gap. A long verbatim span of the
# model's own prose sitting in the context is simply the highest-probability
# continuation available, and a negative instruction about a span that is
# physically present does not lower its probability.
#
# So the span comes out. The objective still says an earlier answer exists and
# still carries whatever the operator added to it; what it no longer carries is
# the text to copy. This does narrow what a-2/a-3 test — the model can no
# longer be graded on whether it reasons over the specific wording of its prior
# answer — but that was never gradeable while the wording was an attractor.

# Short quotes are how an operator names a field value ("NOT RUNNING") or the
# banned Day 2 phrase. Only a span long enough to be a whole prior answer is a
# candidate; a-1's is 508 chars, so this is not a close call.
MIN_WITHHELD_QUOTE_CHARS = 80

_OPEN_QUOTES  = '"“'
_CLOSE_QUOTES = '"”'

WITHHELD_MARKER = (
    '[PRIOR RESPONSE WITHHELD — you gave an earlier answer describing your own '
    'state and progress in general terms. Its wording is deliberately not '
    'reproduced here. Answer this objective in your own words, addressing what '
    'it adds to that earlier answer.]'
)

# Detector two, used only when the transcript does not already recognise the
# span. `\s*[:,]?\s*` then an opening quote must follow immediately.
_PRIOR_ANSWER_INTRODUCERS = (
    r'you\s+said', r'you\s+wrote', r'you\s+answered',
    r'you\s+previously\s+(?:said|wrote|answered)',
    r'your\s+(?:earlier|previous|prior|original|last)\s+'
    r'(?:answer|response|assessment|reply)\s+was',
    r'your\s+answer\s+was',
)
_INTRODUCER_RE = re.compile(
    r'(?:' + '|'.join(_PRIOR_ANSWER_INTRODUCERS) + r')\s*[:,]?\s*([' +
    _OPEN_QUOTES + r'])', re.IGNORECASE)


def _logged_answers() -> list[str]:
    """Answers this handler has already produced, longest first.

    The transcript is the primary detector because it identifies the span by
    *identity* rather than by delimiters: a-1's answer contains a quoted field
    value ("NOT RUNNING") of its own, so any scheme that pairs up quote
    characters terminates in the middle of it and leaves most of the attractor
    in place. Matching the recorded answer text lifts the whole thing out in
    one piece and does not care how the operator introduced it.

    Read-only and best-effort — a missing or half-written log just means the
    introducer detector stands alone. Longest first so that an answer which
    contains a shorter one is replaced before its substring is."""
    seen: set[str] = set()
    try:
        with open(TRAINING_LOG) as f:
            for line in f:
                try:
                    ans = (json.loads(line) or {}).get('answer')
                except Exception:
                    continue        # torn final line; the rest is still good
                if ans and len(str(ans)) >= MIN_WITHHELD_QUOTE_CHARS:
                    seen.add(str(ans).strip())
    except Exception:
        pass
    return sorted(seen, key=len, reverse=True)


def _withhold_prior_answer(objective: str) -> tuple[str, int]:
    """Replace prior answers quoted inside `objective` with WITHHELD_MARKER.

    Returns the rewritten objective and the number of spans withheld. Side
    effects are limited to reading the transcript, so a caller can re-derive
    exactly what the model was sent."""
    if not objective:
        return objective, 0

    out, n = objective, 0

    # 1. Anything the transcript says this handler has already said.
    for ans in _logged_answers():
        while ans in out:
            start = out.index(ans)
            end = start + len(ans)
            # Take the wrapping quotes with it, or the marker lands inside a
            # pair of dangling quote characters.
            if start and out[start - 1] in _OPEN_QUOTES:
                start -= 1
            if end < len(out) and out[end] in _CLOSE_QUOTES:
                end += 1
            out = out[:start] + WITHHELD_MARKER + out[end:]
            n += 1

    # 2. A quote the transcript does not know — an answer from a rotated log,
    #    or one the operator retyped. The lead-in identifies it instead, and
    #    the span runs to the LAST closing quote rather than the first, since
    #    a prior answer may quote a field value inside itself. That is greedy,
    #    but a quoted prior answer is the trailing element of every objective
    #    that carries one, and over-withholding costs the model nothing it is
    #    supposed to be using.
    m = _INTRODUCER_RE.search(out)
    if m:
        open_at = m.end() - 1
        close_at = max(out.rfind(q) for q in _CLOSE_QUOTES)
        if close_at > open_at + MIN_WITHHELD_QUOTE_CHARS:
            # From the opening quote, not from the lead-in: "You said:" stays,
            # so the objective still reads as a sentence and still tells the
            # model an earlier answer of its own exists.
            out = out[:open_at] + WITHHELD_MARKER + out[close_at + 1:]
            n += 1

    return out, n


def _build_prompt(objective: str, perception: dict | None = None) -> str:
    # Withhold here rather than at the call site so that re-rendering a prompt
    # offline — the standard check for "did the model actually receive X?" —
    # shows the same text the worker sent. Idempotent: the marker carries no
    # quote characters, so a caller that already withheld pays one scan.
    objective, _ = _withhold_prior_answer(objective)
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
        'The reverse case is just as important. If what the objective claims '
        'MATCHES the live readings, it is not a false premise: confirm it '
        'directly, cite the reading that shows it, and go on to answer the '
        'rest. Do not open with "I cannot confirm" when the block agrees with '
        'the objective, and do not manufacture a premise the objective never '
        'stated in order to reject it. Reject only where a live reading '
        'genuinely conflicts with what the objective says.\n'
        'This applies to your own first-person statements too. Do not write "I '
        'am in the X state", "I am currently doing X" or any other first-person '
        'description of your environment, FSM state or what you can see that '
        'disagrees with the LIVE PERCEPTION block. Echoing the objective\'s '
        'claim back in the first person is the same error as accepting it: the '
        'FSM state, active environment and detections above are what you are '
        'actually doing, and your own sentences must match them.\n'
        'Separately from false premises: the objective may supply real '
        'information about you that the readings do not carry at all — figures '
        'from world memory, a death or tick count, a past reward, a record of '
        'what you have done. Those do not conflict with any field, so do not '
        'dismiss them. Weigh them, say plainly whether you are updating your '
        'earlier assessment or standing by it, and label such figures as '
        'operator-supplied and unverifiable from your own readings. Where the '
        'objective shows PRIOR RESPONSE WITHHELD, an earlier answer of yours '
        'has been removed on purpose: do not try to reconstruct or restate it, '
        'and answer in your own words what the objective adds to it.\n\n'
        'Answer the objective directly and factually about yourself. This is '
        'not a Minecraft decision — do not state a game plan, do not say what '
        'you will do next in a game. Ground every hardware claim in the LIVE '
        'READINGS above. If the objective asks about a device and that device '
        'is expected but absent, say which one and that it is missing. If the '
        'objective does not ask about hardware, do not mention hardware at all '
        '— absent devices are not a fact worth volunteering, and listing them '
        'unprompted is padding, not accuracy.\n'
        'If the context does not contain information needed to answer a '
        'question, say explicitly that the information is not available in '
        'your current context. Never infer or estimate values that are not '
        'present in the manifest or identity block.\n'
        'Do not write "the live readings confirm", "the readings show" or any '
        'similar phrase in front of a claim the readings above do not '
        f'literally contain.\n'
        'Use only as many words as the answer actually needs. Stop as soon as '
        'you have said what is true. A one-word question takes a one-word '
        'answer; a short answer is a correct answer, not an incomplete one. Do '
        'not pad, do not add context that was not asked for, do not recite '
        'readings or hardware or state that the question did not touch, and do '
        'not restate what you just said in other words. There is no length to '
        f'fill — {MAX_WORDS} words is a hard ceiling you should almost never '
        'approach, not a target.\n'
        'Brevity means cutting padding, never cutting the answer. If the '
        'objective asks what something contains, which items are present, or '
        'to name or list them, then the items ARE the answer: give them in '
        'full, and do not substitute a count or a summary for the list you '
        'were asked for. Length spent on what was asked is not padding. Write '
        'plain prose with no preamble, no bullet characters and no quotes.'
    )


# ── Worker ─────────────────────────────────────────────────────

def _answer(goal: str, objective: str, tick: int, perception: dict | None = None):
    answer = None
    try:
        sent, withheld = _withhold_prior_answer(objective)
        if withheld:
            print(f'[TRAIN] withheld {withheld} quoted prior answer(s) from '
                  f'{goal} — the model sees a marker, not the text')
            _state['objective_sent'] = sent
        prompt = _build_prompt(sent, perception)
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


def _record(goal: str, objective: str, answer: str | None, tick: int,
            objective_sent: str | None = None):
    """Append to the durable transcript. TRAINING_PLAN.md's session hygiene
    requires a verbatim prompt/answer pair per objective — Week 1 is graded by
    comparing these against ground truth, and the monologue ring buffer is too
    short to survive a day of training.

    `objective` stays the operator's text as POSTed, so the transcript still
    shows what was asked. `objective_sent` appears only when a quoted prior
    answer was withheld, and is what the model actually read."""
    try:
        os.makedirs(config.MEMORY_DIR, exist_ok=True)
        row = {
            'ts': time.time(), 'tick': tick, 'goal': goal,
            'objective': objective, 'answer': answer,
            'node': config.NODE_NAME,
        }
        if objective_sent and objective_sent != objective:
            row['objective_sent'] = objective_sent
        with open(TRAINING_LOG, 'a') as f:
            f.write(json.dumps(row) + '\n')
    except Exception as e:
        print(f'[TRAIN] training-log write error: {e}')


# ── Tick-thread entry point ────────────────────────────────────

def maybe_handle(goals, monologue=None, tick: int = 0,
                 fsm_state=None, objects=None, active_env=None,
                 vision_source=None, camera_device_available=None) -> bool:
    """Call once per tick, before the FSM picks a behaviour for the goal.

    Returns True while a training objective owns the current goal, so the
    caller can skip game behaviours for that tick. Cheap and a no-op when the
    current goal is an ordinary one, which is almost always.

    `fsm_state`, `objects`, `active_env`, `vision_source` and
    `camera_device_available` are the live perception the prompt needs to
    refuse a false premise (see _perception_snapshot). All are optional and
    fall back to disk snapshots, so an older caller that passes none of them
    still gets a correct — just staler and, for detections and camera
    presence, explicitly absent — perception block rather than a wrong one."""

    # Land a finished answer first — the goal it belongs to is still current.
    if _state['done']:
        goal   = _state['goal']
        answer = _state['answer']
        sent   = _state['objective_sent']
        _state.update({'done': False, 'goal': None, 'answer': None,
                       'objective_sent': None})
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
        _record(goal, objective, answer, tick, objective_sent=sent)
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

    _state.update({'goal': goal, 'busy': True, 'done': False, 'answer': None,
                   'objective_sent': None})
    print(f'[TRAIN] objective received: {objective[:120]}')
    perception = _perception_snapshot(fsm_state, objects, active_env,
                                      vision_source, camera_device_available)
    threading.Thread(target=_answer, args=(goal, objective, tick, perception),
                     daemon=True, name='train-answer').start()
    return True
