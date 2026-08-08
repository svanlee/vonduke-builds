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


class CaptureCardAdapter(HardwareBase):
    def __init__(self, device_path=None):
        super().__init__("capture_card", "Capture Card")
        # Explicit path pins the adapter to one device (used by tests and by
        # anything that genuinely cares about the card itself); the default
        # follows whatever the runtime is allowed to use.
        self.device_path = device_path
        self._pinned = device_path is not None

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
        return paths

    def _present_path(self):
        """First candidate device node that exists, or None.

        Existence only — actually opening the device would fight the running
        CaptureThread for its exclusive handle."""
        for path in self._candidate_paths():
            if os.path.exists(path):
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
        self.state.metrics = {
            "device":     present or (self.device_path if self._pinned else None),
            "candidates": self._candidate_paths(),
            "present":    present is not None,
        }
        return self.state.metrics

    def get_summary(self) -> str:
        present = self._present_path()
        if present is None:
            return f"CaptureCard: MISSING (none of {self._candidate_paths()})"
        return f"CaptureCard: OK ({present})"
