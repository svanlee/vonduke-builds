"""Capture card adapter — reports on AKSUMAEL's vision source.

Originally hardcoded to /dev/video2 (the Rybozen HDMI capture card). Since
2026-08-08 the runtime can fall back to any of config.CAMERA_FALLBACK_INDICES
when that card is missing, so "MISSING" here should mean *no camera at all*,
not "the preferred one is gone" — otherwise the health summary reads as a
hardware fault while the bot is running perfectly well off the webcam.
"""
import os
import time

import config
from hardware.hw_base import HardwareBase

# The monitor loop calls check() every 30s forever; a permanently absent
# device would otherwise grow this list without bound for the life of the
# process. Only the most recent failures are worth keeping.
MAX_ERRORS = 20

# core/runtime.py::_write_health_log writes the source CaptureThread actually
# settled on here every 60 ticks. Reading it is how this adapter learns that a
# device node existing is not the same as it working — see _live_source().
HEALTH_LOG_PATH    = "/tmp/aksumael_health.txt"
HEALTH_MAX_AGE_SEC = 600


class CaptureCardAdapter(HardwareBase):
    def __init__(self, device_path=None):
        super().__init__("capture_card", "Capture Card")
        # Explicit path pins the adapter to one device (used by tests and by
        # anything that genuinely cares about the card itself); the default
        # follows whatever the runtime is allowed to use.
        self.device_path = device_path
        self._pinned = device_path is not None

    def _screenshot_label(self):
        """Label for the screen-grab fallback, or None when it's disabled."""
        if self._pinned or not getattr(config, 'SCREENSHOT_FALLBACK_ENABLED', True):
            return None
        display = getattr(config, 'SCREENSHOT_DISPLAY', ':0')
        return f"screenshot fallback ({display})"

    def _screenshot_present(self) -> bool:
        """True when the X display exists. Socket check only, for the same
        reason the video nodes get an existence check — actually grabbing a
        frame here would duplicate work CaptureThread is already doing."""
        display = getattr(config, 'SCREENSHOT_DISPLAY', ':0')
        return os.path.exists(f"/tmp/.X11-unix/X{display.lstrip(':').split('.')[0]}")

    def _candidate_paths(self) -> list:
        if self._pinned:
            return [self.device_path]
        indices = [config.CAMERA_INDEX]
        indices.extend(getattr(config, 'CAMERA_FALLBACK_INDICES', []) or [])
        seen, paths = set(), []
        for idx in indices:
            if idx is None or idx < 0 or idx in seen:
                continue
            seen.add(idx)
            paths.append(f"/dev/video{idx}")
        # Last in the list because it is last in core/capture.py's chain: a
        # screen grab is only reached once every camera has failed.
        shot = self._screenshot_label()
        if shot:
            paths.append(shot)
        return paths

    def _live_source(self):
        """What CaptureThread actually settled on, per the runtime's own
        health snapshot — or None if that snapshot is missing or stale.

        Node existence is not usability, and on this laptop the gap is not
        hypothetical: /dev/video0 exists and opens but only ever delivers
        black, so it fails probe_camera()'s real-frame floor and the runtime
        skips past it. An existence-only check would therefore report a
        healthy camera while the bot was really running off a screen grab —
        and would mean the screenshot entry could never be reported at all.
        Preferring the live value keeps the manifest describing what is
        happening rather than what is merely plugged in."""
        if self._pinned:
            return None
        try:
            if time.time() - os.path.getmtime(HEALTH_LOG_PATH) > HEALTH_MAX_AGE_SEC:
                return None       # bot stopped, or this file is a leftover
            with open(HEALTH_LOG_PATH) as f:
                for line in f:
                    if line.startswith('camera:'):
                        value = line.split(':', 1)[1].strip()
                        return value or None
        except Exception:
            return None
        return None

    def _present_path(self):
        """The source actually in use if the runtime says so, else the first
        candidate that exists.

        Existence only on the fallback path — actually opening the device
        would fight the running CaptureThread for its exclusive handle."""
        live = self._live_source()
        if live:
            return None if live.startswith('NONE') else live

        for path in self._candidate_paths():
            if path.startswith('screenshot'):
                if self._screenshot_present():
                    return path
            elif os.path.exists(path):
                return path
        return None

    def check(self) -> bool:
        present = self._present_path()
        self.state.available   = present is not None
        self.state.last_checked = time.time()
        if present is None:
            self.state.errors.append(
                f"no camera among {self._candidate_paths()} at {time.time():.0f}")
            del self.state.errors[:-MAX_ERRORS]
        return self.state.available

    def get_metrics(self) -> dict:
        present = self._present_path()
        is_shot = bool(present) and present.startswith('screenshot')
        self.state.metrics = {
            "device":     present or (self.device_path if self._pinned else None),
            "candidates": self._candidate_paths(),
            "present":    present is not None,
            # A reader that only checks `present` would conclude vision is
            # healthy while the card is unplugged and the bot is looking at
            # its own desktop. Say which of the two it is.
            "kind":       "screenshot" if is_shot else ("camera" if present else None),
            "card_present": bool(present) and not is_shot,
        }
        return self.state.metrics

    def get_summary(self) -> str:
        present = self._present_path()
        if present is None:
            return f"CaptureCard: MISSING (none of {self._candidate_paths()})"
        if present.startswith('screenshot'):
            # Deliberately not "OK": no camera exists, and the frames the bot
            # is getting are its own desktop rather than the game.
            return (f"CaptureCard: MISSING — using {present} "
                    f"(desktop, not the game)")
        return f"CaptureCard: OK ({present})"
