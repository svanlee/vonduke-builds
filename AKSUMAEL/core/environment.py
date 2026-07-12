# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.2.0 — Environment Adapter (base)         ║
# ║  The I/O boundary between the reusable core AI        ║
# ║  (planner, curriculum, episode memory, world model)   ║
# ║  and a specific environment (game or robot).          ║
# ╚══════════════════════════════════════════════════════╝
#
# An adapter's only job is turning a raw frame into an Observation and
# turning an action dict into real-world effects (HID keystrokes, serial,
# ROS2 topics, ...). It holds no planning/goal logic — that stays in
# core/planner.py, core/curriculum.py, core/episode_memory.py, which are
# handed whichever adapter is active via core/env_registry.py and never
# need to know what's behind it.

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Observation:
    """Environment-agnostic snapshot of one tick."""
    objects: list = field(default_factory=list)   # [{label, conf, box:[x1,y1,x2,y2], ...}] — see YOLODetector.detect()
    hud: dict = field(default_factory=dict)        # env-specific HUD/state readout
    position: tuple = None                          # (x, y, z) / (x, y, heading) / None
    alive: bool = True
    raw_frame: object = None                        # optional passthrough for behaviors
    extra: dict = field(default_factory=dict)


class EnvironmentAdapter(ABC):
    """Subclass per environment. See environments/ for implementations."""

    ENV_NAME: str = "base"
    ACTION_SPACE: tuple = ()
    OBJECT_CLASSES: tuple = ()

    def __init__(self):
        # Subclasses flip this False (in __init__, before raising nothing —
        # adapters must not raise on missing hardware/game) when the
        # underlying game/robot/library couldn't be reached, so callers can
        # check .available instead of crashing the runtime.
        self.available = True
        self.unavailable_reason = None

    @abstractmethod
    def observe(self, frame) -> Observation:
        """Turn a raw capture frame into an Observation."""
        raise NotImplementedError

    @abstractmethod
    def execute(self, action: dict) -> None:
        """Route an action dict to the correct executor (keyboard/mouse,
        serial, ROS2, ...)."""
        raise NotImplementedError

    @abstractmethod
    def reward(self, observation: Observation, action: dict) -> float:
        """Compute the reward for this tick given the observation that
        resulted from `action`."""
        raise NotImplementedError

    @abstractmethod
    def reset(self) -> None:
        """Release held inputs / stop motors and clear per-episode state."""
        raise NotImplementedError

    @abstractmethod
    def is_alive(self) -> bool:
        """False when the episode has ended (death, crash, e-stop, ...)."""
        raise NotImplementedError

    def close(self) -> None:
        """Optional cleanup hook (close serial ports, shut down ROS nodes)."""
        pass

    def _mark_unavailable(self, reason: str) -> None:
        self.available = False
        self.unavailable_reason = reason
        print(f'[{self.ENV_NAME}] environment not available: {reason}')


class LowRewardStuckTracker:
    """Anti-stuck heuristic shared by adapters that don't have their own
    stuck-detection behavior (mirrors the consecutive-low-reward-streak
    approach core/runtime.py uses for Minecraft): counts consecutive
    below-threshold reward ticks and flags 'stuck' once a streak threshold
    is crossed, so a caller can trigger an intervention (turn, backtrack,
    pick a new goal)."""

    def __init__(self, low_thresh: float = 0.05, stuck_ticks: int = 150):
        self.low_thresh = low_thresh
        self.stuck_ticks = stuck_ticks
        self.streak = 0

    def update(self, reward: float) -> bool:
        """Feed one tick's reward; returns the new is_stuck() value."""
        if reward < self.low_thresh:
            self.streak += 1
        else:
            self.streak = 0
        return self.is_stuck()

    def is_stuck(self) -> bool:
        return self.streak >= self.stuck_ticks

    def reset(self) -> None:
        self.streak = 0
