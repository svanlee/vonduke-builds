# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Text-to-Speech                     ║
# ║  pyttsx3 (offline) or ElevenLabs (cloud, better)    ║
# ╚══════════════════════════════════════════════════════╝

import threading
import queue
import time
import config
from audio.voice_persona import get_line


class TTSEngine:
    """
    Non-blocking TTS. Calls are queued so AKSUMAEL never
    blocks the main loop waiting for speech to finish.
    """

    def __init__(self):
        self.enabled = config.ENABLE_TTS
        self.engine_name = config.TTS_ENGINE.lower()
        self._queue = queue.Queue()
        self._engine = None
        self._thread = None
        self._running = False

        if self.enabled:
            self._init_engine()
            self._start_worker()

    def _init_engine(self):
        if self.engine_name == 'elevenlabs':
            self._init_elevenlabs()
        else:
            self._init_pyttsx3()

    def _init_pyttsx3(self):
        try:
            import pyttsx3
            self._engine = pyttsx3.init()
            # Tune voice rate and volume for Cortana-ish feel
            self._engine.setProperty('rate', 175)    # slightly faster than default
            self._engine.setProperty('volume', 0.9)
            # Try to find a female voice
            voices = self._engine.getProperty('voices')
            female = next((v for v in voices
                           if 'female' in v.name.lower()
                           or 'zira' in v.name.lower()
                           or 'hazel' in v.name.lower()), None)
            if female:
                self._engine.setProperty('voice', female.id)
            print(f'[TTS] pyttsx3 ready'
                  + (f' — voice: {female.name}' if female else ''))
        except ImportError:
            print('[TTS] pyttsx3 not installed — TTS disabled')
            self.enabled = False
        except Exception as e:
            print(f'[TTS] pyttsx3 init failed: {e} — TTS disabled')
            self.enabled = False

    def _init_elevenlabs(self):
        try:
            import requests
            # Quick API key check
            if not config.ELEVENLABS_API_KEY or \
               config.ELEVENLABS_API_KEY == 'YOUR_ELEVENLABS_KEY_HERE':
                print('[TTS] ElevenLabs key not set — falling back to pyttsx3')
                self.engine_name = 'pyttsx3'
                self._init_pyttsx3()
                return
            self._el_key   = config.ELEVENLABS_API_KEY
            self._el_voice = config.ELEVENLABS_VOICE
            print(f'[TTS] ElevenLabs ready — voice: {self._el_voice}')
        except Exception as e:
            print(f'[TTS] ElevenLabs init failed: {e} — falling back to pyttsx3')
            self.engine_name = 'pyttsx3'
            self._init_pyttsx3()

    def _start_worker(self):
        self._running = True
        self._thread = threading.Thread(target=self._worker,
                                        daemon=True, name='tts')
        self._thread.start()

    def _worker(self):
        while self._running:
            try:
                text = self._queue.get(timeout=0.5)
                if text:
                    self._speak_now(text)
            except queue.Empty:
                continue
            except Exception as e:
                print(f'[TTS] worker error: {e}')

    def _speak_now(self, text: str):
        """Blocking speak — called from worker thread only."""
        if self.engine_name == 'elevenlabs':
            self._speak_elevenlabs(text)
        else:
            self._speak_pyttsx3(text)

    def _speak_pyttsx3(self, text: str):
        try:
            self._engine.say(text)
            self._engine.runAndWait()
        except Exception as e:
            print(f'[TTS] pyttsx3 speak error: {e}')

    def _speak_elevenlabs(self, text: str):
        try:
            import requests, io, subprocess
            url = (f'https://api.elevenlabs.io/v1/text-to-speech/'
                   f'{self._el_voice_id()}/stream')
            headers = {
                'xi-api-key': self._el_key,
                'Content-Type': 'application/json',
            }
            payload = {
                'text': text,
                'model_id': 'eleven_turbo_v2',
                'voice_settings': {'stability': 0.5, 'similarity_boost': 0.75}
            }
            resp = requests.post(url, json=payload, headers=headers,
                                 stream=True, timeout=10)
            if resp.status_code == 200:
                # Stream audio to aplay (Linux)
                proc = subprocess.Popen(['aplay', '-q', '-f', 'S16_LE',
                                         '-r', '22050', '-c', '1', '-'],
                                        stdin=subprocess.PIPE)
                for chunk in resp.iter_content(chunk_size=4096):
                    proc.stdin.write(chunk)
                proc.stdin.close()
                proc.wait()
            else:
                print(f'[TTS] ElevenLabs error {resp.status_code}')
                # Fall back to pyttsx3 for this utterance
                self._speak_pyttsx3(text)
        except Exception as e:
            print(f'[TTS] ElevenLabs speak error: {e}')

    def _el_voice_id(self) -> str:
        """
        Map friendly voice name to ElevenLabs voice ID.
        These IDs are stable pre-made voices.
        """
        VOICE_IDS = {
            'Rachel':  '21m00Tcm4TlvDq8ikWAM',
            'Domi':    'AZnzlk1XvdvUeBnXmlld',
            'Bella':   'EXAVITQu4vr4xnSDxMaL',
            'Antoni':  'ErXwobaYiN019PkySvjV',
            'Elli':    'MF3mGyEYCl7XYWbV9V6O',
        }
        return VOICE_IDS.get(self._el_voice, VOICE_IDS['Rachel'])

    # ── Public API ─────────────────────────────────────────────
    def say(self, text: str, priority: bool = False):
        """
        Queue text for speech. Non-blocking.
        priority=True clears the queue first (urgent messages).
        """
        if not self.enabled or not text:
            return
        if priority:
            self._drain_queue()
        self._queue.put(text)

    def say_line(self, key: str, priority: bool = False):
        """Speak a random persona line by key."""
        self.say(get_line(key), priority=priority)

    def say_observation(self, observation: str, confidence: float):
        """Narrate what AKSUMAEL sees — only if confidence is decent."""
        if confidence < 0.4:
            return   # too uncertain, stay quiet
        from audio.voice_persona import get_observation_narration
        line = get_observation_narration(observation, confidence)
        if line:
            self.say(line)

    def say_action(self, action_dict: dict):
        """Narrate the action AKSUMAEL is taking."""
        from audio.voice_persona import format_action_speech
        line = format_action_speech(action_dict)
        if line:
            self.say(line)

    def _drain_queue(self):
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def stop(self):
        self._running = False
        self._drain_queue()


# ── Quick test ─────────────────────────────────────────────────
if __name__ == '__main__':
    print('TTS self-test')
    tts = TTSEngine()
    if tts.enabled:
        tts.say_line('startup')
        tts.say("I can see a chest in the top-left corner of the screen.")
        tts.say_line('good_reward')
        tts.say_line('unknown_object')
        time.sleep(8)
    else:
        print('TTS not available on this system.')
    tts.stop()
