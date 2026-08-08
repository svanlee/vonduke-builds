"""Training environment — no game, no Minecraft. Pure cognitive loop + goal injection."""
import numpy as np
from envs.base_env import BaseEnvironment

class TrainingEnv(BaseEnvironment):
    def get_frame(self) -> np.ndarray:
        return np.zeros((480, 640, 3), dtype=np.uint8)

    def send_action(self, action: dict):
        pass  # no-op — training mode doesn't actuate hardware

    def get_telemetry(self) -> dict:
        return {"env": "training", "mode": "cognitive_only"}

    def get_env_name(self) -> str:
        return "training"
