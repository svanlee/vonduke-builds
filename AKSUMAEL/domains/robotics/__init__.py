"""
domains.robotics — Robotics domain for AKSUMAEL / Jarvis.

Loaded when robotics hardware is detected (serial port /dev/ttyUSB0 or
/dev/ttyACM0, or a live ROS 2 daemon). Does NOT load in Jarvis base mode
or gaming mode.

Public API:
    from domains.robotics import load_domain
    ctx = load_domain()   # returns RoboticsContext or None if hw unavailable

Sub-packages:
    behaviors/  — high-level task behaviors (monitor, navigation, manipulation)
    sensors/    — sensor abstraction layer (IMU, odometry, range)
    vision/     — robotics-specific CV (ArUco markers, depth, lane detection)

All ROS 2 topics use the /robocar_01/ namespace (per CLAUDE.md).
AgenticROS integration: BLOCKED until hardware ships to new location.
"""
from __future__ import annotations
import os
import logging

log = logging.getLogger(__name__)


def hardware_available() -> bool:
    """True if robotics hardware (serial or ROS 2) is detectable."""
    for dev in ('/dev/ttyUSB0', '/dev/ttyACM0'):
        if os.path.exists(dev):
            return True
    try:
        import subprocess
        r = subprocess.run(['ros2', 'daemon', 'status'], capture_output=True, timeout=1)
        return r.returncode == 0
    except Exception:
        return False


def load_domain():
    """Attempt to initialise the robotics domain.

    Returns a RoboticsContext on success, None if hardware is absent.
    """
    if not hardware_available():
        log.debug('[DOMAIN] robotics hardware not found — skipping domain load')
        return None

    from domains.robotics.sensors.sensor_interface import SensorInterface
    from domains.robotics.behaviors.monitor        import RoboticsMonitor
    from domains.robotics.behaviors.agentros_hook  import AgenticROSHook

    ctx = _RoboticsContext(
        sensors = SensorInterface(),
        monitor = RoboticsMonitor(),
        ros     = AgenticROSHook(),
    )
    log.info('[DOMAIN] robotics domain loaded')
    return ctx


class _RoboticsContext:
    """Holds all robotics domain objects. Passed to runtime on load."""
    __slots__ = ('sensors', 'monitor', 'ros')

    def __init__(self, sensors, monitor, ros):
        self.sensors = sensors
        self.monitor = monitor
        self.ros     = ros
