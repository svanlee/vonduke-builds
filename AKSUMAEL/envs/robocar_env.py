"""
AK-01 Robocar environment — ROS2 Nav2 rover on a Raspberry Pi 4
(192.168.0.104), mecanum chassis.

Comms: ROS2 topics via rclpy, gated on ROS2_ENABLED=1.

Compute split: the Pi 4 has no GPU and can't run YOLO onboard — unlike
envs/vehicle_env.py's Jetson (own GPU, sends back processed detections),
raw frames from /camera/image_raw DO travel to Victus here so its YOLO
can process them.

Topics:
  /cmd_vel          (geometry_msgs/Twist)   — publish, direct velocity commands
  /goal_pose        (geometry_msgs/PoseStamped) — publish, Nav2 goals
  /camera/image_raw (sensor_msgs/Image)     — subscribe, raw frames
  /odom             (nav_msgs/Odometry)     — subscribe, position/speed/heading
  /scan             (sensor_msgs/LaserScan) — subscribe, lidar

Set ROS2_ENABLED=1 to activate.
"""
import logging
import math
import os
import numpy as np
from envs.base_env import BaseEnvironment

log = logging.getLogger(__name__)
ROS2_ENABLED = os.environ.get('ROS2_ENABLED', '0') == '1'


class RobocarEnv(BaseEnvironment):
    """
    Stub implementation — degrades gracefully to blank frames / empty
    telemetry when rclpy isn't installed or ROS2_ENABLED isn't set.
    """

    def __init__(self):
        self._rclpy = None
        self._node = None
        self._pub_cmd_vel = None
        self._pub_nav_goal = None
        self._last_frame = None
        self._last_odom = {}
        self._last_scan = None
        if not ROS2_ENABLED:
            log.info('[RobocarEnv] disabled (set ROS2_ENABLED=1 to enable)')
            return
        try:
            import rclpy
            from geometry_msgs.msg import Twist, PoseStamped
            from sensor_msgs.msg import Image, LaserScan
            from nav_msgs.msg import Odometry
            rclpy.init(args=None)
            self._rclpy = rclpy
            self._node = rclpy.create_node('aksumael_robocar_env')
            self._pub_cmd_vel  = self._node.create_publisher(Twist, '/cmd_vel', 10)
            self._pub_nav_goal = self._node.create_publisher(PoseStamped, '/goal_pose', 10)
            self._node.create_subscription(Image, '/camera/image_raw', self._on_image, 5)
            self._node.create_subscription(Odometry, '/odom', self._on_odom, 10)
            self._node.create_subscription(LaserScan, '/scan', self._on_scan, 10)
            log.info('[RobocarEnv] node started, subscribed to /camera/image_raw, /odom, /scan')
        except ImportError:
            log.warning('[RobocarEnv] rclpy/ROS2 messages not found — stub mode')
            self._node = None
        except Exception as e:
            log.warning(f'[RobocarEnv] init failed: {e} — stub mode')
            self._node = None

    def _spin(self):
        if self._node is not None:
            self._rclpy.spin_once(self._node, timeout_sec=0)

    def _on_image(self, msg):
        try:
            # Manual decode to avoid a hard cv_bridge dependency — assumes
            # bgr8/rgb8 (Pi camera default); mono8 is broadcast to 3 channels.
            channels = 1 if msg.encoding == 'mono8' else 3
            frame = np.frombuffer(msg.data, dtype=np.uint8).reshape(
                msg.height, msg.width, channels)
            self._last_frame = frame if channels == 3 else np.repeat(frame, 3, axis=2)
        except Exception as e:
            log.debug(f'[RobocarEnv] image decode error: {e}')

    def _on_odom(self, msg):
        try:
            p = msg.pose.pose.position
            v = msg.twist.twist.linear
            q = msg.pose.pose.orientation
            yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                              1.0 - 2.0 * (q.y * q.y + q.z * q.z))
            self._last_odom = {
                'x': p.x, 'y': p.y,
                'speed': math.hypot(v.x, v.y),
                'heading': yaw,
            }
        except Exception as e:
            log.debug(f'[RobocarEnv] odom decode error: {e}')

    def _on_scan(self, msg):
        self._last_scan = msg

    def get_frame(self) -> np.ndarray:
        """Pi 4 has no GPU — pull the raw frame here so Victus YOLO can run on it."""
        self._spin()
        if self._last_frame is not None:
            return self._last_frame
        return np.zeros((480, 640, 3), dtype=np.uint8)

    def send_action(self, action: dict):
        """
        Publish a direct velocity command to /cmd_vel.
        action dict expected keys:
          linear_x, linear_y: float m/s (mecanum strafes on y too)
          angular_z: float rad/s
        """
        self._spin()
        if self._pub_cmd_vel is None:
            log.debug(f'[RobocarEnv] stub cmd_vel: {action}')
            return
        try:
            from geometry_msgs.msg import Twist
            msg = Twist()
            msg.linear.x  = float(action.get('linear_x', 0.0))
            msg.linear.y  = float(action.get('linear_y', 0.0))
            msg.angular.z = float(action.get('angular_z', 0.0))
            self._pub_cmd_vel.publish(msg)
        except Exception as e:
            log.debug(f'[RobocarEnv] cmd_vel publish error: {e}')

    def send_nav_goal(self, x: float, y: float, yaw: float = 0.0, frame_id: str = 'map'):
        """Publish a Nav2 goal pose to /goal_pose."""
        self._spin()
        if self._pub_nav_goal is None:
            log.debug(f'[RobocarEnv] stub nav goal: x={x} y={y} yaw={yaw}')
            return
        try:
            from geometry_msgs.msg import PoseStamped
            msg = PoseStamped()
            msg.header.frame_id = frame_id
            msg.header.stamp = self._node.get_clock().now().to_msg()
            msg.pose.position.x = x
            msg.pose.position.y = y
            msg.pose.orientation.z = math.sin(yaw / 2.0)
            msg.pose.orientation.w = math.cos(yaw / 2.0)
            self._pub_nav_goal.publish(msg)
        except Exception as e:
            log.debug(f'[RobocarEnv] nav goal publish error: {e}')

    def get_telemetry(self) -> dict:
        """Speed/heading from /odom, lidar range min from /scan."""
        self._spin()
        scan_min = None
        if self._last_scan is not None:
            ranges = [r for r in self._last_scan.ranges if r > 0.0 and math.isfinite(r)]
            scan_min = min(ranges) if ranges else None
        return {
            'speed':           self._last_odom.get('speed'),
            'heading':         self._last_odom.get('heading'),
            'lidar_range_min': scan_min,
        }

    def get_env_name(self) -> str:
        return 'robocar'

    def on_episode_end(self, reason: str):
        log.info(f'[RobocarEnv] episode ended: {reason}')
        self.send_action({'linear_x': 0.0, 'linear_y': 0.0, 'angular_z': 0.0})
