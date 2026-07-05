# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Game Ear                           ║
# ║  Minecraft-calibrated audio classifier              ║
# ║  Gracefully disables if no audio device found       ║
# ╚══════════════════════════════════════════════════════╝
#
# Audio source priority:
#   1. Rybozen capture card HDMI audio (GAME_AUDIO_INDEX in config)
#   2. Any available input device (-1 = auto)
#   3. Silent fallback (no errors, just disabled)
#
# The Rybozen exposes game audio via HDMI as a stereo ALSA device.
# Run: arecord -l  — find "USB3.0 Audio", use its card number.

import threading
import queue
import time
import config

AUDIO_EVENTS = {
    'explosion':   {'reward': -0.8, 'persona': 'game_danger'},
    'damage':      {'reward': -0.4, 'persona': 'game_danger'},
    'block_break': {'reward': +0.2, 'persona': None},
    'item_pickup': {'reward': +0.5, 'persona': 'game_reward_sound'},
    'xp_gain':     {'reward': +0.6, 'persona': 'game_reward_sound'},
    'ui_click':    {'reward':  0.0, 'persona': 'game_ui_sound'},
    'ambient':     {'reward':  0.0, 'persona': None},
    'silence':     {'reward':  0.0, 'persona': None},
}

# Minecraft-tuned thresholds
MC = {
    'silence_rms':     80,
    'explosion_rms':   6000,
    'damage_rms':      1500,
    'block_break_rms': 600,
    'pickup_rms':      400,
    'xp_rms':          500,
    'explosion_hz':    300,
    'damage_hz_lo':    800,
    'damage_hz_hi':    2500,
    'pickup_hz':       2000,
    'ui_hz':           3500,
    'transient_zcr':   0.15,
    'tonal_zcr':       0.06,
    'debounce_sec':    1.5,
    'chunk_sec':       0.25,
    'sample_rate':     44100,   # capture card delivers 44.1kHz stereo
    'channels':        2,       # stereo from HDMI audio
}


