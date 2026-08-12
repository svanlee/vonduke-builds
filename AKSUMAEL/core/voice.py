# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Voice (in-process, absorbed Axon)  ║
# ║  Whisper STT + piper TTS + F9 push-to-talk           ║
# ╚══════════════════════════════════════════════════════╝
#
# This is axon/hub.py + axon/speaker.py + axon/command_parser.py folded
# into a daemon thread of the *main* AKSUMAEL process, replacing the
# separate axon.service.
#
# Why the fold happened (2026-08-08): axon.service and aksumael.service
# are two processes on one box, and both open audio devices — Axon holds
# the mic for Whisper and the speaker for piper, while the main runtime's
# audio/game_ear.py holds the mic for game audio and audio/tts.py holds
# the speaker for persona lines. PortAudio/ALSA hands out those devices
# first-come-first-served, so whichever process booted first won and the
# other silently degraded (GameEar disabling itself, or piper failing to
# open an output stream). One process means one arbiter.
#
# What the fold buys beyond ending that fight — Axon ran cross-process, so
# everything it wanted to know or say had to go through disk:
#   * status queries re-read data/goals.json instead of the live GoalStack
#   * Q&A answers were written to InnerMonologue's JSON file with
#     push_external() because core.capture's in-process monologue queue
#     was unreachable
#   * "switch to <env>" persisted a focus request to disk for the real
#     AttentionManager to notice on its next tick
# All three are now direct object calls (see VoiceThread.__init__ args).
#
# The one thing deliberately NOT made direct is goal injection. GoalStack
# has no lock and core/runtime.py mutates it (goals.current, push/pop,
# retirement) every tick on the main thread — pushing from this thread
# would race that, and would also skip the authority gating in
# GoalStack.check_injected_goals(). So voice goals still land in
# data/injected_goals.json, which the tick loop drains at a safe point,
# exactly as mastermind/agent_client.py's do.
#
# Listening modes (data/voice_mode.txt, falling back to the older
# data/axon_mode.txt so `python axon/set_mode.py ptt|on|off` keeps
# working): "on" (default, always-listening, VAD-segmented), "ptt" (F9
# push-to-talk), "off" (no mic access at all).
#
# "on" is the default and the intended way to use this: talking to the bot
# should not require reaching for a keyboard it doesn't have focus on. The
# mic is held open continuously and a voice-activity detector cuts it into
# utterances at natural pauses, so a sentence is transcribed once, whole,
# when the speaker stops — rather than the pre-2026-08-08 behavior of
# transcribing a fixed 4-second window on a clock that had no idea where
# speech started or ended (every utterance either truncated mid-word or
# padded with silence, and whisper hallucinated captions on the silence).
#
# PTT is the fallback: for a noisy room, and automatically whenever webrtcvad
# isn't installed — see vad_backend_available(). Always-on without a real
# speech classifier means an RMS energy gate, which game audio on the same
# speakers trips continuously, so a key press is the better degradation.

import json
import os
import queue
import re
import threading
import time

import config

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MIN_COMMAND_WORDS = 3      # drop shorter transcripts as noise/false triggers
SAMPLE_RATE       = 16000  # Whisper's native rate

# ── Voice activity detection ───────────────────────────────────────────────
# webrtcvad only accepts 10/20/30 ms frames of 16-bit mono PCM at 8/16/32/48
# kHz. 30 ms is the coarsest, which is what we want: fewest callbacks, and
# silence timeouts are measured in hundreds of ms anyway.
VAD_FRAME_MS      = 30
VAD_FRAME_SAMPLES = SAMPLE_RATE * VAD_FRAME_MS // 1000   # 480 @ 16 kHz

# Fraction of the trigger window that must be voiced to open an utterance.
# A single voiced frame is 30 ms and a door click clears that easily, so the
# open/close decisions are both made over a window rather than per-frame.
VAD_TRIGGER_RATIO  = 0.6
VAD_TRIGGER_WINDOW = 8       # frames (~240 ms) the ratio is measured over

# Bounded so a stuck consumer can't grow this without limit. ~30 s of audio;
# on overflow the oldest frame is dropped, because during a long whisper
# transcription the newest audio is the audio someone is speaking now.
VAD_QUEUE_FRAMES = 1000

# ── Listening modes ────────────────────────────────────────────────────────
MODE_PTT     = "ptt"
MODE_ON      = "on"
MODE_OFF     = "off"
VALID_MODES  = (MODE_PTT, MODE_ON, MODE_OFF)
DEFAULT_MODE = MODE_ON

MODE_FILE_PATH        = os.path.join(BASE_DIR, "data", "voice_mode.txt")
LEGACY_MODE_FILE_PATH = os.path.join(BASE_DIR, "data", "axon_mode.txt")
MODE_POLL_SEC         = 2.0   # how often the run loop re-reads the mode file

# Goals AKSUMAEL actually knows how to pursue. The mesh-llm fallback in
# _parse_with_local_llm() has free rein over transcript wording and can
# hallucinate a plausible-looking but nonexistent goal (e.g.
# "protect_enchanting_table" from a misheard transcript) — _enqueue_goal()
# is the single choke point both the deterministic parser and the LLM
# fallback funnel through, so the whitelist lives there.
VALID_GOALS = frozenset({
    "find_and_chop_tree", "mine_stone", "mine_iron", "mine_diamonds",
    "craft_wood_pickaxe", "craft_stone_pickaxe", "craft_iron_pickaxe",
    "explore", "rebuild_fort", "return_to_base",
    "dig_up", "escape_underground",
})

# A transcript containing any of these reads as a question rather than a
# command, and gets answered out loud via the LLM instead of being force-fit
# into a goal. Checked only after _parse_deterministic() so a transcript that
# matches a real rule/status pattern ("what's your status") never lands here.
QA_KEYWORDS = ('what', 'why', 'how', 'are you', "what's", 'tell me', 'explain')

# Give the mic a moment to fully release before speaking, so the tail of the
# question isn't clipped by TTS starting mid-breath.
QA_ANSWER_PAUSE_SEC = 1.5


# ── Command parsing (was axon/command_parser.py) ───────────────────────────
# (regex, goal, priority) — checked in order, first match wins. Goal names
# line up with memory.goals.GOAL_PRIORITIES where possible so they slot
# straight into the existing goal stack / retirement logic.
_RULES = [
    (r"\bmine (some |for )?diamonds?\b|\bgo mine diamonds?\b|\bfind diamonds?\b|\bdig for diamonds?\b", "mine_diamonds", 10),
    (r"\bmine (some |for )?coal\b|\bgo mine coal\b", "mine_coal", 10),
    (r"\b(come back|return|head back|get back)( to)? (the )?base\b|\bcome home\b|\bgo home\b", "return_to_base", 10),
    (r"\bstop\b|\bhalt\b|\bfreeze\b|\bcancel that\b|\bstand down\b", "idle", 99),
    (r"\bfind shelter\b|\bbuild (a )?shelter\b|\btake cover\b|\bhide\b", "find_shelter", 8),
    (r"\bexplore\b|\blook around\b|\bscout\b|\bgo explore\b", "explore", 5),
    (r"\bfind food\b|\bget (some )?food\b|\bgo eat\b|\bfind something to eat\b", "find_food", 8),
    (r"\bcraft (a |an )?(wood|wooden) pickaxe\b", "craft_wood_pickaxe", 6),
    (r"\bcraft (a |an )?stone pickaxe\b", "craft_stone_pickaxe", 6),
    (r"\bcraft (a |an )?iron pickaxe\b", "craft_iron_pickaxe", 6),
    (r"\bcraft (a |an )?diamond pickaxe\b", "craft_diamond_pickaxe", 6),
]
_COMPILED_RULES = [(re.compile(p, re.IGNORECASE), goal, pr) for p, goal, pr in _RULES]

