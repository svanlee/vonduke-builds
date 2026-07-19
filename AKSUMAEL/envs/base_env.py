"""Abstract base for all AKSUMAEL environments."""
import abc
import numpy as np

class BaseEnvironment(abc.ABC):
    @abc.abstractmethod
    def get_frame(self) -> np.ndarray:
        """Capture current visual frame."""

    @abc.abstractmethod
    def send_action(self, action: dict):
        """Send action to environment (keyboard, mouse, serial, ZeroMQ, etc.)."""

    @abc.abstractmethod
    def get_telemetry(self) -> dict:
        """Return environment-specific telemetry (health, speed, GPS, etc.)."""

    @abc.abstractmethod
    def get_env_name(self) -> str:
        """Return environment identifier string: 'minecraft', 'vehicle', etc."""

    def on_goal_complete(self, goal: str):
        """Optional hook called when a goal completes."""

    def on_episode_end(self, reason: str):
        """Optional hook called when an episode ends."""
