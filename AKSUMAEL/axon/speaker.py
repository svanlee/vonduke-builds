# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Axon Speaker                       ║
# ║  Offline TTS for voice responses (pyttsx3, falls    ║
# ║  back to espeak/espeak-ng; silent no-op if neither)  ║
# ╚══════════════════════════════════════════════════════╝

import shutil
import subprocess


class Speaker:
    def __init__(self):
        self._engine = None
        self._espeak_bin = None
        self._mode = None
        if not self._init_pyttsx3():
            self._init_espeak()

    def _init_pyttsx3(self) -> bool:
        try:
            import pyttsx3
            self._engine = pyttsx3.init()
            self._engine.setProperty('rate', 175)
            self._engine.setProperty('volume', 0.9)
            self._mode = 'pyttsx3'
            print('[AXON] speaker ready (pyttsx3)')
            return True
        except Exception as e:
            print(f'[AXON] pyttsx3 unavailable ({e})')
            return False

    def _init_espeak(self) -> bool:
        path = shutil.which('espeak') or shutil.which('espeak-ng')
        if path:
            self._espeak_bin = path
            self._mode = 'espeak'
            print(f'[AXON] speaker ready ({path})')
            return True
        print('[AXON] no TTS backend available — speaker disabled')
        return False

    def say(self, text: str):
        """Blocking speak. Axon's loop is single-threaded and idle while
        speaking anyway (it isn't listening), so no queue is needed."""
        if not text or self._mode is None:
            return
        print(f'[AXON] speaking: "{text}"')
        try:
            if self._mode == 'pyttsx3':
                self._engine.say(text)
                self._engine.runAndWait()
            elif self._mode == 'espeak':
                subprocess.run([self._espeak_bin, text], check=False)
        except Exception as e:
            print(f'[AXON] speak error: {e}')


if __name__ == '__main__':
    Speaker().say("Axon voice hub online.")