_STATUS_PATTERN = re.compile(
    r"\bwhat are you doing\b|\bwhat'?s your status\b|\bstatus report\b|"
    r"\bwhat'?s the plan\b|\bwhat'?s your goal\b|\bcurrent goal\b",
    re.IGNORECASE,
)


def parse_deterministic(transcript: str) -> dict | None:
    """Try only the free (non-LLM) status/rule matches. Returns None if
    nothing matched, rather than falling through to the LLM goal-parse —
    callers use that None to decide whether a transcript should be treated
    as a question before ever paying for a local-LLM call trying to force
    it into a goal."""
    text = (transcript or "").strip()
    if not text:
        return {"type": "unknown"}

    if _STATUS_PATTERN.search(text):
        return {"type": "query", "query": "status"}

    for pattern, goal, priority in _COMPILED_RULES:
        if pattern.search(text):
            return {"type": "goal", "goal": goal, "priority": priority, "source": "rule"}

    return None


def parse(transcript: str) -> dict:
    """Parse a voice transcript into a structured intent:
      {"type": "goal",  "goal": str, "priority": int, "source": "rule"|"local_llm"}
      {"type": "query", "query": "status"}
      {"type": "unknown"}
    """
    text = (transcript or "").strip()
    if not text:
        return {"type": "unknown"}
    det = parse_deterministic(text)
    if det is not None:
        return det
    return _parse_with_local_llm(text)


def _parse_with_local_llm(text: str) -> dict:
    """Single routed LLM call for free-form commands the rules didn't catch.
    At most one call per command; never retries."""
    if not config.LOCAL_LLM_ENABLED:
        print('[VOICE] LLM routing disabled — cannot parse free-form command')
        return {"type": "unknown"}

    from core.llm_router import route_llm_call

    prompt = f"""You are AKSUMAEL, an autonomous AI engineering assistant. Turn the voice command
below into a short snake_case goal name (e.g. explore, find_food, return_to_base,
gather_resources, find_shelter, idle) and a priority
1-10 (10 = most urgent, drop everything else).

Voice command: "{text}"

Respond with JSON only, no other text: {{"goal": "snake_case_goal", "priority": 1-10}}"""

    # Generous budget — the model 'thinks' before answering, which can burn
    # several hundred tokens before the actual JSON reply.
    raw, _provider = route_llm_call(prompt, max_tokens=800, timeout=45)
    if not raw:
        return {"type": "unknown"}
    try:
        parsed = json.loads(raw)
        goal = parsed.get('goal')
        if not goal:
            return {"type": "unknown"}
        return {"type": "goal", "goal": goal,
                "priority": int(parsed.get('priority', 5)), "source": "local_llm"}
    except Exception as e:
        print(f'[VOICE] LLM parse error: {e}')
        return {"type": "unknown"}


# ── Env switching ──────────────────────────────────────────────────────────
# "switch to <env>" / "focus on <env>" moves core/runtime.py's
# AttentionManager onto a different envs/*.py adapter. Several spoken
# aliases map to each of the three env names AttentionManager/runtime.py
# actually use (see envs/*_env.py's get_env_name()).
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


# ── Voice-to-label ─────────────────────────────────────────────────────────
# Phrases like "that is a tree", "that's lava", "label this bedrock",
# "this is a bee" save the current live frame as a labeled training sample.
_LABEL_PATTERNS = re.compile(
    r"(?:that(?:'s| is)(?: a| an)?|this is(?: a| an)?|label this(?: as)?|"
    r"label as|mark this(?: as)?|call this(?: a| an)?|"
    r"orbit this(?: as)?|survey this(?: as)?|scan this(?: as)?)\s+([a-z][a-z _]+[a-z])",
    re.IGNORECASE,
)
_FRAME_SERVER_URL  = "http://localhost:8765/frame"
_LABEL_DATASET_IMG = os.path.join(BASE_DIR, "data", "yolo_dataset", "images", "train")
_LABEL_DATASET_LBL = os.path.join(BASE_DIR, "data", "yolo_dataset", "labels", "train")

# Synonyms Scott might say → canonical class name
_LABEL_SYNONYMS: dict[str, str] = {
    "tree": "log",
    "oak tree": "log",
    "birch tree": "birch_log",
    "iron": "iron_ore",
    "diamond": "diamond_ore",
    "gold": "gold_ore",
    "coal": "coal_ore",
    "fire": "fire_hazard",
    "bee hive": "bee",
    "beehive": "bee",
    "bees": "bee",
    "villager house": "village_house",
    "village house": "village_house",
    "house": "village_house",
    "golem": "iron_golem",
    "iron golem": "iron_golem",
    "iron giant": "iron_golem",
    "kitty": "cat",
    "kitten": "cat",
    # hostile mobs
    "ender man": "enderman",
    "tall black guy": "enderman",
    "cave spider": "cave_spider",
    "baby spider": "cave_spider",
    "wither skeleton": "wither_skeleton",
    "magma cube": "magma_cube",
    "magma slime": "magma_cube",
    "zombie piglin": "zombified_piglin",
    "zombie pig": "zombified_piglin",
    "piglin zombie": "zombified_piglin",
    # passive mobs
    "polar bear": "polar_bear",
    "dog": "wolf",
    "puppy": "wolf",
    "donkey": "donkey",
    # blocks
    "mossy stone": "mossy_cobblestone",
    "mossy stone floor": "mossy_cobblestone",
    "mossy stone tile": "mossy_cobblestone",
    "mossy cobblestone": "mossy_cobblestone",
    "mossy floor": "mossy_cobblestone",
    "nether rack": "netherrack",
    "soul sand": "soul_sand",
    "glow stone": "glowstone",
    "nether bricks": "nether_brick",
    "nether brick": "nether_brick",
    "enchantment table": "enchanting_table",
    "nether gate": "nether_portal",
    "nether portal": "nether_portal",
    "portal": "nether_portal",
}


