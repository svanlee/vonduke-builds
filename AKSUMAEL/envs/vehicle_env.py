"""
GOAT Racer One / AKV vehicle environment stub.

Hardware: Traxxas 1/10 AWD, Jetson Orin Nano 8GB, Intel RealSense D435,
          VESC motor controller, sensored brushless, LiPo 2S 5000mAh.
Comms: ZeroMQ/Protobuf (not heavy ROS2).
Waypoints: AprilTags.

Set AKSUMAEL_ENV=vehicle to activate.
"""
import logging
import numpy as np
from envs.base_env import BaseEnvironment

log = logging.getLogger(__name__)

# ZeroMQ addresses — update to match Jetson Orin Nano network config
VESC_CMD_ADDR   = 'tcp://localhost:5555'   # send throttle/steering
VESC_TELEM_ADDR = 'tcp://localhost:5556'   # receive VESC telemetry
REALSENSE_ADDR  = 'tcp://localhost:5557'   # receive frame from RealSense


class VehicleEnv(BaseEnvironment):
    """
    Stub implementation — replace internals with live ZeroMQ connections
    once the Jetson Orin Nano is wired up.
    """

    def __init__(self):
        self._zmq_ctx = None
        self._cmd_sock = None
        self._telem_sock = None
        self._frame_sock = None
        self._last_telemetry = {}
        try:
            import zmq
            ctx = zmq.Context()
            self._zmq_ctx = ctx
            self._cmd_sock   = ctx.socket(zmq.PUSH)
            self._telem_sock = ctx.socket(zmq.SUB)
            self._frame_sock = ctx.socket(zmq.SUB)
            self._cmd_sock.connect(VESC_CMD_ADDR)
            self._telem_sock.connect(VESC_TELEM_ADDR)
            self._telem_sock.setsockopt(zmq.SUBSCRIBE, b'')
            self._frame_sock.connect(REALSENSE_ADDR)
            self._frame_sock.setsockopt(zmq.SUBSCRIBE, b'')
            log.info('[VehicleEnv] ZeroMQ connected')
        except ImportError:
            log.warning('[VehicleEnv] zmq not installed — running in stub mode')
        except Exception as e:
            log.warning(f'[VehicleEnv] ZeroMQ connect failed: {e} — stub mode')

    def get_frame(self) -> np.ndarray:
        """Get RGB frame from RealSense D435 via ZeroMQ."""
        if self._frame_sock is None:
            # Stub: return blank frame
            return np.zeros((480, 640, 3), dtype=np.uint8)
        try:
            if self._frame_sock.poll(timeout=50):  # 50ms timeout
                buf = self._frame_sock.recv()
                import cv2
                frame = cv2.imdecode(np.frombuffer(buf, np.uint8), cv2.IMREAD_COLOR)
                return frame if frame is not None else np.zeros((480, 640, 3), dtype=np.uint8)
        except Exception as e:
            log.debug(f'[VehicleEnv] frame recv error: {e}')
        return np.zeros((480, 640, 3), dtype=np.uint8)

    def send_action(self, action: dict):
        """
        Send throttle/steering to VESC.
        action dict expected keys:
          throttle: float -1.0 to 1.0
          steering: float -1.0 to 1.0 (negative=left)
          brake: bool
        """
        if self._cmd_sock is None:
            log.debug(f'[VehicleEnv] stub action: {action}')
            return
        try:
            self._cmd_sock.send_json(action)
        except Exception as e:
            log.debug(f'[VehicleEnv] action send error: {e}')

    def get_telemetry(self) -> dict:
        """Poll latest VESC telemetry: speed, battery, RPM, IMU."""
        if self._telem_sock is None:
            return self._last_telemetry
        try:
            if self._telem_sock.poll(timeout=10):
                self._last_telemetry = self._telem_sock.recv_json()
        except Exception:
            pass
        return self._last_telemetry

    def get_env_name(self) -> str:
        return 'vehicle'

    def on_episode_end(self, reason: str):
        log.info(f'[VehicleEnv] episode ended: {reason}')
        # Send zero throttle / steering on episode end
        self.send_action({'throttle': 0.0, 'steering': 0.0, 'brake': True})
