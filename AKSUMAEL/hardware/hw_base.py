"""Base class for all hardware adapters."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import time

@dataclass
class HardwareState:
    device_id: str
    name: str
    available: bool = False
    last_checked: float = field(default_factory=time.time)
    metrics: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)

class HardwareBase(ABC):
    def __init__(self, device_id: str, name: str):
        self.state = HardwareState(device_id=device_id, name=name)

    @abstractmethod
    def check(self) -> bool:
        """Check if device is available. Returns True if healthy."""
        pass

    @abstractmethod
    def get_metrics(self) -> dict:
        """Return current device metrics as dict."""
        pass

    def get_summary(self) -> str:
        status = "OK" if self.state.available else "UNAVAILABLE"
        return f"{self.state.name}: {status} | {self.state.metrics}"