def _voice_label(raw_label: str) -> bool:
    """Fetch the current live frame and save it as a labeled YOLO training
    sample. Returns True on success. Uses get_or_add_class so new labels
    (like lava, bedrock) are added to data.yaml automatically."""
    label = raw_label.strip().lower().replace(" ", "_")
    canonical = _LABEL_SYNONYMS.get(raw_label.strip().lower(), label)

    try:
        from core.class_registry import get_or_add_class
        class_id = get_or_add_class(canonical, source='voice_label')
        if class_id is None:
            print(f'[VOICE-LABEL] could not resolve class for "{canonical}"')
            return False
    except Exception as e:
        print(f'[VOICE-LABEL] class registry error: {e}')
        return False

    # Fetch current frame from the MJPEG frame server. This process *is* the
    # one serving it now, but going through the HTTP endpoint keeps this path
    # identical to what Axon did and avoids reaching into VideoCapturePipeline
    # from a thread that doesn't own it.
    try:
        import urllib.request, numpy as np, cv2
        with urllib.request.urlopen(_FRAME_SERVER_URL, timeout=3) as resp:
            data = resp.read()
        arr = np.frombuffer(data, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("empty frame")
    except Exception as e:
        print(f'[VOICE-LABEL] could not fetch frame: {e}')
        return False

    # Save image + label (wide center bbox — good enough for coarse learning)
    ts = int(time.time() * 1000)
    stem = f"voice_{ts}_{canonical}"
    os.makedirs(_LABEL_DATASET_IMG, exist_ok=True)
    os.makedirs(_LABEL_DATASET_LBL, exist_ok=True)
    cv2.imwrite(os.path.join(_LABEL_DATASET_IMG, f"{stem}.jpg"), frame)
    # YOLO label: <class_id> <cx> <cy> <w> <h> (normalised, center box ~70%)
    with open(os.path.join(_LABEL_DATASET_LBL, f"{stem}.txt"), "w") as f:
        f.write(f"{class_id} 0.500000 0.500000 0.700000 0.700000\n")

    print(f'[VOICE-LABEL] saved {stem} — class {class_id} ({canonical})')

    # Also write learning_trigger.json so the bot runs a full 24-frame orbit
    # on the labeled object — same pipeline as the Qwen vision watcher.
    trigger_path = os.path.join(BASE_DIR, "data", "learning_trigger.json")
    with open(trigger_path, "w") as f:
        json.dump({
            "label":       canonical,
            "description": f"voice-labeled by user as: {raw_label.strip()}",
            "box":         [0, 0, frame.shape[1], frame.shape[0]],
            "box_frac":    [0.5, 0.5, 1.0, 1.0],
            "conf":        1.0,   # user-confirmed, max confidence
            "ts":          time.time(),
            "source":      "voice_label",
        }, f)
    print(f'[VOICE-LABEL] learning trigger written → orbit "{canonical}"')
    return True


# ── TTS (was axon/speaker.py) ──────────────────────────────────────────────
class Speaker:
    """Offline TTS. Tries piper (neural British voice) first, falls back to
    pyttsx3/espeak."""

    def __init__(self, alsa_device: str = None, out_device_idx: int = None):
        self._engine = None
        self._espeak_bin = None
        self._mode = None
        self._piper_voice = None
        self._out_device_idx = out_device_idx
        self._lock = threading.Lock()
        if alsa_device:
            # Neither pyttsx3's espeak driver nor the plain `espeak` CLI take a
            # device argument on Linux — both shell out to ALSA's implicit
            # "default" PCM. ALSA_CARD redirects that default to our probed
            # card, scoped to this process only.
            card = alsa_device.split(':', 1)[1].split(',')[0] if ':' in alsa_device else None
            if card:
                os.environ['ALSA_CARD'] = card
        if not self._init_piper():
            if not self._init_pyttsx3():
                self._init_espeak()

    @property
    def available(self) -> bool:
        return self._mode is not None

    def _init_piper(self) -> bool:
        try:
            from piper import PiperVoice
        except ImportError as e:
            print(f'[VOICE] piper-tts not installed ({e})')
            return False
        model_path = os.path.join(config.VOICE_PIPER_VOICE_DIR,
                                  f'{config.VOICE_PIPER_VOICE}.onnx')
        if not os.path.exists(model_path):
            print(f'[VOICE] piper voice model not found at {model_path}')
            return False
        try:
            self._piper_voice = PiperVoice.load(model_path)
            self._mode = 'piper'
            print(f'[VOICE] speaker ready (piper: {config.VOICE_PIPER_VOICE}, '
                  f'volume={config.VOICE_TTS_VOLUME})')
            return True
        except Exception as e:
            print(f'[VOICE] piper unavailable ({e})')
            return False

    def _init_pyttsx3(self) -> bool:
        try:
            import pyttsx3
            self._engine = pyttsx3.init()
            self._engine.setProperty('rate', 195)
            self._engine.setProperty('volume', config.VOICE_TTS_VOLUME)
            voice_id = self._pick_pyttsx3_voice(self._engine)
            if voice_id:
                self._engine.setProperty('voice', voice_id)
            self._mode = 'pyttsx3'
            print(f'[VOICE] speaker ready (pyttsx3, voice={voice_id})')
            return True
        except Exception as e:
            print(f'[VOICE] pyttsx3 unavailable ({e})')
            return False

    @staticmethod
    def _pick_pyttsx3_voice(engine):
        """Best available male British voice — closest espeak has to a
        JARVIS-style tone when piper isn't available. Falls back through
        RP -> any en-gb -> engine default."""
        voices = engine.getProperty('voices')
        by_id = {v.id: v for v in voices}
        for key in ('gmw/en-gb-x-rp', 'gmw/en-gb'):
            if key in by_id:
                return by_id[key].id
        for v in voices:
            langs = [str(l).lower() for l in getattr(v, 'languages', [])]
            if any('en-gb' in l for l in langs) or 'en-gb' in v.id.lower():
                return v.id
        return None

    def _init_espeak(self) -> bool:
        import shutil
        path = shutil.which('espeak') or shutil.which('espeak-ng')
        if path:
            self._espeak_bin = path
            self._mode = 'espeak'
            print(f'[VOICE] speaker ready ({path})')
            return True
        print('[VOICE] no TTS backend available — speaker disabled')
        return False

    def _speak_piper(self, text: str):
        import io
        import subprocess
        import wave
        import numpy as np
        from piper.config import SynthesisConfig
        # length_scale < 1 = faster; 0.8 matches the ~30% rate bump applied to
        # pyttsx3 (175 -> 195 wpm). volume is normalized-full-scale amplitude
        # (normalize_audio defaults True), so it's an absolute output level
        # rather than "whatever this voice model happens to emit".
        syn_config = SynthesisConfig(length_scale=0.8,
                                     volume=config.VOICE_TTS_VOLUME)
        chunks = [c.audio_float_array for c in self._piper_voice.synthesize(text, syn_config)]
        if not chunks:
            return
        audio = np.concatenate(chunks)
        # Use aplay subprocess instead of sd.play()/sd.wait(). The sounddevice
        # ALSA backend has a double-free crash (pa_linux_alsa.c:3102) on
        # Python 3.12 when the output stream closes — PortAudio's background
        # thread isn't cleanly joined. aplay uses the kernel ALSA path
        # directly (PipeWire intercepts it transparently) and avoids the race.
        rate = self._piper_voice.config.sample_rate
        audio_int16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(audio_int16.tobytes())
        wav_bytes = buf.getvalue()
        subprocess.run(['aplay', '-q'], input=wav_bytes, check=False)

    def say(self, text: str):
        """Blocking speak, serialized by a lock. The voice thread is idle
        while speaking (it isn't listening), but the escalation path can be
        driven from a different thread, so two overlapping sd.play() calls
        against the same device are possible without this."""
        if not text or self._mode is None:
            return
        print(f'[VOICE] speaking: "{text}"')
        with self._lock:
            try:
                if self._mode == 'piper':
                    self._speak_piper(text)
                elif self._mode == 'pyttsx3':
                    self._engine.say(text)
                    self._engine.runAndWait()
                elif self._mode == 'espeak':
                    import subprocess
                    subprocess.run([self._espeak_bin, text], check=False)
            except Exception as e:
                print(f'[VOICE] speak error: {e}')


# ── Push-to-talk ───────────────────────────────────────────────────────────
class PTTKeyWatcher:
    """Watches for the push-to-talk key and fires on_press/on_release.

    Prefers pynput (F9) since it listens globally without needing window
    focus — important here since Minecraft holds focus. Falls back to the
    `keyboard` lib on SCROLL_LOCK (a key Minecraft doesn't intercept) if
    pynput isn't installed. If neither library is available, `available` is
    False and the caller should stay in "on" instead.

    Backend is detected once at construction; start()/stop() can be called
    repeatedly as the mode toggles at runtime.
    """

    def __init__(self, on_press, on_release):
        self._on_press_cb = on_press
        self._on_release_cb = on_release
        self._pynput_listener = None
        self._active = False

        try:
            import pynput  # noqa: F401
            self._backend = "pynput"
        except ImportError:
            try:
                import keyboard  # noqa: F401
                self._backend = "keyboard"
            except ImportError:
                self._backend = None

    @property
    def available(self) -> bool:
        return self._backend is not None

    @property
    def description(self) -> str:
        return {
            "pynput": "pynput (F9)",
            "keyboard": "keyboard lib (SCROLL_LOCK)",
        }.get(self._backend, "unavailable")

    def start(self):
        if self._active or self._backend is None:
            return
        if self._backend == "pynput":
            from pynput import keyboard as pynput_keyboard

            def _on_press(key):
                if key == pynput_keyboard.Key.f9:
                    self._on_press_cb()

            def _on_release(key):
                if key == pynput_keyboard.Key.f9:
                    self._on_release_cb()

            self._pynput_listener = pynput_keyboard.Listener(
                on_press=_on_press, on_release=_on_release)
            self._pynput_listener.daemon = True
            self._pynput_listener.start()
        else:  # "keyboard"
            import keyboard as keyboard_lib
            keyboard_lib.on_press_key("scroll lock", lambda _: self._on_press_cb())
            keyboard_lib.on_release_key("scroll lock", lambda _: self._on_release_cb())
        self._active = True

    def stop(self):
        if not self._active:
            return
        if self._backend == "pynput" and self._pynput_listener is not None:
            self._pynput_listener.stop()
            self._pynput_listener = None
        elif self._backend == "keyboard":
            import keyboard as keyboard_lib
            keyboard_lib.unhook_all()
        self._active = False


class _StreamRecorder:
    """Records mic audio of unknown-in-advance length via a sounddevice
    InputStream, unlike "on" mode's fixed-length blocking sd.rec() — PTT
    doesn't know how long the key will be held until it's released."""

    def __init__(self):
        self._stream = None
        self._frames = []

    def start(self, device_idx):
        import sounddevice as sd
        self._frames = []

        def _callback(indata, frames, time_info, status):
            self._frames.append(indata.copy())

        self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                      dtype='float32', device=device_idx,
                                      callback=_callback)
        self._stream.start()

    def stop(self):
        if self._stream is None:
            return None
        self._stream.stop()
        self._stream.close()
        self._stream = None
        if not self._frames:
            return None
        import numpy as np
        return np.concatenate(self._frames, axis=0).flatten()


