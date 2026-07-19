"""Minecraft environment — wraps existing KB2040 + v4l2 capture card."""
import numpy as np
from envs.base_env import BaseEnvironment

class MinecraftEnv(BaseEnvironment):
    """
    Lazy imports — only available on the Victus machine. Wraps the same
    VideoCapturePipeline + ControllerRouter used by core/runtime.py, so a
    caller here sees the same frames/controls the FSM/overseer already use.
    """

    def __init__(self, yolo_detector=None):
        from core.capture import VideoCapturePipeline
        from vision.yolo import YOLODetector
        from input.controller_router import ControllerRouter
        import config
        self._cap = VideoCapturePipeline(yolo_detector or YOLODetector(),
                                          device_index=config.CAMERA_INDEX)
        self._cap.start()
        self._ctrl = ControllerRouter()
        self._ctrl.start()

    def get_frame(self) -> np.ndarray:
        return self._cap.latest_small_frame

    def send_action(self, action: dict):
        self._ctrl.update_aksumael(action)
        return self._ctrl.resolve()

    def get_telemetry(self) -> dict:
        return {}  # Minecraft telemetry comes from F3 overlay, not this env

    def get_env_name(self) -> str:
        return 'minecraft'
