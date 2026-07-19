# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Axon Voice Hub                     ║
# ║  Local, offline voice control (Whisper + pyttsx3)    ║
# ╚══════════════════════════════════════════════════════╝
#
# Listens on the default microphone continuously. When the wake word
# ("Aksumael" / "Axon") is heard, records the next 5 seconds and
# transcribes it with local Whisper (no cloud STT). The transcript is
# parsed (axon/command_parser.py) into either a goal or a status query.
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
import time

import config
from memory.goals import INJECTED_GOALS_PATH, GOALS_PATH
from axon.command_parser import parse
from axon.speaker import Speaker
from audio.device_probe import select_devices, alsa_card

WAKE_CHUNK_SEC = 3.0    # rolling window while waiting for the wake word
COMMAND_SEC    = 5.0    # recording length after the wake word fires
SAMPLE_RATE    = 16000  # Whisper's native rate


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
        self.speaker = Speaker(alsa_device=alsa_card(self._out_device))
        self._model = None
        self.enabled = self._probe()

    def _select_audio_devices(self):
        in_idx, in_dev, out_idx, out_dev = select_devices()
        self._in_device_idx = in_idx
        self._in_device      = in_dev
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
        print(f'[AXON] command: "{transcript}"')
        intent = parse(transcript)

        if intent['type'] == 'query' and intent['query'] == 'status':
            goal = _read_current_goal().replace('_', ' ')
            self.speaker.say(f"I'm currently working on: {goal}.")
            return

        if intent['type'] == 'goal':
            _enqueue_goal(intent['goal'], f"axon:{intent['source']} — \"{transcript}\"")
            self.speaker.say(f"Got it. {intent['goal'].replace('_', ' ')}.")
            return

        self.speaker.say("Sorry, I didn't catch a command in that.")

    def run(self):
        if not self.enabled:
            print('[AXON] hub cannot start — see errors above')
            return

        self._load_model()
        wake_words = [w.lower() for w in config.AXON_WAKE_WORDS]
        print(f'[AXON] listening for wake word: {", ".join(wake_words)}')

        while True:
            try:
                chunk = self._record(WAKE_CHUNK_SEC)
                heard = self._transcribe(chunk).lower()
                if not heard:
                    continue
                if any(w in heard for w in wake_words):
                    print(f'[AXON] wake word detected in: "{heard}"')
                    command_audio = self._record(COMMAND_SEC)
                    transcript = self._transcribe(command_audio)
                    if transcript:
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