def vad_backend_available() -> bool:
    """Whether always-on VAD is available.

    Returns True because _VADSegmenter now uses an energy threshold that
    doesn't require any C extension. webrtcvad was disabled due to a
    double-free crash on Python 3.12 — restore it here once a compatible
    build exists."""
    return True


class _VADSegmenter:
    """Always-on mic, cut into utterances on silence rather than on a clock.

    Holds one sounddevice InputStream open for as long as "on" mode lasts and
    classifies every 30 ms frame as speech or not. Frames accumulate once the
    trigger window goes voiced, and the utterance is emitted after
    VOICE_VAD_SILENCE_SEC of quiet — so the caller gets whole sentences,
    bounded by where the speaker actually paused.

    Uses webrtcvad — a real speech classifier, so a fan or a game explosion
    doesn't read as speech. The RMS energy threshold below is a last resort,
    not a peer: it fires on any sound above a level, which on a box with game
    audio coming out of the speakers means it fires more or less continuously.
    VoiceThread._apply_mode() therefore prefers push-to-talk over always-on
    when webrtcvad is missing, and only lands here on a box that has no
    keyboard hook either — where the alternative is no voice at all.

    Everything that touches ALSA goes through this one stream. Nothing else
    may open the mic while it runs — a second sd.rec() against a device this
    stream already holds is exactly the contention the Axon fold removed.
    """

    def __init__(self, device_idx):
        self._device_idx = device_idx
        self._stream = None
        self._q = queue.Queue(maxsize=VAD_QUEUE_FRAMES)
        self._vad = None
        self._energy_threshold = float(getattr(config, 'VOICE_VAD_ENERGY_THRESHOLD', 0.012))
        self._suppress_until = 0.0   # monotonic ts; frames before this are dropped
        self._reset()

        import collections
        self._window = collections.deque(maxlen=VAD_TRIGGER_WINDOW)
        # Pre-roll: the frames just before the trigger fired. Without it the
        # utterance starts mid-first-word, because it takes a voiced window to
        # decide someone started talking.
        preroll_sec = float(getattr(config, 'VOICE_VAD_PREROLL_SEC', 0.3))
        self._preroll = collections.deque(
            maxlen=max(1, int(preroll_sec * 1000 / VAD_FRAME_MS)))

        # webrtcvad causes `double free or corruption (out)` on Python 3.12
        # after the first TTS speak — skip it and use energy threshold instead.
        # Re-enable once a py312-compatible VAD (silero-vad or a patched
        # webrtcvad build) is available.
        self._vad = None
        print(f'[VOICE] VAD: energy threshold {self._energy_threshold:.4f} '
              f'(webrtcvad disabled — py312 crash workaround)')

    # ── stream lifecycle ───────────────────────────────────────────────────
    def start(self):
        import sounddevice as sd

        def _callback(indata, frames, time_info, status):
            # Runs on PortAudio's thread — must not block. On overflow drop
            # the oldest frame rather than this one.
            try:
                self._q.put_nowait(bytes(indata))
            except queue.Full:
                try:
                    self._q.get_nowait()
                    self._q.put_nowait(bytes(indata))
                except (queue.Empty, queue.Full):
                    pass

        self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                      dtype='int16', device=self._device_idx,
                                      blocksize=VAD_FRAME_SAMPLES,
                                      callback=_callback)
        self._stream.start()
        print('[VOICE] always-on listening: mic open, '
              f'{getattr(config, "VOICE_VAD_SILENCE_SEC", 0.8)}s silence ends an utterance')

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                print(f'[VOICE] VAD stream close failed: {e}')
            self._stream = None
        self.flush()

    @property
    def active(self) -> bool:
        return self._stream is not None

    # ── state ──────────────────────────────────────────────────────────────
    def _reset(self):
        self._triggered = False
        self._buf = []
        self._silence_ms = 0
        # Voiced audio only — not the buffer length, which also holds the
        # pre-roll and the trailing silence that closed the utterance.
        self._voiced_ms = 0

    def flush(self):
        """Drop everything captured so far. Called after the bot speaks: at
        VOICE_TTS_VOLUME the mic hears the speaker, and without this the bot
        transcribes its own answer and can talk itself into a loop."""
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._window.clear()
        self._preroll.clear()
        self._reset()

    def suppress_until(self, monotonic_ts: float):
        """Suppress audio capture until `monotonic_ts`. Called after speaking
        to absorb echo/reverb that persists after sd.wait() returns — the mic
        hears room reflections for ~0.5-1.5s even after playback ends."""
        self._suppress_until = monotonic_ts

    # ── classification ─────────────────────────────────────────────────────
    def _is_speech(self, frame: bytes) -> bool:
        if self._vad is not None:
            try:
                return self._vad.is_speech(frame, SAMPLE_RATE)
            except Exception:
                return False
        import numpy as np
        samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return False
        return float(np.sqrt(np.mean(samples ** 2))) > self._energy_threshold

    @staticmethod
    def _to_float32(frames):
        import numpy as np
        return (np.frombuffer(b''.join(frames), dtype=np.int16)
                .astype(np.float32) / 32768.0)

    # ── consumption ────────────────────────────────────────────────────────
    def read_utterance(self, poll_sec: float, gate=None):
        """Return one utterance as a float32 array, or None once poll_sec has
        elapsed without one completing — the caller needs to come up for air
        to re-read the mode file and check for escalations.

        `gate` is called per frame; while it returns True (the bot is
        speaking) audio is discarded and any partial utterance is dropped.
        """
        silence_sec  = float(getattr(config, 'VOICE_VAD_SILENCE_SEC', 0.8))
        min_speech   = float(getattr(config, 'VOICE_VAD_MIN_SPEECH_SEC', 0.4))
        max_utter    = float(getattr(config, 'VOICE_VAD_MAX_UTTERANCE_SEC', 15.0))
        deadline     = time.monotonic() + poll_sec

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                frame = self._q.get(timeout=min(0.2, remaining))
            except queue.Empty:
                continue

            if gate is not None and gate():
                if self._triggered:
                    self._reset()
                continue

            # Post-speak echo suppression: drop frames for a short window after
            # the bot finishes talking, so room reflections don't trigger a
            # new utterance and loop back into another TTS speak.
            if time.monotonic() < self._suppress_until:
                if self._triggered:
                    self._reset()
                continue

            speech = self._is_speech(frame)

            if not self._triggered:
                self._preroll.append(frame)
                self._window.append(speech)
                voiced = sum(self._window)
                if (len(self._window) == self._window.maxlen
                        and voiced >= VAD_TRIGGER_RATIO * self._window.maxlen):
                    self._triggered = True
                    self._silence_ms = 0
                    self._voiced_ms = voiced * VAD_FRAME_MS
                    self._buf = list(self._preroll)
                    self._window.clear()
                    self._preroll.clear()
                continue

            self._buf.append(frame)
            if speech:
                self._silence_ms = 0
                self._voiced_ms += VAD_FRAME_MS
            else:
                self._silence_ms += VAD_FRAME_MS
            duration = len(self._buf) * VAD_FRAME_MS / 1000.0

            if self._silence_ms >= silence_sec * 1000 or duration >= max_utter:
                # Read these off before _reset() zeroes them.
                trailing = self._silence_ms / 1000.0
                spoken   = self._voiced_ms / 1000.0
                frames   = self._buf
                self._reset()

                if spoken < min_speech:
                    continue   # a cough or a door — not worth waking whisper

                import numpy as np

                # Anything that runs all the way to VOICE_VAD_MAX_UTTERANCE_SEC
                # without ever going VOICE_VAD_SILENCE_SEC quiet is not a person
                # talking to the bot — nobody says a 15-second sentence with no
                # 0.8s pause in it. It is game audio holding the gate open, so
                # drop it here rather than pay whisper to caption it.
                if duration >= 0.95 * max_utter:
                    capped = self._to_float32(frames)
                    rms = (float(np.sqrt(np.mean(capped ** 2)))
                           if capped.size else 0.0)
                    print(f'[VOICE] dropping max-cap utterance ({duration:.1f}s) '
                          f'— likely game audio, not speech '
                          f'(rms={rms:.4f} vs threshold '
                          f'{self._energy_threshold:.4f})')
                    continue

                # Hand whisper only a short tail. It captions silence as
                # plausible-sounding text ("Thanks for watching!"), and a
                # full VOICE_VAD_SILENCE_SEC of it is enough to invite that.
                keep = len(frames) - int(max(0.0, trailing - 0.3) * 1000 / VAD_FRAME_MS)
                frames = frames[:max(1, keep)]

                audio = self._to_float32(frames)
                # Log the RMS that actually got through the gate. Tuning
                # VOICE_VAD_ENERGY_THRESHOLD blind takes a restart per guess;
                # this says how far over the line the source really was.
                rms = float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0
                print(f'[VOICE] utterance captured ({spoken:.1f}s speech, '
                      f'rms={rms:.4f} vs threshold {self._energy_threshold:.4f})')
                return audio

    def capture_window(self, seconds: float):
        """Collect a fixed window from the already-open stream, for the
        escalation yes/no prompt. Exists so that path never opens a second
        input stream while this one holds the device."""
        self.flush()
        end = time.monotonic() + seconds
        frames = []
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            try:
                frames.append(self._q.get(timeout=min(0.2, remaining)))
            except queue.Empty:
                continue
        if not frames:
            import numpy as np
            return np.zeros(0, dtype='float32')
        return self._to_float32(frames)


