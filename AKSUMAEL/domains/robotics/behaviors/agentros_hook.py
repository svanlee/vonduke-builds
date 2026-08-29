"""
domains.robotics.behaviors.agentros_hook
Stub hook for AgenticROS integration.

When AgenticROS (Jazzy / CycloneDDS) is reachable, this module registers
AKSUMAEL as an action server and forwards goal commands from the Jarvis
goal stack to ROS 2 action clients.

All topics follow the /robocar_01/ namespace per CLAUDE.md.

Status: STUB — hardware packed (CA relocation). Fill in when RoboCar is
available. Non-hardware integration work (message definitions, topic design)
is in scope now.
"""
import logging

log = logging.getLogger(__name__)

ROS2_NAMESPACE = '/robocar_01'


def try_connect() -> bool:
    """Attempt to connect to a running ROS 2 daemon. Returns True if live."""
    try:
        import subprocess
        r = subprocess.run(['ros2', 'daemon', 'status'], capture_output=True, timeout=2)
        return r.returncode == 0
    except Exception:
        return False


class AgenticROSHook:
    """
    Bridges the AKSUMAEL goal stack to ROS 2 action servers.

    Usage (when hardware is present):
        hook = AgenticROSHook()
        if hook.connected:
            hook.send_nav_goal(x=1.0, y=0.0)
    """

    def __init__(self):
        self.connected = try_connect()
        if self.connected:
            log.info('[ROBOTICS] AgenticROS hook connected (namespace=%s)', ROS2_NAMESPACE)
        else:
            log.info('[ROBOTICS] AgenticROS hook: ROS 2 daemon not found — stub mode')

    def send_nav_goal(self, x: float, y: float, yaw: float = 0.0):
        """Send a navigation goal to /robocar_01/navigate_to_pose."""
        if not self.connected:
            log.warning('[ROBOTICS] send_nav_goal skipped — not connected')
            return
        # TODO: implement rclpy action client when hardware is present
        raise NotImplementedError('Implement when RoboCar hardware is available')

    def stop(self):
        """Cancel any active goals."""
        if not self.connected:
            return
        raise NotImplementedError('Implement when RoboCar hardware is available')
