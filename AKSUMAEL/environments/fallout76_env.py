# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.2.0 — Fallout 76 Environment Adapter     ║
# ╚══════════════════════════════════════════════════════╝
#
# Same capture-card + HID-keystroke I/O path as Minecraft (ActionExecutor
# just emits raw key/click/look packets over the KB2040/CH9329 chip — it has
# no idea what game is running), so this adapter reuses it directly. What's
# genuinely F76-specific is the object vocabulary, the action→key mapping,
# and the reward shaping.

import os
import time

import config
from core.environment import EnvironmentAdapter, Observation, LowRewardStuckTracker
from vision.yolo import YOLODetector
from actions.executor import ActionExecutor


class Fallout76Env(EnvironmentAdapter):
    ENV_NAME = "fallout76"

    # Semantic action names the planner/curriculum layer can emit. translate()
    # below maps these to the raw key/click/look dict ActionExecutor expects.
    ACTION_SPACE = (
        'move_forward', 'move_back', 'strafe_left', 'strafe_right', 'look',
        'interact', 'reload', 'pip_boy', 'pip_boy_alt',
        'weapon_1', 'weapon_2', 'weapon_3', 'weapon_4', 'weapon_5', 'weapon_6',
        'sprint', 'crouch', 'jump', 'vats',
    )

    _KEY_MAP = {
        'move_forward': 'w', 'move_back': 's',
        'strafe_left': 'a', 'strafe_right': 'd',
        'interact': 'e', 'reload': 'r',
        'pip_boy': 'f', 'pip_boy_alt': 'tab',
        'weapon_1': '1', 'weapon_2': '2', 'weapon_3': '3',
        'weapon_4': '4', 'weapon_5': '5', 'weapon_6': '6',
        'sprint': 'shift', 'crouch': 'ctrl', 'jump': 'space',
        'vats': 'v',
    }

    OBJECT_CLASSES = (
        'player_hud', 'health_bar', 'ap_bar', 'caps_count', 'weight_indicator',
        'enemy_human', 'enemy_creature', 'enemy_robot', 'loot_bag', 'door',
        'terminal', 'workbench', 'waypoint_marker', 'fast_travel_icon',
        'pip_boy_closed', 'pip_boy_open', 'ammo_pickup', 'item_pickup',
        'radiation_zone',
    )

    # Curriculum-seeded goals — pushed onto GoalStack the same way Minecraft's
    # CurriculumGenerator seeds tech-tree goals, but F76 has no tech tree so
    # these are the flat frontier the curriculum picks from.
    GOALS = (
        'explore_area', 'loot_container', 'follow_waypoint',
        'kill_enemy', 'craft_item', 'fast_travel',
    )

    _ENEMY_CLASSES = ('enemy_human', 'enemy_creature', 'enemy_robot')
    _LOOT_CLASSES = ('loot_bag', 'ammo_pickup', 'item_pickup')

    _WEIGHTS_PATH = 'data/models/aksumael_f76.pt'

    def __init__(self):
        super().__init__()
        self.executor = None
        self.yolo = None
        self._stuck = LowRewardStuckTracker(
            low_thresh=getattr(config, 'F76_LOW_REWARD_THRESH', 0.05),
            stuck_ticks=getattr(config, 'F76_STUCK_TICKS', 150),
        )
        self._dead = False
        self._prev_caps = None
        self._prev_health = None

        try:
            self.executor = ActionExecutor()
        except Exception as e:
            self._mark_unavailable(f'action executor init failed: {e}')
            return

        try:
            self.yolo = YOLODetector()
            if os.path.exists(self._WEIGHTS_PATH):
                self.yolo.reload_weights(self._WEIGHTS_PATH)
            else:
                print(f'[{self.ENV_NAME}] no F76-specific YOLO weights at '
                      f'{self._WEIGHTS_PATH} — using default model '
                      f'({config.YOLO_MODEL}); detections will be low quality '
                      f'until an F76 dataset is trained.')
        except Exception as e:
            print(f'[{self.ENV_NAME}] YOLO init failed: {e} — running without vision')
            self.yolo = None

    # ── EnvironmentAdapter ──────────────────────────────────────────
    def observe(self, frame) -> Observation:
        if not self.available:
            return Observation(alive=not self._dead, raw_frame=frame)

        objects = []
        if self.yolo is not None and self.yolo.model is not None and frame is not None:
            objects = self.yolo.detect(frame)

        hud = self._hud_from_objects(objects)

        return Observation(
            objects=objects,
            hud=hud,
            position=None,   # no in-game telemetry equivalent to MC's F3 overlay
            alive=not self._dead,
            raw_frame=frame,
        )

    def execute(self, action: dict) -> None:
        if not self.available or not action:
            return
        self.executor.execute(self.translate(action))

    def translate(self, action: dict) -> dict:
        """Map a semantic F76 action dict ({'action': 'move_forward', ...})
        to the raw key/click/look dict ActionExecutor understands. Actions
        already in raw form (have 'key'/'click'/'look') pass through
        unchanged so callers can still hand-drive when needed."""
        if 'key' in action or 'click' in action or 'look' in action:
            return action

        name = action.get('action')
        out = {'source': self.ENV_NAME}
        if name in self._KEY_MAP:
            out['key'] = self._KEY_MAP[name]
        if 'look' in action:
            out['look'] = action['look']
        if name == 'attack':
            out['click'] = 'left'
        if 'delay_ms' in action:
            out['delay_ms'] = action['delay_ms']
        return out

    def reward(self, observation: Observation, action: dict) -> float:
        if not self.available:
            return 0.0

        r = 0.0
        objects = observation.objects or []
        labels = [o.get('label') for o in objects]

        if objects:
            r += 0.15  # activity/visibility signal, same idea as Minecraft's

        if any(l in self._LOOT_CLASSES for l in labels):
            r += 0.1
        if any(l in self._ENEMY_CLASSES for l in labels):
            r -= 0.05  # unresolved threat in frame
        if 'radiation_zone' in labels:
            r -= 0.2
        if 'waypoint_marker' in labels or 'fast_travel_icon' in labels:
            r += 0.05

        conf_vals = [o.get('conf', 0) for o in objects]
        if conf_vals:
            r += 0.3 * (sum(conf_vals) / len(conf_vals))

        if action and action.get('action') in ('move_forward',) and 'key' not in action:
            r += 0.02  # small forward-progress bonus, mirrors MC's 'w' bonus

        r = round(r, 3)
        self._stuck.update(r)
        return r

    def reset(self) -> None:
        if self.available:
            self.executor.release_all()
        self._dead = False
        self._stuck.reset()

    def is_alive(self) -> bool:
        return not self._dead

    def mark_dead(self) -> None:
        """No reliable per-frame death signal exists yet (no 'you died'
        class in OBJECT_CLASSES) — a death-detection behavior calls this
        explicitly once one is added, same pattern as MinecraftEnv."""
        self._dead = True

    def is_stuck(self) -> bool:
        return self._stuck.is_stuck()

    def close(self) -> None:
        if self.available:
            self.executor.close()

    # ── Internals ────────────────────────────────────────────────
    def _hud_from_objects(self, objects: list) -> dict:
        labels = {o.get('label') for o in objects}
        return {
            'health_bar_visible': 'health_bar' in labels,
            'health_bar_missing': 'health_bar' not in labels,
            'ap_bar_visible': 'ap_bar' in labels,
            'pip_boy_open': 'pip_boy_open' in labels,
            'in_radiation': 'radiation_zone' in labels,
            'ts': time.time(),
        }
