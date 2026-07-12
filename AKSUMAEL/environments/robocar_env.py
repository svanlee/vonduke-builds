# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.2.0 — Robocar Environment Adapter        ║
# ╚══════════════════════════════════════════════════════╝
#
# Backed by the ROS 2 Jazzy workspace at ~/robocar_ws (package "robocar":
# mecanum chassis, Delta-2A lidar, BE-880 GPS, OV5647 camera, nav2). As of
# this writing that package's console_scripts (motor_node, camera_node,
# brain_node, ...) are declared in setup.py but not yet implemented —
# robocar/robocar/ only has an empty __init__.py — so there is no existing
# "brain" node to wrap. This adapter fills that role directly: it is itself
# a lightweight rclpy node that subscribes to the camera topic and publishes
# Twist commands to cmd_vel, the same topic nav2's collision_monitor chain
# outputs to (see config/nav2_params.yaml: cmd_vel_out_topic: "cmd_vel").
#
# rclpy lives under /opt/ros/jazzy (sourced via setup.bash), not in this
# project's venv — if it isn't importable, the adapter marks itself
# unavailable with a clear message instead of crashing so the rest of
# AKSUMAEL keeps working.

import os
import time

import config
from core.environment import EnvironmentAdapter, Observation, LowRewardStuckTracker

try:
    import numpy as np
except ImportError:
    np = None

try:
    from vision.yolo import YOLODetector
except Exception:
    YOLODetector = None

CAMERA_TOPIC = getattr(config, 'ROBOCAR_CAMERA_TOPIC', '/camera/image_raw')
CMD_VEL_TOPIC = getattr(config, 'ROBOCAR_CMD_VEL_TOPIC', '/cmd_vel')
LINEAR_SPEED = getattr(config, 'ROBOCAR_LINEAR_SPEED', 0.2)   # m/s
ANGULAR_SPEED = getattr(config, 'ROBOCAR_ANGULAR_SPEED', 0.6)  # rad/s


