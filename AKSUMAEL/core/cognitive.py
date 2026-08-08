# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Inner Monologue                    ║
# ║  Self-generated thought, persisted as JSON, fed back  ║
# ║  into the vision-LLM prompt as planning context.      ║
# ╚══════════════════════════════════════════════════════╝
#
# Formerly also housed BeliefState, GoalStack, and EpisodicMemory
# "cognitive architecture" stubs. Removed: none of the three were ever
# read back by anything (grep confirmed no caller outside this file and
# test_cognitive.py touched cognitive.belief / cognitive.goals /
# cognitive.episodic) — each was a pure write-to-JSON-every-tick no-op.
# GoalStack's reactive threat/opportunity rules (creeper -> flee,
# diamond_ore -> mine_diamond, ...) duplicated what core/fsm.py already
# does directly and faster from the same YOLO detections, and its own
# EpisodicMemory duplicated (under a confusingly identical name) the
# real, actually-used core/episode_memory.EpisodeMemory. Real goal
# tracking lives in memory/goals.GoalStack; real episode memory lives in
# core/episode_memory.EpisodeMemory. This file now only does the one
# thing that ever fed back into a decision: the inner monologue.

import json
import os
import re
import threading
import time

import config
from core.identity import AKSUMAEL_IDENTITY
from core.llm_router import route_llm_call
from core.capture import push_monologue_line
from core.honcho_context import get_honcho, default_session_id
from core.telegram_channel import get_channel

COGNITIVE_DIR = 'data/cognitive'
MAX_THOUGHTS  = 50

# ── Repetition gate ────────────────────────────────────────────
# The monologue prompt is nearly identical tick to tick — same goal, same
# visible labels, same reward — so a local model will happily emit the same
# sentence forever. Observed in the wild as 10+ consecutive ticks of "I need
# to gather wood and food before I can craft the crafting table", which is
# not a thought, it's a stuck loop, and it poisons the planning context that
# reads it back.
#
# After REPEAT_LIMIT identical thoughts in a row, the next prompt carries an
# explicit break instruction. Cheap, local, and it can't wedge: the gate is
# computed fresh from the thought log on every generation, so one different
# sentence clears it.
REPEAT_LIMIT  = 3
REPEAT_NUDGE  = 'Try something different.'


def _thought_key(text: str) -> str:
    """Comparison form of a thought — case, whitespace and trailing
    punctuation folded away.

    Strict equality would miss the actual failure mode: the model re-emits
    the same sentence with a different final period or a stray capital, and
    a byte-comparison would read that as progress.
    """
    return re.sub(r'\s+', ' ', (text or '').strip().lower()).strip('.!? ')


def _load(path, default):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            print(f'[COGNITIVE] load error {path}: {e}')
    return default


def _save(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f'[COGNITIVE] save error {path}: {e}')


