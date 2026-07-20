"""CPU adapter — load average and memory."""
import time
import os
from hardware.hw_base import HardwareBase

class CPUAdapter(HardwareBase):
    def __init__(self):
        super().__init__("cpu", "CPU")
        self.state.available = True

    def check(self) -> bool:
        self.state.available = True
        self.state.last_checked = time.time()
        return True

    def get_metrics(self) -> dict:
        try:
            load1, load5, load15 = os.getloadavg()
            # Read memory from /proc/meminfo
            mem = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split()
                    if parts[0] in ("MemTotal:", "MemAvailable:", "MemFree:"):
                        mem[parts[0].rstrip(":")] = int(parts[1])  # kB
            self.state.metrics = {
                "load_1m": round(load1, 2),
                "load_5m": round(load5, 2),
                "load_15m": round(load15, 2),
                "mem_total_mb": mem.get("MemTotal", 0) // 1024,
                "mem_available_mb": mem.get("MemAvailable", 0) // 1024,
                "mem_used_mb": (mem.get("MemTotal", 0) - mem.get("MemAvailable", 0)) // 1024
            }
        except Exception as e:
            self.state.metrics = {"error": str(e)}
        return self.state.metrics

    def get_summary(self) -> str:
        m = self.get_metrics()
        if "error" in m:
            return f"CPU: ERROR"
        return (f"CPU: load={m.get('load_1m','?')} | "
                f"RAM {m.get('mem_used_mb','?')}/{m.get('mem_total_mb','?')}MB")
