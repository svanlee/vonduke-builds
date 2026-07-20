# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Axon Voice Hub                     ║
# ║  Local, offline voice control (Whisper + pyttsx3)    ║
# ╚══════════════════════════════════════════════════════╝
#
# Always-listening, no wake word: records a rolling chunk of the default
# microphone, transcribes it with local Whisper (no cloud STT), and — if
# the transcript is at least MIN_COMMAND_WORDS long — parses it
# (axon/command_parser.py) into either a goal or a status query. Short
# transcripts (mic noise, stray words) are dropped before parsing so
# ambient sound doesn't spam the goal queue. Unrecognized transcripts
# are logged but not spoken aloud — with no wake word gating what counts
# as "directed at Axon", most listened-to chunks are just room noise or
# background conversation, and narrating "sorry, didn't catch that" for
# each one would make the assistant unbearable to be around.
#
# Goals are delivered into data/injected_goals.json — the same queue file
# mastermind/agent_client.py uses to hand goals to a running AKSUMAEL
# instance — which memory.goals.GoalStack.check_injected_goals() already
# drains once per runtime tick. Axon doesn't need its own polling path
# in core/runtime.py; it just writes into the existing one.
#
# Runs as its own process, independent of the main vision/action loop
# (see tools/start_axon.sh / tools/systemd/axon.service).

import json
import os
import re
import time

import config
from memory.goals import INJECTED_GOALS_PATH, GOALS_PATH
from memory import MemoryContext
from axon.command_parser import parse, parse_deterministic
from axon.speaker import Speaker
from audio.device_probe import select_devices, alsa_card
from core.llm_router import route_llm_call
from core.cognitive import InnerMonologue
from envs.attention import AttentionManager

LISTEN_CHUNK_SEC  = 4.0  # rolling recording window, always on
MIN_COMMAND_WORDS = 3    # drop shorter transcripts as noise/false triggers
SAMPLE_RATE       = 16000  # Whisper's native rate

# Voice Q&A (v1.4) — a transcript containing any of these reads as a
# question rather than a command, and gets answered out loud via mesh-llm
# instead of being force-fit into a goal by _parse_with_local_llm(). Checked
# via axon.command_parser.parse_deterministic() first so a transcript that
# matches a real rule/status pattern (e.g. "what's your status") never gets
# diverted here — see _handle_command below.
QA_KEYWORDS = ('what', 'why', 'how', 'are you', "what's", 'tell me', 'explain')

# Give the mic a moment to fully release before speaking, so the tail of
# the question isn't clipped by TTS starting mid-breath.
QA_ANSWER_PAUSE_SEC = 1.5

# Multi-environment attention (envs/attention.py) voice switch — "switch to
# <env>" / "focus on <env>" moves core/runtime.py's AttentionManager onto a
# different envs/*.py adapter. Several spoken aliases map to each of the
# three env names AttentionManager/runtime.py actually use (see
# core/runtime.py's _attention_envs and envs/*_env.py's get_env_name()).
ENV_SWITCH_ALIASES = {
    'minecraft':  ('minecraft',),
    'vehicle':    ('vehicle', 'goat racer', 'goat racer one', 'the car', 'race car'),
    'robocar':    ('robocar', 'robo car', 'ak-01', 'ak01', 'the rover'),
}
_ENV_SWITCH_PATTERN = re.compile(
    r'\b(?:switch|change|move|focus)(?: your)?(?: attention)? (?:to|on)\s+(.+)',
    re.IGNORECASE,
)


def _match_env_switch(transcript: str) -> str | None:
    """Return the canonical env name AttentionManager expects if `transcript`
    reads as a "switch to <env>" voice command, else None."""
    m = _ENV_SWITCH_PATTERN.search(transcript or '')
    if not m:
        return None
    target = m.group(1).strip('. ').lower()
    for env_name, aliases in ENV_SWITCH_ALIASES.items():
        if any(alias in target for alias in aliases):
            return env_name
    return None


