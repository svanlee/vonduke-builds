#!/usr/bin/env python3
"""
robocar/nodes/gesture_receiver.py — UDP gesture receiver for RoboCar.

Listens for gesture commands from gesture/dispatcher.py and publishes them
as Twist messages on /robocar_01/cmd_vel.

Runs as a ROS 2 node. Start with:
    ros2 run robocar_bringup gesture_receiver

Or standalone (no ROS):
    python3 gesture_receiver.py --no-ros

UDP format: plain ASCII "COMMAND\\n" (e.g. "FORWARD\\n")
Default port: 7700
"""

import socket
import sys
import threading

LISTEN_PORT = 7700

# Drive parameters (tune per platform)
LINEAR_SPEED  = 0.3   # m/s forward
ANGULAR_SPEED = 0.5   # rad/s turn

COMMAND_MAP = {
    "STOP":       (0.0, 0.0),
    "FORWARD":    (LINEAR_SPEED, 0.0),
    "HOLD":       (0.0, 0.0),
    "TURN_LEFT":  (0.0,  ANGULAR_SPEED),
    "TURN_RIGHT": (0.0, -ANGULAR_SPEED),
}


def run_ros(port: int):
    """ROS 2 mode — publishes Twist on /robocar_01/cmd_vel."""
    import rclpy
    from rclpy.node import Node
    from geometry_msgs.msg import Twist

    class GestureReceiverNode(Node):
        def __init__(self):
            super().__init__('gesture_receiver')
            self._pub = self.create_publisher(Twist, '/robocar_01/cmd_vel', 10)
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(('', port))
            self._sock.settimeout(0.1)
            self._timer = self.create_timer(0.02, self._poll)  # 50 Hz
            self.get_logger().info(f'Gesture receiver listening on UDP:{port}')

        def _poll(self):
            try:
                data, addr = self._sock.recvfrom(64)
                cmd = data.decode().strip()
                self._handle(cmd, addr)
            except socket.timeout:
                pass
            except Exception as e:
                self.get_logger().warn(f'UDP recv error: {e}')

        def _handle(self, cmd: str, addr):
            speeds = COMMAND_MAP.get(cmd)
            if speeds is None:
                self.get_logger().warn(f'Unknown gesture command: {cmd!r}')
                return
            linear, angular = speeds
            twist = Twist()
            twist.linear.x = linear
            twist.angular.z = angular
            self._pub.publish(twist)
            self.get_logger().info(f'{cmd} from {addr[0]} → lin={linear} ang={angular}')

    rclpy.init()
    node = GestureReceiverNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


def run_standalone(port: int):
    """Standalone mode — prints commands (no ROS dependency)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', port))
    print(f'[GESTURE_RX] standalone, listening on UDP:{port}')
    print('[GESTURE_RX] (no ROS — commands printed only)')
    while True:
        try:
            data, addr = sock.recvfrom(64)
            cmd = data.decode().strip()
            speeds = COMMAND_MAP.get(cmd, None)
            if speeds:
                print(f'[GESTURE_RX] {cmd} from {addr[0]} → {speeds}')
            else:
                print(f'[GESTURE_RX] unknown: {cmd!r}')
        except KeyboardInterrupt:
            print('\n[GESTURE_RX] exit')
            break
    sock.close()


if __name__ == '__main__':
    no_ros = '--no-ros' in sys.argv
    port_arg = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == '--port'), None)
    port = int(port_arg) if port_arg else LISTEN_PORT

    if no_ros:
        run_standalone(port)
    else:
        try:
            run_ros(port)
        except ImportError:
            print('[GESTURE_RX] ROS 2 not available, running standalone')
            run_standalone(port)
