"""
Hardware manager — monitors all hardware adapters and provides
a summary string for LLM context.
"""
import threading
import time
from hardware.capture_card import CaptureCardAdapter
from hardware.ftdi import FTDIAdapter
from hardware.gpu import GPUAdapter
from hardware.cpu import CPUAdapter

class HardwareManager:
    def __init__(self):
        self._adapters = {
            "capture_card": CaptureCardAdapter(),
            "ftdi": FTDIAdapter(),
            "gpu": GPUAdapter(),
            "cpu": CPUAdapter(),
        }
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._check_interval = 30  # seconds between hardware checks

    def start(self):
        """Start background hardware monitoring."""
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        print("[Hardware] Manager started.")

    def stop(self):
        self._running = False

    def _monitor_loop(self):
        while self._running:
            with self._lock:
                for name, adapter in self._adapters.items():
                    try:
                        adapter.check()
                    except Exception as e:
                        print(f"[Hardware] Error checking {name}: {e}")
            time.sleep(self._check_interval)

    def check_all(self) -> dict:
        """Run a check on all adapters and return status dict."""
        results = {}
        with self._lock:
            for name, adapter in self._adapters.items():
                try:
                    results[name] = {
                        "available": adapter.check(),
                        "metrics": adapter.get_metrics()
                    }
                except Exception as e:
                    results[name] = {"available": False, "error": str(e)}
        return results

    def summary(self) -> str:
        """Return a hardware summary string for LLM context."""
        lines = []
        with self._lock:
            for adapter in self._adapters.values():
                try:
                    lines.append(f"  {adapter.get_summary()}")
                except Exception as e:
                    lines.append(f"  {adapter.state.name}: ERROR ({e})")
        return "Hardware:\n" + "\n".join(lines)

    def get(self, device_id: str):
        """Get a specific adapter by device_id."""
        return self._adapters.get(device_id)