def _looks_like_question(text: str) -> bool:
    t = (text or '').lower()
    return any(kw in t for kw in QA_KEYWORDS)


def _enqueue_goal(goal: str, reason: str):
    """Append to data/injected_goals.json in the same {"queue": [...]}
    format mastermind/agent_client.py writes, so GoalStack.check_injected_goals()
    drains voice commands exactly like hive-assigned goals."""
    os.makedirs(os.path.dirname(INJECTED_GOALS_PATH) or ".", exist_ok=True)
    queue = []
    if os.path.exists(INJECTED_GOALS_PATH):
        try:
            with open(INJECTED_GOALS_PATH) as f:
                queue = json.load(f).get("queue", [])
        except (OSError, json.JSONDecodeError):
            queue = []
    queue.append({
        "goal": goal,
        "reason": reason,
        "received_at": time.time(),
    })
    with open(INJECTED_GOALS_PATH, "w") as f:
        json.dump({"queue": queue}, f)
    print(f'[AXON] queued goal "{goal}" ({reason})')


def _read_current_goal() -> str:
    """Read AKSUMAEL's current goal straight off disk — runtime.py owns
    the live GoalStack, Axon runs as a separate process."""
    try:
        with open(GOALS_PATH) as f:
            return json.load(f).get("current", "unknown")
    except (OSError, json.JSONDecodeError):
        return "unknown"


