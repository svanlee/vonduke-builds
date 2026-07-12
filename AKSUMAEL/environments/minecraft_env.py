# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.2.0 — Minecraft Environment Adapter      ║
# ╚══════════════════════════════════════════════════════╝
#
# Thin EnvironmentAdapter wrapping the existing Minecraft I/O stack
# (YOLODetector, ActionExecutor, F3 reader, RewardSystem). This does NOT
# replace core/runtime.py — that file still drives Minecraft directly
# against those same classes and is untouched by this adapter layer, so
# ACTIVE_ENV="minecraft" changes nothing about how AKSUMAEL actually plays.
# This adapter exists so a future environment-agnostic runtime can drive
# Minecraft through the same observe/execute/reward/reset interface as
# Fallout 76, the driving sim, and the robocar.

import config
from core.environment import EnvironmentAdapter, Observation
from vision.yolo import YOLODetector
from vision.f3_reader import read_f3
from actions.executor import ActionExecutor
from memory.reward import RewardSystem


class MinecraftEnv(EnvironmentAdapter):
    ENV_NAME = "minecraft"

    ACTION_SPACE = (
        'w', 'a', 's', 'd', 'space', 'ctrl', 'shift', 'e', 'f', 'q', 'esc',
        '1', '2', '3', '4', '5', '6', '7', '8', '9',
        'click_left', 'click_right', 'look',
    )

    # Minecraft's classes come from the trainable YOLO label DB
    # (data/models/aksumael_mc.pt + data/yolo_labels.json) rather than a
    # fixed tuple — new ore/mob/block labels get taught at runtime via
    # ui/labeling.py, so this stays empty here by design.
    OBJECT_CLASSES = ()

    def __init__(self):
        super().__init__()
        try:
            self.yolo = YOLODetector()
            self.executor = ActionExecutor()
            self.reward_system = RewardSystem()
        except Exception as e:
            self._mark_unavailable(str(e))
            self.yolo = self.executor = self.reward_system = None
        self._dead = False

    def observe(self, frame) -> Observation:
        if not self.available or frame is None:
            return Observation(alive=not self._dead, raw_frame=frame)

        objects = self.yolo.detect(frame) if self.yolo.model is not None else []
        f3 = read_f3(frame) or {}
        position = None
        if f3.get('x') is not None and f3.get('z') is not None:
            position = (f3.get('x'), f3.get('y'), f3.get('z'))

        return Observation(
            objects=objects,
            hud={'f3': f3},
            position=position,
            alive=not self._dead,
            raw_frame=frame,
        )

    def execute(self, action: dict) -> None:
        if not self.available or not action:
            return
        self.executor.execute(action)

    def reward(self, observation: Observation, action: dict) -> float:
        if not self.available:
            return 0.0
        state = {'objects': observation.objects}
        return self.reward_system.compute(state, action or {})

    def reset(self) -> None:
        if self.available:
            self.executor.release_all()
        self._dead = False

    def is_alive(self) -> bool:
        return not self._dead

    def mark_dead(self) -> None:
        """Called by respawn/death-detection behaviors — kept separate from
        observe() since Minecraft has no single reliable per-frame death
        signal (it's inferred from screen-text/HUD-state behaviors)."""
        self._dead = True

    def close(self) -> None:
        if self.available:
            self.executor.close()
