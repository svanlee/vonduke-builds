"""
GOAT Racer One / AKV vehicle environment stub.

Hardware: Traxxas 1/10 AWD, Jetson Orin Nano 8GB (onboard GPU), Intel
          RealSense D435, VESC motor controller, sensored brushless,
          LiPo 2S 5000mAh.
Comms: ZeroMQ/Protobuf (not heavy ROS2).
Waypoints: AprilTags.

Compute split: the Jetson Orin Nano has its own GPU and runs YOLO + the
ONNX driving policy locally — it does NOT ship raw camera frames to
Victus. This env sends high-level directives and receives back
already-processed state (detections + speed/heading/battery/waypoint)
over ZeroMQ. Contrast with envs/robocar_env.py (AK-01, Pi 4, no GPU),
where raw frames DO need to travel to Victus for inference.

Set AKSUMAEL_ENV=vehicle to activate.
"""
import logging
import numpy as np
from envs.base_env import BaseEnvironment

log = logging.getLogger(__name__)

# ZeroMQ addresses — update to match Jetson Orin Nano network config
JETSON_CMD_ADDR   = 'tcp://localhost:5555'   # send high-level directives
JETSON_STATE_ADDR = 'tcp://localhost:5557'   # receive processed state (detections + telemetry)


class VehicleEnv(BaseEnvironment):
    """
    Stub implementation — replace internals with live ZeroMQ connections
    once the Jetson Orin Nano is wired up.
    """

    def __init__(self):
        self._zmq_ctx = None
        self._cmd_sock = None
        self._state_sock = None
        self._last_state = {}
        try:
            import zmq
            ctx = zmq.Context()
            self._zmq_ctx = ctx
            self._cmd_sock   = ctx.socket(zmq.PUSH)
            self._state_sock = ctx.socket(zmq.SUB)
            self._cmd_sock.connect(JETSON_CMD_ADDR)
            self._state_sock.connect(JETSON_STATE_ADDR)
            self._state_sock.setsockopt(zmq.SUBSCRIBE, b'')
            log.info('[VehicleEnv] ZeroMQ connected')
        except ImportError:
            log.warning('[VehicleEnv] zmq not installed — running in stub mode')
        except Exception as e:
            log.warning(f'[VehicleEnv] ZeroMQ connect failed: {e} — stub mode')

    def get_frame(self) -> np.ndarray:
        """
        Vision runs on the Jetson's own GPU, not here — always returns a
        blank placeholder. Use get_processed_state() for the Jetson's
        already-computed detections instead.
        """
        return np.zeros((480, 640, 3), dtype=np.uint8)

    def get_processed_state(self) -> dict:
        """
        Poll the Jetson's latest processed state over ZeroMQ:
          {"detections": [...], "speed": float, "heading": float,
           "battery": float, "waypoint_id": str}
        This is what the overseer should read for this env — not raw pixels.
        """
        if self._state_sock is None:
            return self._last_state
        try:
            if self._state_sock.poll(timeout=50):  # 50ms timeout
                self._last_state = self._state_sock.recv_json()
        except Exception as e:
            log.debug(f'[VehicleEnv] state recv error: {e}')
        return self._last_state

    def send_action(self, action: dict):
        """
        Send a high-level directive to the Jetson — it owns throttle/
        steering internally (ONNX policy + VESC), so this is NOT raw
        control. Expected shape:
          {"directive": "navigate_waypoint", "target": "apriltag_3"}
          {"directive": "emergency_stop"}
        """
        if self._cmd_sock is None:
            log.debug(f'[VehicleEnv] stub directive: {action}')
            return
        try:
            self._cmd_sock.send_json(action)
        except Exception as e:
            log.debug(f'[VehicleEnv] directive send error: {e}')

    def get_telemetry(self) -> dict:
        """Speed/heading/battery, as last reported in the Jetson's processed state."""
        state = self.get_processed_state()
        return {
            'speed':   state.get('speed'),
            'heading': state.get('heading'),
            'battery': state.get('battery'),
        }

    def get_env_name(self) -> str:
        return 'vehicle'

    def on_episode_end(self, reason: str):
        log.info(f'[VehicleEnv] episode ended: {reason}')
        self.send_action({'directive': 'emergency_stop'})