class AxonHub:
    def __init__(self):
        self._in_device = None
        self._out_device = None
        self._select_audio_devices()
        self.speaker = Speaker(alsa_device=alsa_card(self._out_device),
                                out_device_idx=self._out_device_idx)
        self._model = None
        self.memory_context = MemoryContext()
        # No real adapters here — Axon runs as its own process (see this
        # module's docstring) and never touches capture-card/ZeroMQ/ROS2
        # hardware itself. This instance only exists so .focus() persists
        # a switch request to envs/attention.py's FOCUS_STATE_PATH, which
        # core/runtime.py's real AttentionManager (holding the live
        # adapters) picks up via sync_external_focus() on its next tick.
        self.attention_manager = AttentionManager(envs={})
        self.enabled = self._probe()

    def _select_audio_devices(self):
        in_idx, in_dev, out_idx, out_dev = select_devices()
        self._in_device_idx  = in_idx
        self._in_device      = in_dev
        self._out_device_idx = out_idx
        self._out_device     = out_dev

    def _probe(self) -> bool:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as e:
            print(f'[AXON] audio library not available ({e}) — hub disabled')
            return False
        try:
            import whisper  # noqa: F401
        except ImportError as e:
            print(f'[AXON] whisper not installed ({e}) — hub disabled')
            return False

        try:
            devices = sd.query_devices()
        except Exception as e:
            print(f'[AXON] cannot query audio devices: {e} — hub disabled')
            return False
        if not any(d['max_input_channels'] > 0 for d in devices):
            print('[AXON] no microphone found — hub disabled')
            return False
        return True

    def _load_model(self):
        import whisper
        model_name = config.AXON_WHISPER_MODEL
        print(f'[AXON] loading whisper model "{model_name}" ...')
        self._model = whisper.load_model(model_name)
        print('[AXON] whisper ready')

    def _record(self, seconds: float):
        import sounddevice as sd
        frames = int(seconds * SAMPLE_RATE)
        audio = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1,
                        dtype='float32', device=self._in_device_idx, blocking=True)
        return audio.flatten()

    def _transcribe(self, audio) -> str:
        result = self._model.transcribe(audio, fp16=False, language='en')
        return result.get('text', '').strip()

    def _handle_command(self, transcript: str):
        # Env-switch ("switch to minecraft" / "focus on GOAT Racer" / ...)
        # checked before anything else — it's neither a goal nor a status
        # query, and free-text env names like "GOAT Racer" would otherwise
        # risk getting force-fit into a goal by the local-LLM fallback below.
        env_name = _match_env_switch(transcript)
        if env_name is not None:
            print(f'[AXON] command: "{transcript}"')
            if self.attention_manager.focus(env_name):
                self.speaker.say(f"Switching attention to {env_name}.")
            else:
                self.speaker.say(f"I don't know an environment called {env_name}.")
            return

        # Deterministic (free) status/rule matches first — cheap regex,
        # unchanged behavior. Only a transcript that matches NEITHER falls
        # through to the question check below, before ever reaching
        # parse()'s local-LLM goal-fallback (_parse_with_local_llm) — that
        # fallback would otherwise burn a mesh-llm call trying to force a
        # question like "what is a creeper" into a snake_case goal.
        det = parse_deterministic(transcript)

        if det is not None and det['type'] == 'query' and det['query'] == 'status':
            print(f'[AXON] command: "{transcript}"')
            goal = _read_current_goal().replace('_', ' ')
            self.speaker.say(f"I'm currently working on: {goal}.")
            return

        if det is not None and det['type'] == 'goal':
            print(f'[AXON] command: "{transcript}"')
            _enqueue_goal(det['goal'], f"axon:{det['source']} — \"{transcript}\"")
            self.speaker.say(f"Got it. {det['goal'].replace('_', ' ')}.")
            return

        if _looks_like_question(transcript):
            self._answer_question(transcript)
            return

        # No rule matched and it doesn't read as a question — last resort,
        # ask the local LLM to interpret it as a free-form goal command.
        intent = parse(transcript)
        if intent['type'] == 'goal':
            print(f'[AXON] command: "{transcript}"')
            _enqueue_goal(intent['goal'], f"axon:{intent['source']} — \"{transcript}\"")
            self.speaker.say(f"Got it. {intent['goal'].replace('_', ' ')}.")
            return

        # Not recognized as a command — with no wake word, this is most
        # likely ambient speech, not a failed request. Log only, stay quiet.
        print(f'[AXON] heard (unrecognized): "{transcript}"')

    def _answer_question(self, question: str):
        """Answer a spoken question out loud via mesh-llm, using the same
        self-built memory system (memory/context.py) that feeds the
        overseer — episodic/semantic/procedural memory plus the current
        FSM state, goal, health/hunger, and recent inner monologue (all
        read cross-process off disk, since Axon runs as its own separate
        process — see this module's docstring)."""
        print(f'[AXON] question: "{question}"')
        time.sleep(QA_ANSWER_PAUSE_SEC)

        context = self.memory_context.build_context_for_llm()
        prompt = (
            f'{context}\n\n'
            f'Someone just asked you: "{question}"\n'
            'Answer in one or two short spoken sentences, as AKSUMAEL, '
            'using the context above where relevant. No markdown, just '
            'the words to speak.'
        )
        raw, provider = route_llm_call(prompt, max_tokens=300, timeout=config.LOCAL_LLM_TIMEOUT)
        if not raw:
            self.speaker.say("I'm not sure — my thinking module isn't responding right now.")
            return

        answer = raw.strip()
        self.speaker.say(answer)
        # Surface the answer on the monologue strip too. core.capture's
        # push_monologue_line() is an in-process queue tied to the main
        # runtime's DisplayThread — useless from this separate process — so
        # instead write straight to the same file InnerMonologue persists
        # to; its recent() reloads from disk each call, so this shows up in
        # the overlay on the main process's next tick.
        InnerMonologue().push_external(answer)

    def run(self):
        if not self.enabled:
            print('[AXON] hub cannot start — see errors above')
            return

        self._load_model()
        print('[AXON] listening (always-on, no wake word)')

        while True:
            try:
                chunk = self._record(LISTEN_CHUNK_SEC)
                transcript = self._transcribe(chunk).strip()
                if not transcript or len(transcript.split()) < MIN_COMMAND_WORDS:
                    continue
                self._handle_command(transcript)
            except KeyboardInterrupt:
                print('[AXON] shutting down')
                break
            except Exception as e:
                print(f'[AXON] loop error: {e}')
                time.sleep(1.0)


def run():
    AxonHub().run()


if __name__ == '__main__':
    run()
