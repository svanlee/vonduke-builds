# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.2.0 — Driving Simulator Environment      ║
# ║  Generic adapter for any keyboard-driven sim (BeamNG, ║
# ║  Assetto Corsa, ...) — same capture-card + HID path.  ║
# ╚══════════════════════════════════════════════════════╝

import os
import time

import config
from core.environment import EnvironmentAdapter, Observation, LowRewardStuckTracker
from vision.yolo import YOLODetector
from actions.executor import ActionExecutor


class DrivingEnv(EnvironmentAdapter):
    ENV_NAME = "driving"

    ACTION_SPACE = (
        'steer_left', 'steer_right', 'accelerate', 'brake',
        'handbrake', 'shift_up', 'shift_down',
    )

    # WASD by default (same physical HID path as Minecraft/F76); override
    # per-sim in data/envs/driving.yaml if a title expects arrow keys.
    _KEY_MAP = {
        'steer_left': 'a', 'steer_right': 'd',
        'accelerate': 'w', 'brake': 's',
        'handbrake': 'space', 'shift_up': 'e', 'shift_down': 'q',
    }

    OBJECT_CLASSES = (
        'road_center', 'road_edge_left', 'road_edge_right',
        'vehicle_ahead', 'vehicle_oncoming',
        'traffic_light_red', 'traffic_light_green', 'traffic_light_yellow',
        'stop_sign', 'speed_limit_sign', 'barrier', 'pedestrian',
        'finish_line', 'checkpoint',
    )

    GOALS = ('stay_on_road', 'reach_checkpoint', 'minimize_lap_time')

    _WEIGHTS_PATH = 'data/models/aksumael_driving.pt'

    def __init__(self):
        super().__init__()
        self.executor = None
        self.yolo = None
        self._stuck = LowRewardStuckTracker(
            low_thresh=getattr(config, 'DRIVING_LOW_REWARD_THRESH', 0.05),
            stuck_ticks=getattr(config, 'DRIVING_STUCK_TICKS', 150),
        )
        self._crashed = False
        self._last_accel_tick = 0

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
                print(f'[{self.ENV_NAME}] no driving-specific YOLO weights at '
                      f'{self._WEIGHTS_PATH} — using default model '
                      f'({config.YOLO_MODEL}); road/lane detections will be '
                      f'unreliable until a driving dataset is trained.')
        except Exception as e:
            print(f'[{self.ENV_NAME}] YOLO init failed: {e} — running without vision')
            self.yolo = None

    # ── EnvironmentAdapter ──────────────────────────────────────────
    def observe(self, frame) -> Observation:
        if not self.available:
            return Observation(alive=not self._crashed, raw_frame=frame)

        objects = []
        if self.yolo is not None and self.yolo.model is not None and frame is not None:
            objects = self.yolo.detect(frame)

        frame_width = frame.shape[1] if frame is not None and hasattr(frame, 'shape') else None
        hud = self._lane_state(objects, frame_width)

        return Observation(
            objects=objects,
            hud=hud,
            position=None,   # no GPS/telemetry hook in the generic sim case
            alive=not self._crashed,
            raw_frame=frame,
        )

    def execute(self, action: dict) -> None:
        if not self.available or not action:
            return
        self.executor.execute(self.translate(action))

    def translate(self, action: dict) -> dict:
        if 'key' in action or 'click' in action or 'look' in action:
            return action
        name = action.get('action')
        out = {'source': self.ENV_NAME}
        if name in self._KEY_MAP:
            out['key'] = self._KEY_MAP[name]
        if 'delay_ms' in action:
            out['delay_ms'] = action['delay_ms']
        return out

    def reward(self, observation: Observation, action: dict) -> float:
        if not self.available:
            return 0.0

        r = 0.0
        hud = observation.hud or {}
        labels = [o.get('label') for o in (observation.objects or [])]

        # Speed proxy: reward holding the accelerate action, since there's
        # no generic telemetry hook for real speed across arbitrary sims.
        act_name = action.get('action') if action else None
        if act_name == 'accelerate' or (action or {}).get('key') == self._KEY_MAP['accelerate']:
            r += 0.15

        # Lane centering: road_edge_left/right both present and roughly
        # balanced around frame center = well-centered; only one edge
        # visible = drifting toward it; neither edge = off-road (penalty).
        centering = hud.get('lane_offset')
        if centering is not None:
            r += 0.3 * (1.0 - min(abs(centering), 1.0))
        elif 'road_edge_left' not in labels and 'road_edge_right' not in labels \
                and 'road_center' not in labels:
            r -= 0.25  # no road markers in frame at all — likely off-road

        if 'vehicle_ahead' in labels or 'vehicle_oncoming' in labels or 'pedestrian' in labels:
            r -= 0.1  # unresolved collision risk in frame
        if 'traffic_light_red' in labels or 'stop_sign' in labels:
            if act_name == 'brake' or (action or {}).get('key') == self._KEY_MAP['brake']:
                r += 0.1
            else:
                r -= 0.1
        if 'checkpoint' in labels or 'finish_line' in labels:
            r += 0.2

        r = round(r, 3)
        self._stuck.update(r)
        return r

    def reset(self) -> None:
        if self.available:
            self.executor.release_all()
        self._crashed = False
        self._stuck.reset()

    def is_alive(self) -> bool:
        return not self._crashed

    def mark_crashed(self) -> None:
        self._crashed = True

    def is_stuck(self) -> bool:
        return self._stuck.is_stuck()

    def close(self) -> None:
        if self.available:
            self.executor.close()

    # ── Internals ────────────────────────────────────────────────
    @staticmethod
    def _lane_state(objects: list, frame_width) -> dict:
        left = next((o for o in objects if o.get('label') == 'road_edge_left'), None)
        right = next((o for o in objects if o.get('label') == 'road_edge_right'), None)
        state = {'ts': time.time()}

        if left and right and frame_width:
            lx = (left['box'][0] + left['box'][2]) / 2
            rx = (right['box'][0] + right['box'][2]) / 2
            lane_center = (lx + rx) / 2
            frame_center = frame_width / 2
            lane_width = max(rx - lx, 1)
            # normalized offset in [-1, 1]-ish; 0 = centered in lane
            state['lane_offset'] = (frame_center - lane_center) / (lane_width / 2)
        state['road_edge_left_visible'] = left is not None
        state['road_edge_right_visible'] = right is not None
        return state
