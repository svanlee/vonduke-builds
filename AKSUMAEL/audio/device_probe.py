# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Audio Device Probe                 ║
# ║  Auto-detects mic/speaker at startup so nothing is    ║
# ║  hardcoded when AKSUMAEL moves to a different box.    ║
# ╚══════════════════════════════════════════════════════╝
#
# robocar-hub *is* the HP Victus laptop (the "T7" name refers to the
# external Samsung T7 SSD it boots/stores AKSUMAEL from, not a separate
# host) — so its built-in mic/speaker are always attached locally. Rather
# than hardcode a device index, this probes sounddevice.query_devices()
# and picks by name keyword priority, so the same config keeps working
# after a hardware move (e.g. to the Z490 desktop build).
# config.AUDIO_INPUT_DEVICE / AUDIO_OUTPUT_DEVICE remain as an explicit
# override (int index) for when auto-detection guesses wrong.
#
# NOTE: PortAudio/ALSA device names never include "Victus" — they're
# codec/chip names, so that keyword never matches here and this falls
# through to "default" (the PipeWire pseudo-device). That's deliberate:
# raw ALSA hw devices (e.g. "acp63", the built-in mic's DSP) only accept
# their native sample rate, but hub.py records at Whisper's fixed 16kHz —
# only PipeWire's "default" resamples on the fly. Correctness therefore
# depends on PipeWire's *default source* actually being the built-in mic
# (`wpctl status` / `wpctl set-default <id>`), not on this keyword list.

import re

import config

PRIORITY_KEYWORDS = ["Victus", "USB Audio", "HDMI Audio", "default"]

_ALSA_HW_RE = re.compile(r'\(hw:(\d+),(\d+)\)')


def _pick(devices, channel_key, override):
    if override is not None:
        return override
    candidates = [i for i, d in enumerate(devices) if d[channel_key] > 0]
    if not candidates:
        return None
    for kw in PRIORITY_KEYWORDS:
        for i in candidates:
            if kw.lower() in devices[i]['name'].lower():
                return i
    return candidates[0]   # nothing matched — first available device


def alsa_card(device: dict) -> str:
    """Extract an ALSA 'hw:N,M' string from a PortAudio device name, e.g.
    'HD-Audio Generic: ALC245 Analog (hw:2,0)' -> 'hw:2,0'. Returns None
    for pseudo-devices (pipewire, default) that carry no hw tag."""
    if not device:
        return None
    m = _ALSA_HW_RE.search(device.get('name', ''))
    return f'hw:{m.group(1)},{m.group(2)}' if m else None


def select_devices():
    """Probe input/output audio devices and pick the best match.

    Honors config.AUDIO_INPUT_DEVICE / AUDIO_OUTPUT_DEVICE overrides (int
    index) — otherwise auto-picks by name keyword priority (Victus > USB
    Audio > HDMI Audio > default), falling back to the first available
    device of that kind if nothing matches, or system default if there is
    no device at all.

    Returns (in_idx, in_device, out_idx, out_device) — the *_device values
    are raw sounddevice.query_devices() entries, or None.
    """
    try:
        import sounddevice as sd
        devices = sd.query_devices()
    except Exception as e:
        print(f'[AUDIO] cannot query devices: {e} — using system default')
        return None, None, None, None

    in_idx  = _pick(devices, 'max_input_channels', config.AUDIO_INPUT_DEVICE)
    out_idx = _pick(devices, 'max_output_channels', config.AUDIO_OUTPUT_DEVICE)
    in_dev  = devices[in_idx] if in_idx is not None else None
    out_dev = devices[out_idx] if out_idx is not None else None
    in_name  = in_dev['name'] if in_dev else 'system default'
    out_name = out_dev['name'] if out_dev else 'system default'
    print(f'[AUDIO] Using input: "{in_name}" (idx {in_idx}), '
          f'output: "{out_name}" (idx {out_idx})')
    return in_idx, in_dev, out_idx, out_dev