class RobocarEnv(EnvironmentAdapter):
    ENV_NAME = "robocar"

    ACTION_SPACE = ('forward', 'backward', 'turn_left', 'turn_right', 'stop')

    OBJECT_CLASSES = (
        'lane_left', 'lane_right', 'obstacle', 'stop_sign',
        'person', 'traffic_cone', 'open_path',
    )

    GOALS = ('follow_lane', 'avoid_obstacle', 'stop_at_sign', 'reach_waypoint')

    _WEIGHTS_PATH = 'data/models/aksumael_robocar.pt'

    def __init__(self):
        super().__init__()
        self._stuck = LowRewardStuckTracker(
            low_thresh=getattr(config, 'ROBOCAR_LOW_REWARD_THRESH', 0.05),
            stuck_ticks=getattr(config, 'ROBOCAR_STUCK_TICKS', 150),
        )
        self._estopped = False
        self._latest_ros_frame = None
        self._node = None
        self._pub = None
        self._sub = None
        self._we_initialized_rclpy = False
        self.yolo = None
        self._rclpy = None

        self._init_ros()
        if self.available:
            self._init_yolo()

    # ── Setup ────────────────────────────────────────────────────
    def _init_ros(self):
        try:
            import rclpy
            from rclpy.node import Node
            from geometry_msgs.msg import Twist
            from sensor_msgs.msg import Image
        except ImportError as e:
            self._mark_unavailable(
                f'rclpy/ROS2 messages not importable ({e}). Robocar needs '
                f'ROS2 Jazzy sourced (`source /opt/ros/jazzy/setup.bash`) — '
                f'this project venv does not include rclpy by design.'
            )
            return

        self._Twist = Twist

        try:
            if not rclpy.ok():
                rclpy.init(args=None)
                self._we_initialized_rclpy = True
            self._rclpy = rclpy

            node = Node('aksumael_robocar_bridge')
            self._pub = node.create_publisher(Twist, CMD_VEL_TOPIC, 10)
            self._sub = node.create_subscription(
                Image, CAMERA_TOPIC, self._on_image, 10,
            )
            self._node = node
            print(f'[{self.ENV_NAME}] ROS bridge up — sub:{CAMERA_TOPIC} pub:{CMD_VEL_TOPIC}')
        except Exception as e:
            self._mark_unavailable(f'ROS2 node/topic init failed: {e}')

    def _init_yolo(self):
        if YOLODetector is None:
            print(f'[{self.ENV_NAME}] YOLODetector import failed — running without vision')
            return
        try:
            self.yolo = YOLODetector()
            if os.path.exists(self._WEIGHTS_PATH):
                self.yolo.reload_weights(self._WEIGHTS_PATH)
            else:
                print(f'[{self.ENV_NAME}] no robocar-specific YOLO weights at '
                      f'{self._WEIGHTS_PATH} — using default model '
                      f'({config.YOLO_MODEL}); lane/obstacle detections will '
                      f'be unreliable until a robocar dataset is trained.')
        except Exception as e:
            print(f'[{self.ENV_NAME}] YOLO init failed: {e} — running without vision')
            self.yolo = None

    def _on_image(self, msg):
        if np is None:
            return
        try:
            dtype = np.uint16 if '16' in msg.encoding else np.uint8
            arr = np.frombuffer(bytes(msg.data), dtype=dtype)
            channels = 1 if msg.encoding in ('mono8', 'mono16') else 3
            frame = arr.reshape(msg.height, msg.width, channels)
            if msg.encoding == 'rgb8':
                frame = frame[:, :, ::-1]  # RGB -> BGR, matches cv2/YOLO convention
            self._latest_ros_frame = frame
        except Exception as e:
            print(f'[{self.ENV_NAME}] image decode error: {e}')

    def _spin(self):
        if self._node is not None:
            self._rclpy.spin_once(self._node, timeout_sec=0.0)

    # ── EnvironmentAdapter ──────────────────────────────────────────
    def observe(self, frame) -> Observation:
        if not self.available:
            return Observation(alive=not self._estopped)

        self._spin()
        use_frame = frame if frame is not None else self._latest_ros_frame

        objects = []
        if self.yolo is not None and self.yolo.model is not None and use_frame is not None:
            objects = self.yolo.detect(use_frame)

        frame_width = use_frame.shape[1] if use_frame is not None and hasattr(use_frame, 'shape') else None
        hud = self._lane_state(objects, frame_width)

        return Observation(
            objects=objects,
            hud=hud,
            position=None,  # odom/AMCL pose available via /odom or /amcl_pose if wired up later
            alive=not self._estopped,
            raw_frame=use_frame,
        )

    def execute(self, action: dict) -> None:
        if not self.available:
            return
        name = action.get('action') if action else None
        twist = self._Twist()
        if name == 'forward':
            twist.linear.x = LINEAR_SPEED
        elif name == 'backward':
            twist.linear.x = -LINEAR_SPEED
        elif name == 'turn_left':
            twist.angular.z = ANGULAR_SPEED
        elif name == 'turn_right':
            twist.angular.z = -ANGULAR_SPEED
        # 'stop' (or anything unrecognized) leaves twist zeroed — safe default
        self._pub.publish(twist)

    def reward(self, observation: Observation, action: dict) -> float:
        if not self.available:
            return 0.0

        r = 0.0
        hud = observation.hud or {}
        labels = [o.get('label') for o in (observation.objects or [])]
        name = action.get('action') if action else None

        if name == 'forward':
            r += 0.2  # forward progress

        offset = hud.get('lane_offset')
        if offset is not None:
            r += 0.3 * (1.0 - min(abs(offset), 1.0))
        elif 'lane_left' not in labels and 'lane_right' not in labels \
                and 'open_path' not in labels:
            r -= 0.2  # no lane/path markers at all

        blocking = {'obstacle', 'person', 'traffic_cone', 'stop_sign'} & set(labels)
        if blocking:
            if name == 'stop':
                r += 0.1
            else:
                r -= 0.3

        r = round(r, 3)
        self._stuck.update(r)
        return r

    def reset(self) -> None:
        if self.available:
            self.execute({'action': 'stop'})
        self._estopped = False
        self._stuck.reset()

    def is_alive(self) -> bool:
        return not self._estopped

    def estop(self) -> None:
        """Emergency stop — publishes zero Twist and marks the episode dead."""
        if self.available:
            self.execute({'action': 'stop'})
        self._estopped = True

    def is_stuck(self) -> bool:
        return self._stuck.is_stuck()

    def close(self) -> None:
        if not self.available:
            return
        try:
            self.execute({'action': 'stop'})
            if self._node is not None:
                self._node.destroy_node()
            if self._we_initialized_rclpy and self._rclpy is not None and self._rclpy.ok():
                self._rclpy.shutdown()
        except Exception as e:
            print(f'[{self.ENV_NAME}] shutdown error: {e}')

    # ── Internals ────────────────────────────────────────────────
    @staticmethod
    def _lane_state(objects: list, frame_width) -> dict:
        left = next((o for o in objects if o.get('label') == 'lane_left'), None)
        right = next((o for o in objects if o.get('label') == 'lane_right'), None)
        state = {'ts': time.time()}
        if left and right and frame_width:
            lx = (left['box'][0] + left['box'][2]) / 2
            rx = (right['box'][0] + right['box'][2]) / 2
            lane_center = (lx + rx) / 2
            frame_center = frame_width / 2
            lane_width = max(rx - lx, 1)
            state['lane_offset'] = (frame_center - lane_center) / (lane_width / 2)
        state['lane_left_visible'] = left is not None
        state['lane_right_visible'] = right is not None
        return state
