"""Capture card adapter — /dev/video2."""
import subprocess
import time
from hardware.hw_base import HardwareBase

class CaptureCardAdapter(HardwareBase):
    def __init__(self, device_path="/dev/video2"):
        super().__init__("capture_card", "Capture Card")
        self.device_path = device_path

    def check(self) -> bool:
        import os
        available = os.path.exists(self.device_path)
        self.state.available = available
        self.state.last_checked = time.time()
        if not available:
            self.state.errors.append(f"{self.device_path} not found at {time.time():.0f}")
        return available

    def get_metrics(self) -> dict:
        import os
        self.state.metrics = {
            "device": self.device_path,
            "present": os.path.exists(self.device_path)
        }
        return self.state.metrics

    def get_summary(self) -> str:
        import os
        present = os.path.exists(self.device_path)
        return f"CaptureCard: {'OK' if present else 'MISSING'} ({self.device_path})"
