"""FTDI USB-serial adapter — /dev/ttyUSB0 (KB2040 bridge)."""
import os
import time
from hardware.hw_base import HardwareBase

class FTDIAdapter(HardwareBase):
    def __init__(self, device_path="/dev/ttyUSB0"):
        super().__init__("ftdi", "FTDI/KB2040")
        self.device_path = device_path

    def check(self) -> bool:
        available = os.path.exists(self.device_path)
        self.state.available = available
        self.state.last_checked = time.time()
        return available

    def get_metrics(self) -> dict:
        self.state.metrics = {
            "device": self.device_path,
            "present": os.path.exists(self.device_path)
        }
        return self.state.metrics

    def get_summary(self) -> str:
        present = os.path.exists(self.device_path)
        return f"FTDI/KB2040: {'OK' if present else 'MISSING'} ({self.device_path})"
