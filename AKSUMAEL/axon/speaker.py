# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Axon Speaker                       ║
# ║  Offline TTS for voice responses. Tries piper (neural ║
# ║  British voice) first, falls back to pyttsx3/espeak. ║
# ╚══════════════════════════════════════════════════════╝

import os
import shutil
import subprocess

import config


class Speaker:
    def __init__(self, alsa_device: str = None, out_device_idx: int = None):
        self._engine = None
        self._espeak_bin = None
        self._mode = None
        self._piper_voice = None
        self._out_device_idx = out_device_idx
        if alsa_device:
            # Neither pyttsx3's espeak driver nor the plain `espeak` CLI
            # take a device argument on Linux — both shell out to ALSA's
            # implicit "default" PCM. ALSA_CARD redirects that default to
            # our probed card, scoped to this process only.
            card = alsa_device.split(':', 1)[1].split(',')[0] if ':' in alsa_device else None
            if card:
                os.environ['ALSA_CARD'] = card
        if not self._init_piper():
            if not self._init_pyttsx3():
                self._init_espeak()

    def _init_piper(self) -> bool:
        try:
            from piper import PiperVoice
        except ImportError as e:
            print(f'[AXON] piper-tts not installed ({e})')
            return False
        model_path = os.path.join(config.AXON_PIPER_VOICE_DIR, f'{config.AXON_PIPER_VOICE}.onnx')
        if not os.path.exists(model_path):
            print(f'[AXON] piper voice model not found at {model_path}')
            return False
        try:
            self._piper_voice = PiperVoice.load(model_path)
            self._mode = 'piper'
            print(f'[AXON] speaker ready (piper: {config.AXON_PIPER_VOICE})')
            return True
        except Exception as e:
            print(f'[AXON] piper unavailable ({e})')
            return False

    def _init_pyttsx3(self) -> bool:
        try:
            import pyttsx3
            self._engine = pyttsx3.init()
            self._engine.setProperty('rate', 195)
            self._engine.setProperty('volume', 0.9)
            voice_id = self._pick_pyttsx3_voice(self._engine)
            if voice_id:
                self._engine.setProperty('voice', voice_id)
            self._mode = 'pyttsx3'
            print(f'[AXON] speaker ready (pyttsx3, voice={voice_id})')
            return True
        except Exception as e:
            print(f'[AXON] pyttsx3 unavailable ({e})')
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
        path = shutil.which('espeak') or shutil.which('espeak-ng')
        if path:
            self._espeak_bin = path
            self._mode = 'espeak'
            print(f'[AXON] speaker ready ({path})')
            return True
        print('[AXON] no TTS backend available — speaker disabled')
        return False

    def _speak_piper(self, text: str):
        import numpy as np
        import sounddevice as sd
        from piper.config import SynthesisConfig
        # length_scale < 1 = faster; 0.8 matches the ~30% rate bump requested
        # for pyttsx3 (175 -> 195 wpm), applied to piper since it's the voice
        # actually in use whenever its model is available.
        syn_config = SynthesisConfig(length_scale=0.8)
        chunks = [c.audio_float_array for c in self._piper_voice.synthesize(text, syn_config)]
        if not chunks:
            return
        audio = np.concatenate(chunks)
        sd.play(audio, samplerate=self._piper_voice.config.sample_rate, device=self._out_device_idx)
        sd.wait()

    def say(self, text: str):
        """Blocking speak. Axon's loop is single-threaded and idle while
        speaking anyway (it isn't listening), so no queue is needed."""
        if not text or self._mode is None:
            return
        print(f'[AXON] speaking: "{text}"')
        try:
            if self._mode == 'piper':
                self._speak_piper(text)
            elif self._mode == 'pyttsx3':
                self._engine.say(text)
                self._engine.runAndWait()
            elif self._mode == 'espeak':
                subprocess.run([self._espeak_bin, text], check=False)
        except Exception as e:
            print(f'[AXON] speak error: {e}')


if __name__ == '__main__':
    Speaker().say("Axon voice hub online.")
