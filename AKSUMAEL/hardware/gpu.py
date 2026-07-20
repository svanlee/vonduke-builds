"""GPU adapter — RTX 4050 via nvidia-smi."""
import subprocess
import time
from hardware.hw_base import HardwareBase

class GPUAdapter(HardwareBase):
    def __init__(self):
        super().__init__("gpu", "RTX 4050")

    def check(self) -> bool:
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5
            )
            self.state.available = result.returncode == 0
        except Exception:
            self.state.available = False
        self.state.last_checked = time.time()
        return self.state.available

    def get_metrics(self) -> dict:
        try:
            result = subprocess.run([
                "nvidia-smi",
                "--query-gpu=name,temperature.gpu,utilization.gpu,utilization.memory,memory.used,memory.total",
                "--format=csv,noheader,nounits"
            ], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                parts = [p.strip() for p in result.stdout.strip().split(",")]
                self.state.metrics = {
                    "name": parts[0],
                    "temp_c": int(parts[1]),
                    "gpu_util_pct": int(parts[2]),
                    "mem_util_pct": int(parts[3]),
                    "mem_used_mb": int(parts[4]),
                    "mem_total_mb": int(parts[5]),
                    "mem_free_mb": int(parts[5]) - int(parts[4])
                }
        except Exception as e:
            self.state.metrics = {"error": str(e)}
        return self.state.metrics

    def get_summary(self) -> str:
        m = self.get_metrics()
        if "error" in m:
            return f"GPU: ERROR ({m['error']})"
        return (f"GPU: {m.get('name','?')} | "
                f"temp={m.get('temp_c','?')}°C | "
                f"util={m.get('gpu_util_pct','?')}% | "
                f"VRAM {m.get('mem_used_mb','?')}/{m.get('mem_total_mb','?')}MB")