def read_mode_file() -> str:
    """Read the current listening mode, defaulting to MODE_ON (always-on,
    VAD-segmented) if no mode file is readable. data/voice_mode.txt wins;
    data/axon_mode.txt is still honored so the pre-fold
    `python axon/set_mode.py` helper keeps working until axon/ is deleted."""
    for path in (MODE_FILE_PATH, LEGACY_MODE_FILE_PATH):
        try:
            with open(path) as f:
                mode = f.read().strip().lower()
        except OSError:
            continue
        if mode in VALID_MODES:
            return mode
    return DEFAULT_MODE


class VoiceThread:
    """Whisper STT + piper TTS + PTT, as a daemon thread of the main process.

    Constructed with live references to the objects Axon could only reach
    through disk. All of them are optional — passing None falls back to the
    old cross-process behavior (read/write the JSON files), which keeps this
    module importable and testable standalone.

      goals             — live memory.goals.GoalStack, READ-ONLY here (status
                          queries). Goal *injection* still goes through
                          data/injected_goals.json; see the module docstring.
      monologue         — live core.cognitive.InnerMonologue, for Q&A answers.
      attention_manager — live envs.attention.AttentionManager, for env switch.
      memory_context    — live memory.MemoryContext, for Q&A prompt context.
    """

    def __init__(self, goals=None, monologue=None, attention_manager=None,
                 memory_context=None):
        self._goals = goals
        self._monologue = monologue
        self._attention_manager = attention_manager
        self._memory_context = memory_context

        self._model = None
        self._thread = None
        self._running = False
        self.mode = None            # set by _apply_mode() on the first poll
        self.enabled = False

        self._in_device_idx = None
        self._out_device_idx = None
        self.speaker = None

        self._ptt_recording = False
        self._ptt_recorder = _StreamRecorder()
        self._ptt_watcher = None
        self._segmenter = None      # _VADSegmenter, only while mode == "on"
        # Set while TTS is playing. The mic is open the whole time in "on"
        # mode and it hears the speaker, so captured audio is discarded until
        # the bot stops talking.
        self._speaking = threading.Event()
        # PTT release hands its audio here rather than transcribing inline —
        # whisper takes seconds and the release callback runs on pynput's
        # listener thread, which must stay responsive to catch the next press.
        self._work = queue.Queue()
        self._inject_lock = threading.Lock()

    # ── setup ──────────────────────────────────────────────────────────────
    def _probe(self) -> bool:
        """Check every hard dependency up front. Any failure disables voice
        and leaves the rest of the bot untouched."""
        if not getattr(config, 'VOICE_ENABLED', True):
            print('[VOICE] disabled by config.VOICE_ENABLED')
            return False
        try:
            import sounddevice as sd
        except (ImportError, OSError) as e:
            print(f'[VOICE] audio library not available ({e}) — voice disabled')
            return False
        try:
            import whisper  # noqa: F401
        except ImportError as e:
            print(f'[VOICE] whisper not installed ({e}) — voice disabled')
            return False
        try:
            devices = sd.query_devices()
        except Exception as e:
            print(f'[VOICE] cannot query audio devices: {e} — voice disabled')
            return False
        if not any(d['max_input_channels'] > 0 for d in devices):
            print('[VOICE] no microphone found — voice disabled')
            return False
        return True

    def _select_audio_devices(self):
        from audio.device_probe import select_devices, alsa_card
        in_idx, _in_dev, out_idx, out_dev = select_devices()
        self._in_device_idx = in_idx
        self._out_device_idx = out_idx
        return alsa_card(out_dev)

    def start(self) -> bool:
        """Probe, then spin up the daemon thread. Returns whether voice is
        actually running. Never raises — a failure here must not stop the
        bot from booting."""
        try:
            self.enabled = self._probe()
        except Exception as e:
            print(f'[VOICE] probe failed ({e}) — voice disabled')
            self.enabled = False
        if not self.enabled:
            return False

        try:
            out_alsa = self._select_audio_devices()
            self.speaker = Speaker(alsa_device=out_alsa,
                                   out_device_idx=self._out_device_idx)
            self._ptt_watcher = PTTKeyWatcher(self._on_ptt_press,
                                              self._on_ptt_release)
        except Exception as e:
            print(f'[VOICE] audio setup failed ({e}) — voice disabled')
            self.enabled = False
            return False

        self._running = True
        self._thread = threading.Thread(target=self._run, name='VoiceThread',
                                        daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        if self._ptt_watcher is not None:
            self._ptt_watcher.stop()
        # Release the mic now rather than waiting for the loop to notice —
        # shutdown can race the next process opening the device. Both this
        # and the loop's exit path are guarded against a double close.
        self._stop_segmenter()

    def _load_model(self):
        import whisper
        model_name = config.VOICE_WHISPER_MODEL
        print(f'[VOICE] loading whisper model "{model_name}" ...')
        self._model = whisper.load_model(model_name)
        print('[VOICE] whisper ready')

    # ── mode handling ──────────────────────────────────────────────────────
    def _apply_mode(self, new_mode: str):
        """Switch listening mode if new_mode differs from the current one,
        logging on startup (self.mode starts as None) and every change."""
        if new_mode == self.mode:
            return
        self.mode = new_mode
        print(f'[VOICE] mode: {new_mode}')

        # Always-on needs a real speech classifier. Without one _VADSegmenter
        # degrades to a bare RMS energy threshold, which a speaker playing game
        # audio trips more or less continuously — every false trigger costs a
        # whisper transcription and invites a hallucinated caption. Prefer
        # push-to-talk when the key hook exists; the energy threshold is only
        # reached on a box with neither, where PTT would mean no voice at all.
        if (self.mode == MODE_ON and not vad_backend_available()
                and self._ptt_watcher.available):
            print('[VOICE] no VAD backend installed (pip install webrtcvad) — '
                  'using push-to-talk rather than an energy threshold')
            self.mode = MODE_PTT
            print(f'[VOICE] mode: {MODE_PTT}')

        if self.mode == MODE_PTT:
            if self._ptt_watcher.available:
                self._stop_segmenter()
                self._ptt_watcher.start()
                print(f'[VOICE] PTT key listener active ({self._ptt_watcher.description})')
            else:
                print('[VOICE] PTT requested but neither pynput nor the '
                      'keyboard lib is installed — staying in on')
                self.mode = MODE_ON
                print(f'[VOICE] mode: {MODE_ON}')
        else:
            self._ptt_watcher.stop()
            if self._ptt_recording:
                self._ptt_recording = False
                self._ptt_recorder.stop()

        # Only "on" holds the mic open. Both branches above can land here with
        # self.mode == MODE_ON (including the PTT-unavailable fallback), so
        # the decision is made on self.mode, not on new_mode.
        if self.mode == MODE_ON:
            self._start_segmenter()
        else:
            self._stop_segmenter()

    def _start_segmenter(self):
        if self._segmenter is not None and self._segmenter.active:
            return
        try:
            self._segmenter = _VADSegmenter(self._in_device_idx)
            self._segmenter.start()
        except Exception as e:
            # No mic stream means no always-on listening, but PTT and the
            # escalation prompt still work, so this isn't fatal to voice.
            print(f'[VOICE] could not open always-on mic ({e}) — '
                  'no voice commands until the mode changes')
            self._segmenter = None

    def _stop_segmenter(self):
        if self._segmenter is not None:
            self._segmenter.stop()
            self._segmenter = None

    def _on_ptt_press(self):
        if self.mode != MODE_PTT or self._ptt_recording:
            return
        self._ptt_recording = True
        print('[VOICE] PTT: recording...')
        try:
            self._ptt_recorder.start(self._in_device_idx)
        except Exception as e:
            self._ptt_recording = False
            print(f'[VOICE] PTT: could not open mic ({e})')

    def _on_ptt_release(self):
        if not self._ptt_recording:
            return
        self._ptt_recording = False
        try:
            audio = self._ptt_recorder.stop()
        except Exception as e:
            print(f'[VOICE] PTT: recorder stop failed ({e})')
            return
        print('[VOICE] PTT: released, processing...')
        if audio is None or len(audio) < SAMPLE_RATE * 0.2:
            return  # too short to be real speech
        # Transcription happens on the voice thread, not here — see _work.
        self._work.put(audio)

    # ── audio I/O ──────────────────────────────────────────────────────────
    def _record(self, seconds: float):
        import sounddevice as sd
        frames = int(seconds * SAMPLE_RATE)
        audio = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1,
                       dtype='float32', device=self._in_device_idx, blocking=True)
        return audio.flatten()

    def _transcribe(self, audio) -> str:
        result = self._model.transcribe(audio, fp16=False, language='en')
        return result.get('text', '').strip()

    def _say(self, text: str):
        """Speak, with the mic gated for the duration. In "on" mode the input
        stream stays open while the speaker plays, so without the gate the bot
        transcribes its own voice — and since its answers are English
        sentences, they parse as commands."""
        if self.speaker is None:
            return
        self._speaking.set()
        # Close the mic InputStream before opening the output stream.
        # Having both an ALSA input and output stream open simultaneously
        # causes a double-free crash in PortAudio's ALSA backend (pa_unix_util.c
        # pthread_join + pa_linux_alsa.c PaUnixThread_Terminate) on Python 3.12.
        # Stopping the segmenter here serialises access so only one ALSA stream
        # is live at a time.
        if self._segmenter is not None:
            self._segmenter.stop()
        try:
            self.speaker.say(text)
        finally:
            self._speaking.clear()
            post_suppress = float(getattr(config, 'VOICE_POST_SPEAK_SUPPRESS_SEC', 1.5))
            if self._segmenter is not None:
                # Reopen mic after speaking, then apply suppress window so room
                # echo doesn't immediately trip the newly-opened stream.
                self._segmenter.start()
                self._segmenter.suppress_until(time.monotonic() + post_suppress)

    # ── goal / state plumbing ──────────────────────────────────────────────
    def _enqueue_goal(self, goal: str, reason: str) -> bool:
        """Append to data/injected_goals.json in the same {"queue": [...]}
        format mastermind/agent_client.py writes, so
        GoalStack.check_injected_goals() drains voice commands exactly like
        hive-assigned goals. Deliberately not a direct GoalStack.push() —
        see the module docstring. Returns whether the goal was queued
        (False if rejected as not in VALID_GOALS)."""
        if goal not in VALID_GOALS:
            print(f'[VOICE] rejected unrecognized goal: {goal}')
            return False
        from memory.goals import INJECTED_GOALS_PATH
        with self._inject_lock:
            os.makedirs(os.path.dirname(INJECTED_GOALS_PATH) or ".", exist_ok=True)
            queue_items = []
            if os.path.exists(INJECTED_GOALS_PATH):
                try:
                    with open(INJECTED_GOALS_PATH) as f:
                        queue_items = json.load(f).get("queue", [])
                except (OSError, json.JSONDecodeError):
                    queue_items = []
            queue_items.append({
                "goal": goal,
                "reason": reason,
                "received_at": time.time(),
            })
            with open(INJECTED_GOALS_PATH, "w") as f:
                json.dump({"queue": queue_items}, f)
        print(f'[VOICE] queued goal "{goal}" ({reason})')
        return True

    def _read_current_goal(self) -> str:
        """Live GoalStack when we have one (in-process now), disk otherwise."""
        if self._goals is not None:
            try:
                return self._goals.current_goal()
            except Exception:
                pass
        from memory.goals import GOALS_PATH
        try:
            with open(GOALS_PATH) as f:
                return json.load(f).get("current", "unknown")
        except (OSError, json.JSONDecodeError):
            return "unknown"

    def _push_monologue(self, text: str):
        """Surface a spoken answer on the monologue strip. In-process now, so
        push straight onto core.capture's thread-safe display queue AND the
        live InnerMonologue (which persists it and feeds it back into the
        next planning call)."""
        try:
            from core.capture import push_monologue_line
            push_monologue_line(text)
        except Exception as e:
            print(f'[VOICE] monologue display push failed: {e}')
        try:
            if self._monologue is not None:
                self._monologue.push_external(text)
            else:
                from core.cognitive import InnerMonologue
                InnerMonologue().push_external(text)
        except Exception as e:
            print(f'[VOICE] monologue persist failed: {e}')

    def _focus_env(self, env_name: str) -> bool:
        if self._attention_manager is not None:
            return self._attention_manager.focus(env_name)
        # Standalone fallback — persist the request for whoever owns the
        # real AttentionManager to pick up.
        from envs.attention import AttentionManager
        return AttentionManager(envs={}).focus(env_name)

    # ── command handling ───────────────────────────────────────────────────
    def _handle_command(self, transcript: str):
        # Voice labeling ("that is a tree", "that's lava", ...) is checked
        # first — it short-circuits before goal/LLM parsing so labeling
        # phrases don't get force-fit into goal strings.
        lm = _LABEL_PATTERNS.search(transcript)
        if lm:
            raw_label = lm.group(1).strip()
            print(f'[VOICE] voice label: "{transcript}" → "{raw_label}"')
            if _voice_label(raw_label):
                self._say(f"Got it, labeled that as {raw_label}.")
            else:
                self._say(f"Couldn't save the label for {raw_label}.")
            return

        # Env switch is checked before anything else — it's neither a goal nor
        # a status query, and free-text env names like "GOAT Racer" would
        # otherwise risk getting force-fit into a goal by the LLM fallback.
        env_name = _match_env_switch(transcript)
        if env_name is not None:
            print(f'[VOICE] command: "{transcript}"')
            if self._focus_env(env_name):
                self._say(f"Switching attention to {env_name}.")
            else:
                self._say(f"I don't know an environment called {env_name}.")
            return

        # Deterministic (free) status/rule matches first — cheap regex. Only a
        # transcript matching NEITHER falls through to the question check
        # below, before ever reaching _parse_with_local_llm — that fallback
        # would otherwise burn a mesh-llm call trying to force a question like
        # "what is a creeper" into a snake_case goal.
        det = parse_deterministic(transcript)

        if det is not None and det['type'] == 'query' and det['query'] == 'status':
            print(f'[VOICE] command: "{transcript}"')
            self._say(f"I'm currently working on: {self._read_current_goal().replace('_', ' ')}.")
            return

        if det is not None and det['type'] == 'goal':
            print(f'[VOICE] command: "{transcript}"')
            if self._enqueue_goal(det['goal'], f"voice:{det['source']} — \"{transcript}\""):
                self._say(f"Got it. {det['goal'].replace('_', ' ')}.")
            else:
                self._say("I don't know how to do that yet.")
            return

        if _looks_like_question(transcript):
            self._answer_question(transcript)
            return

        # No rule matched and it doesn't read as a question — last resort, ask
        # the local LLM to interpret it as a free-form goal command.
        intent = parse(transcript)
        if intent['type'] == 'goal':
            print(f'[VOICE] command: "{transcript}"')
            if self._enqueue_goal(intent['goal'], f"voice:{intent['source']} — \"{transcript}\""):
                self._say(f"Got it. {intent['goal'].replace('_', ' ')}.")
            else:
                self._say("I don't know how to do that yet.")
            return

        # Not recognized as a command. In "on" mode this is most likely
        # ambient speech, not a failed request — log only, stay quiet.
        print(f'[VOICE] heard (unrecognized): "{transcript}"')

    def _answer_question(self, question: str):
        """Answer a spoken question out loud, using the same self-built memory
        system (memory/context.py) that feeds the overseer — episodic/semantic/
        procedural memory plus the current FSM state, goal, health/hunger, and
        recent inner monologue. The answer is spoken AND pushed onto the
        monologue strip, so what Scott hears is what the bot is thinking."""
        print(f'[VOICE] question: "{question}"')
        time.sleep(QA_ANSWER_PAUSE_SEC)

        from core.llm_router import route_llm_call

        mem = self._memory_context
        if mem is None:
            from memory import MemoryContext
            mem = MemoryContext()
        context = mem.build_context_for_llm()
        prompt = (
            f'{context}\n\n'
            f'Someone just asked you: "{question}"\n'
            'Answer in one or two short spoken sentences, as AKSUMAEL, '
            'using the context above where relevant. No markdown, just '
            'the words to speak.'
        )
        raw, _provider = route_llm_call(prompt, max_tokens=300,
                                        timeout=config.LOCAL_LLM_TIMEOUT)
        if not raw:
            self._say("I'm not sure — my thinking module isn't responding right now.")
            return

        answer = raw.strip()
        self._say(answer)
        self._push_monologue(answer)

    # ── supervisor escalation ──────────────────────────────────────────────
    def _check_escalation(self) -> None:
        """Poll for a pending supervisor escalation written by core/fsm.py.
        If found: announce via TTS, listen 8s for "proceed"/"yes", resolve."""
        from core.escalation_ipc import poll_pending, resolve as ipc_resolve

        req = poll_pending()
        if req is None:
            return

        proposed = req.get("proposed", "?")
        reason = req.get("reason", "no reason given")
        print(f'[VOICE] supervisor escalation: {proposed} — {reason}')
        self._say(f"Bot paused. Action {proposed} blocked: {reason}. "
                  f"Say 'proceed' to override, or stay silent to refuse.")

        approved = False
        if self._model is not None:
            try:
                # Read the window off the always-on stream when there is one.
                # sd.rec() here would be a second open of a device the
                # segmenter already holds, which fails outright on ALSA.
                audio = (self._segmenter.capture_window(8.0)
                         if self._segmenter is not None and self._segmenter.active
                         else self._record(8.0))
                text = self._transcribe(audio).lower()
                print(f"[VOICE] escalation heard: '{text}'")
                approved = any(w in text for w in
                               ('proceed', 'yes', 'go', 'allow', 'override'))
            except Exception as e:
                print(f'[VOICE] escalation listen error ({e}) — refusing')

        ipc_resolve(approved)
        self._say("Override accepted. Proceeding." if approved else "Action refused.")

    # ── main loop ──────────────────────────────────────────────────────────
    def _drain_work(self, block: bool):
        """Transcribe and handle audio captured by the PTT callback thread.

        Whisper runs here, on the voice thread, rather than in
        _on_ptt_release — that callback runs on pynput's listener thread,
        which has to stay responsive to catch the next key press."""
        try:
            audio = (self._work.get(timeout=MODE_POLL_SEC) if block
                     else self._work.get_nowait())
        except queue.Empty:
            return
        while True:
            transcript = self._transcribe(audio).strip()
            if transcript and len(transcript.split()) >= MIN_COMMAND_WORDS:
                self._handle_command(transcript)
            try:
                audio = self._work.get_nowait()
            except queue.Empty:
                return

    def _run(self):
        try:
            self._load_model()
        except Exception as e:
            print(f'[VOICE] whisper model load failed ({e}) — voice disabled')
            self.enabled = False
            return

        self._apply_mode(read_mode_file())
        # Startup announcement is handled by runtime.py (tts.say_line('startup')).
        # No second announcement here.

        while self._running:
            try:
                # Always check for pending supervisor escalations, regardless
                # of mode.
                self._check_escalation()

                if self.mode == MODE_ON and self._segmenter is not None:
                    # Anything PTT captured before the mode flipped still gets
                    # handled, but never blocks the listener.
                    self._drain_work(block=False)
                    # Returns as soon as someone stops talking; the timeout is
                    # only so the mode file and escalations get checked while
                    # the room is quiet.
                    audio = self._segmenter.read_utterance(
                        MODE_POLL_SEC, gate=self._speaking.is_set)
                    if audio is not None:
                        transcript = self._transcribe(audio).strip()
                        if transcript and len(transcript.split()) >= MIN_COMMAND_WORDS:
                            self._handle_command(transcript)
                elif self.mode == MODE_ON:
                    # "on" with no stream (mic open failed) — retry on the
                    # next pass rather than spinning.
                    self._drain_work(block=True)
                    self._start_segmenter()
                else:
                    # ptt is event-driven (PTTKeyWatcher callbacks) and off
                    # takes no mic access at all — both idle here. Blocking on
                    # the work queue means a released key is picked up the
                    # instant it lands rather than up to MODE_POLL_SEC later,
                    # while the timeout still re-reads the mode file on time.
                    self._drain_work(block=True)

                self._apply_mode(read_mode_file())
            except Exception as e:
                print(f'[VOICE] loop error: {e}')
                time.sleep(1.0)

        self._ptt_watcher.stop()
        self._stop_segmenter()
        print('[VOICE] thread stopped')


def start(goals=None, monologue=None, attention_manager=None,
          memory_context=None) -> "VoiceThread | None":
    """Construct and start a VoiceThread. Returns the instance if voice came
    up, None otherwise. Never raises — voice is strictly optional, and the bot
    must boot identically on a box with no mic, no whisper, or no piper."""
    try:
        vt = VoiceThread(goals=goals, monologue=monologue,
                         attention_manager=attention_manager,
                         memory_context=memory_context)
        if vt.start():
            return vt
    except Exception as e:
        print(f'[VOICE] could not start voice thread: {e} — continuing without voice')
    return None


if __name__ == '__main__':
    # Standalone smoke test: `venv/bin/python3 -m core.voice`
    vt = start()
    if vt is None:
        raise SystemExit('[VOICE] failed to start')
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        vt.stop()
