"""
ROS2 bridge — optional; gated on ROS2_ENABLED env var.
Publishes vehicle world state, subscribes to overseer decisions.
"""
import os
import logging

log = logging.getLogger(__name__)
ROS2_ENABLED = os.environ.get('ROS2_ENABLED', '0') == '1'


class ROS2Bridge:
    def __init__(self):
        if not ROS2_ENABLED:
            log.info('[ROS2Bridge] disabled (set ROS2_ENABLED=1 to enable)')
            self._node = None
            return
        try:
            import rclpy
            from std_msgs.msg import String
            rclpy.init()
            self._node = rclpy.create_node('aksumael_bridge')
            self._pub = self._node.create_publisher(String, '/vehicle/world_state', 10)
            self._sub = self._node.create_subscription(
                String, '/mesh_llm/decision', self._on_decision, 10)
            self._last_decision = None
            log.info('[ROS2Bridge] node started')
        except ImportError:
            log.warning('[ROS2Bridge] rclpy not found — bridge disabled')
            self._node = None
        except Exception as e:
            log.warning(f'[ROS2Bridge] init failed: {e}')
            self._node = None

    def _on_decision(self, msg):
        self._last_decision = msg.data

    def publish_state(self, state: dict):
        if not self._node:
            return
        try:
            import json
            from std_msgs.msg import String
            msg = String()
            msg.data = json.dumps(state)
            self._pub.publish(msg)
        except Exception as e:
            log.debug(f'[ROS2Bridge] publish error: {e}')

    def spin_once(self):
        if not self._node:
            return
        try:
            import rclpy
            rclpy.spin_once(self._node, timeout_sec=0)
        except Exception:
            pass

    def get_last_decision(self):
        return self._last_decision