class InnerMonologue:
    """One self-generated thought, kicked off every config.MONOLOGUE_EVERY_N_SECONDS
    of wall-clock time (cheap haiku call, ~50 tokens). Gated on wall-clock
    time rather than tick count because tick duration varies from ~0.5s to
    30-40s (LLM-call ticks), so a tick-count gate made the refresh cadence
    wildly unpredictable in practice — sometimes minutes between updates
    (see 2026-07-19, black-strip caption in ui/labeling.py going stale).

    The actual generation call (core.llm_router.route_llm_call, up to
    config.LOCAL_LLM_TIMEOUT=40s) runs in a background daemon thread —
    update() itself only ever kicks the thread off and returns immediately,
    since it's called every tick from the main loop and a 40s local-LLM
    stall there would freeze the display exactly like the KB2040 UART
    write and pre-queue TTS calls did earlier (2026-07-19).

    The most recent real thought is fed back into the next vision-LLM
    planning call as extra context.

    Phase 3 added two side channels, both hanging off this same
    background thread because both are blocking network I/O that must
    never touch the tick loop:

      - **Honcho** (core/honcho_context.py) supplies a cross-session
        self-model, prepended to the prompt as a "Memory:" block and
        refreshed on its own 30s cadence, and receives each generated
        thought so the deriver can build on it.
      - **Telegram** (core/telegram_channel.py) supplies Scott's inbound
        messages, injected as "Scott says:" lines. When Scott has spoken,
        the resulting thought is sent back to him *and* the cadence gate
        is bypassed so he isn't waiting on the next scheduled tick. When
        he hasn't, nothing is sent — the monologue fires every few
        seconds and would otherwise be a firehose."""

    FILE = f'{COGNITIVE_DIR}/inner_monologue.json'

    def __init__(self):
        self.thoughts = _load(self.FILE, [])
        self.claude_call_count = 0      # session total, for health reporting
        self._last_kickoff_ts = 0.0
        self._generating = False
        self._lock = threading.Lock()
        self.session_id = default_session_id()
        self.honcho     = get_honcho()
        self.telegram   = get_channel()
        self._last_honcho_write = 0.0

    def update(self, tick: int, objects: list, action_dict: dict, reward: float,
               goal: str = None, recent_episodes: list = None):
        if self._generating:
            return
        now = time.time()
        # has_pending() is a queue peek, not a drain — a message is never
        # consumed on a tick that then declines to handle it.
        scott_spoke = self.telegram.has_pending()
        if not scott_spoke and now - self._last_kickoff_ts < config.MONOLOGUE_EVERY_N_SECONDS:
            return
        incoming = self.telegram.get_pending_messages() if scott_spoke else []
        self._last_kickoff_ts = now
        self._generating = True
        threading.Thread(
            target=self._generate_and_store,
            args=(tick, objects, action_dict, reward, goal, recent_episodes, incoming),
            daemon=True, name='monologue',
        ).start()

    def _generate_and_store(self, tick, objects, action_dict, reward, goal,
                             recent_episodes, incoming=None):
        incoming = incoming or []
        try:
            # Persist Scott's side first so the thought that answers it is
            # already downstream of it in the session history.
            for m in incoming:
                self.honcho.add_message('scott', m.text, self.session_id)
            memory = self.honcho.get_context(
                self.session_id,
                search_query=incoming[-1].text if incoming else None)
            thought = self._generate_llm(objects, action_dict, reward, goal,
                                          recent_episodes, memory=memory,
                                          incoming=incoming)
            if thought is None:
                thought = self._compose(objects, action_dict, reward)
            with self._lock:
                self.thoughts.append({'tick': tick, 'ts': time.time(), 'thought': thought})
                self.thoughts = self.thoughts[-MAX_THOUGHTS:]
                snapshot = list(self.thoughts)
            _save(self.FILE, snapshot)
            push_monologue_line(thought)
            # Reply only when spoken to; the monologue itself is not a
            # notification stream.
            if incoming:
                self.telegram.send_message(thought)
            self._persist_thought(thought, forced=bool(incoming))
        except Exception as e:
            print(f'[COGNITIVE] monologue generation error: {e}')
        finally:
            self._generating = False

    def _persist_thought(self, thought: str, forced: bool = False):
        """Write the thought into Honcho, rate-limited.

        The monologue fires every config.MONOLOGUE_EVERY_N_SECONDS (8s).
        Handing Honcho a message that often would keep the deriver
        permanently busy, and each derivation holds mesh-llm — the single
        shared llama.cpp instance — for 2-7s, stalling vision inference
        (docs/HONCHO_SPIKE.md, "The real cost: GPU contention"). So the
        routine path writes at most one thought per
        config.HONCHO_WRITE_EVERY_N_SECONDS. A thought that answers Scott
        is always written: dropping half a conversation from the session
        history is worse than the GPU cost of one derivation."""
        now = time.time()
        every = getattr(config, 'HONCHO_WRITE_EVERY_N_SECONDS', 120)
        if not forced and now - self._last_honcho_write < every:
            return
        if self.honcho.add_message('assistant', thought, self.session_id):
            self._last_honcho_write = now

    def _is_repeating(self) -> bool:
        """True if the last REPEAT_LIMIT thoughts are all the same one."""
        with self._lock:
            recent = [t.get('thought') for t in self.thoughts[-REPEAT_LIMIT:]]
        if len(recent) < REPEAT_LIMIT:
            return False
        keys = [_thought_key(t) for t in recent]
        # An empty key means a blank thought — those are not repetition,
        # and treating them as such would fire the nudge on a fresh log.
        return bool(keys[0]) and len(set(keys)) == 1

    def _compose(self, objects: list, action_dict: dict, reward: float) -> str:
        labels = [o.get('label') for o in objects if o.get('label')]
        seen   = f"I see {', '.join(labels)}." if labels else "I don't see anything notable."
        action = action_dict.get('action', 'wait')
        mood   = 'good' if reward > 0 else 'bad' if reward < 0 else 'neutral'
        return f"{seen} I chose to {action}. That felt {mood} (r={reward:+.2f})."

    def _generate_llm(self, objects: list, action_dict: dict, reward: float,
                       goal: str = None, recent_episodes: list = None,
                       memory: str = None, incoming: list = None) -> str | None:
        if not config.LOCAL_LLM_ENABLED:
            return None
        labels = [o.get('label') for o in objects if o.get('label')]
        fails  = ''
        if recent_episodes:
            bad = [e.get('goal') for e in recent_episodes[-3:] if e.get('outcome') != 'success']
            if bad:
                fails = f' Recent failures: {", ".join(bad)}.'
        # Honcho's cross-session self-model goes in front of the task
        # framing so the model reads it as standing context rather than
        # as part of the current situation.
        mem_block = f'Memory:\n{memory}\n\n' if memory else ''
        if incoming:
            said = '\n'.join(f'Scott says: {m.text}' for m in incoming)
            task = (
                f'{said}\n'
                'You are the inner monologue of a Minecraft AI, and Scott just spoke '
                'to you. In ONE short sentence (max 25 words), answer him directly. '
            )
        else:
            task = (
                'You are the inner monologue of a Minecraft AI. In ONE short sentence '
                '(max 20 words), think out loud about what to do next. '
            )
        # Repetition gate. Placed after the situation and before the output
        # instruction so it reads as the most recent thing said, which is
        # where a small local model actually weights it. Skipped when Scott
        # is being answered — repetition there is his conversation, not a
        # stuck loop, and telling the model to change the subject mid-reply
        # would make it answer a question he did not ask.
        nudge = ''
        if not incoming and self._is_repeating():
            last = ''
            with self._lock:
                if self.thoughts:
                    last = (self.thoughts[-1].get('thought') or '').strip()
            print(f'[COGNITIVE] monologue repeated {REPEAT_LIMIT}x — nudging: {last[:60]!r}')
            nudge = (f'You have now said "{last}" {REPEAT_LIMIT} times in a row. '
                     f'{REPEAT_NUDGE} Do not repeat that sentence or its meaning; '
                     f'name a concrete next action instead. ')
        prompt = (
            f'{AKSUMAEL_IDENTITY}\n'
            f'{mem_block}'
            f'{task}'
            f'Current goal: {goal or "explore"}. Visible: {", ".join(labels) or "nothing"}. '
            f'Last reward: {reward:+.2f}.{fails} '
            f'{nudge}'
            'Respond with only the sentence, no quotes, no preamble.'
        )
        # Generous budget — the model 'thinks' before answering, which can
        # burn several hundred tokens before the actual sentence.
        raw, provider = route_llm_call(prompt, max_tokens=800, timeout=45)
        if provider == 'claude':
            self.claude_call_count += 1
        return raw.strip() if raw else None

    def push_external(self, text: str):
        """Append a thought generated outside the normal update() cadence
        (e.g. an Axon voice Q&A answer — see axon/hub.py) directly to disk
        in the same schema update() writes. axon/hub.py runs as its own
        separate process (see its module docstring), so it has no access
        to this live InnerMonologue instance or to core.capture's
        in-process push_monologue_line() queue — writing straight to
        FILE is the only way its answer reaches this process. recent()
        reloads from disk every call (see below), so it shows up in the
        overlay strip on the next tick without any other plumbing."""
        text = (text or '').strip()
        if not text:
            return
        with self._lock:
            self.thoughts = _load(self.FILE, self.thoughts)
            self.thoughts.append({'tick': None, 'ts': time.time(), 'thought': text})
            self.thoughts = self.thoughts[-MAX_THOUGHTS:]
            _save(self.FILE, self.thoughts)

    def recent(self, n: int = 5) -> str:
        with self._lock:
            # Reload from disk rather than trusting self.thoughts alone —
            # push_monologue_line() (below) appends from axon/hub.py's
            # separate process, so a fresh line from there only becomes
            # visible here (and to ui/labeling.py's overlay strip) by
            # re-reading the shared file each call. The file is tiny
            # (MAX_THOUGHTS entries), so this is cheap enough for a
            # once-per-tick read.
            self.thoughts = _load(self.FILE, self.thoughts)
            return '\n'.join(t['thought'] for t in self.thoughts[-n:])


class CognitiveArchitecture:
    """Thin wrapper around InnerMonologue, called once per tick with the
    same signals already flowing through the runtime loop (objects,
    action_dict, reward, goal, recent_episodes)."""

    def __init__(self):
        self.monologue = InnerMonologue()

    def update(self, tick: int, objects: list, action_dict: dict, reward: float,
               goal: str = None, recent_episodes: list = None):
        self.monologue.update(tick, objects, action_dict, reward,
                               goal=goal, recent_episodes=recent_episodes)