class GameEar:
    def __init__(self):
        self.enabled     = config.ENABLE_GAME_EAR
        self.device_idx  = config.GAME_AUDIO_INDEX   # -1 = auto
        self.event_queue = queue.Queue()
        self._running    = False
        self._thread     = None
        self._last       = {}   # event → last emit time

        if self.enabled:
            self.enabled = self._probe()

    def _probe(self) -> bool:
        """
        Check whether sounddevice + numpy are available AND a usable
        input device exists. Returns True if game ear can run.
        """
        try:
            import sounddevice as sd
            import numpy as np
        except (ImportError, OSError) as e:
            print(f'[GAME_EAR] audio library not available ({e}) — disabled')
            return False

        try:
            devices = sd.query_devices()
        except Exception as e:
            print(f'[GAME_EAR] cannot query audio devices: {e} — disabled')
            return False

        if self.device_idx >= 0:
            # Specific device requested
            try:
                dev = sd.query_devices(self.device_idx)
                if dev['max_input_channels'] < 1:
                    print(f'[GAME_EAR] device {self.device_idx} has no '
                          f'input channels — disabled')
                    return False
                print(f'[GAME_EAR] using device {self.device_idx}: '
                      f'{dev["name"]} (Minecraft-calibrated)')
                return True
            except Exception as e:
                print(f'[GAME_EAR] device {self.device_idx} not found: {e} — disabled')
                return False
        else:
            # Auto: find any input device
            inputs = [d for d in devices if d['max_input_channels'] > 0]
            if not inputs:
                print('[GAME_EAR] no audio input devices found — disabled')
                print('           Plug in the Rybozen capture card USB '
                      'or set ENABLE_GAME_EAR=False in config.py')
                return False
            # Prefer capture card audio (look for USB3.0 Audio)
            preferred = next(
                (d for d in inputs if 'usb3' in d['name'].lower()
                 or 'usb 3' in d['name'].lower()
                 or 'rybozen' in d['name'].lower()),
                inputs[0]
            )
            idx = list(devices).index(preferred)
            self.device_idx = idx
            print(f'[GAME_EAR] auto-selected: {preferred["name"]} '
                  f'(device {idx}) — Minecraft-calibrated')
            return True

    def start(self):
        if not self.enabled:
            return
        self._running = True
        self._thread  = threading.Thread(target=self._loop,
                                         daemon=True, name='game_ear')
        self._thread.start()

    def _loop(self):
        try:
            import sounddevice as sd
            import numpy as np
        except (ImportError, OSError):
            return

        SR       = MC['sample_rate']
        CH       = MC['channels']
        samples  = int(MC['chunk_sec'] * SR)
        dev_idx  = self.device_idx if self.device_idx >= 0 else None

        consecutive_errors = 0
        MAX_ERRORS = 5

        while self._running:
            try:
                audio = sd.rec(samples, samplerate=SR, channels=CH,
                               dtype='int16', device=dev_idx,
                               blocking=True)
                # Mix stereo to mono
                if CH > 1:
                    mono = audio.mean(axis=1).astype('int16')
                else:
                    mono = audio.flatten()

                af = mono.astype('float32') / 32768.0
                event = self._classify(af, SR)
                self._maybe_emit(event)
                consecutive_errors = 0

            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors == 1:
                    print(f'[GAME_EAR] audio read error: {e}')
                if consecutive_errors >= MAX_ERRORS:
                    print(f'[GAME_EAR] {MAX_ERRORS} consecutive errors — '
                          f'disabling game ear')
                    self.enabled = False
                    break
                time.sleep(0.5)

    def _classify(self, af, sr: int) -> str:
        import numpy as np

        rms = float(np.sqrt(np.mean(af ** 2))) * 32768
        if rms < MC['silence_rms']:
            return 'silence'

        fft   = np.abs(np.fft.rfft(af))
        freqs = np.fft.rfftfreq(len(af), 1.0 / sr)
        denom = np.sum(fft) + 1e-8
        cent  = float(np.sum(freqs * fft) / denom)
        zcr   = float(np.mean(np.abs(np.diff(np.sign(af)))) / 2)
        flux  = float(np.sum(np.abs(fft[len(fft)//2:] -
                                    fft[:len(fft)//2])) / denom)

        if rms > MC['explosion_rms'] and cent < MC['explosion_hz']:
            return 'explosion'
        if (MC['damage_hz_lo'] < cent < MC['damage_hz_hi']
                and rms > MC['damage_rms']
                and zcr > MC['transient_zcr']):
            return 'damage'
        if cent > MC['pickup_hz'] and zcr < MC['tonal_zcr']:
            if rms > MC['xp_rms'] and flux > 0.1:
                return 'xp_gain'
            if rms > MC['pickup_rms']:
                return 'item_pickup'
        if cent > MC['ui_hz'] and rms < MC['damage_rms']:
            return 'ui_click'
        if (rms > MC['block_break_rms']
                and cent < MC['damage_hz_lo']
                and flux > 0.05):
            return 'block_break'
        return 'ambient'

    def _maybe_emit(self, event: str):
        if event in ('silence', 'ambient'):
            return
        now  = time.time()
        if now - self._last.get(event, 0) < MC['debounce_sec']:
            return
        self._last[event] = now
        meta = AUDIO_EVENTS.get(event, {})
        self.event_queue.put({
            'event':   event,
            'reward':  meta.get('reward', 0.0),
            'persona': meta.get('persona'),
            'ts':      now,
        })

    def poll(self):
        try:
            return self.event_queue.get_nowait()
        except queue.Empty:
            return None

    def stop(self):
        self._running = False


if __name__ == '__main__':
    print('Game Ear self-test')
    ear = GameEar()
    print(f'Enabled: {ear.enabled}')
    if ear.enabled:
        ear.start()
        print('Listening 5s — make noise or play game audio near the mic')
        for _ in range(20):
            time.sleep(0.25)
            ev = ear.poll()
            if ev:
                print(f'  event: {ev}')
        ear.stop()
    else:
        print('Game ear not available — AKSUMAEL will run without it (no errors).')
