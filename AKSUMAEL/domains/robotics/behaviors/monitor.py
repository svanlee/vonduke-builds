"""
domains.robotics.behaviors.monitor
Robotics hardware monitoring behavior.

Polls connected sensors, publishes diagnostics to the goal stack,
and triggers alerts when hardware goes offline.

Loaded only when robotics hardware is detected (e.g. /dev/ttyACM0, ROS 2 bridge).
"""
import time
import logging

log = logging.getLogger(__name__)


class RoboticsMonitor:
    """Lightweight hardware health monitor for the robotics domain."""

    POLL_INTERVAL = 5.0  # seconds

    def __init__(self):
        self._last_poll = 0.0
        self._status: dict = {}

    def tick(self) -> dict:
        """Call each main loop iteration. Returns current status dict."""
        now = time.time()
        if now - self._last_poll < self.POLL_INTERVAL:
            return self._status
        self._last_poll = now

        status = {}
        status['serial'] = self._check_serial()
        status['ros']    = self._check_ros()
        status['camera'] = self._check_camera()

        self._status = status
        return status

    # ── hardware probes ────────────────────────────────────────────────

    def _check_serial(self) -> bool:
        """True if KB2040/serial HID bridge is reachable."""
        import os
        for dev in ('/dev/ttyUSB0', '/dev/ttyACM0'):
            if os.path.exists(dev):
                return True
        return False

    def _check_ros(self) -> bool:
        """True if a ROS 2 daemon is running."""
        try:
            import subprocess
            r = subprocess.run(
                ['ros2', 'daemon', 'status'],
                capture_output=True, timeout=2
            )
            return r.returncode == 0
        except Exception:
            return False

    def _check_camera(self) -> bool:
        """True if primary capture device is available."""
        import os
        return os.path.exists('/dev/video2') or os.path.exists('/dev/video0')
