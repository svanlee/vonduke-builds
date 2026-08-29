"""
domains.robotics.sensors.sensor_interface
Unified sensor interface for the robotics domain.

Abstracts over:
  - IMU (MPU-6050 via I2C or ROS 2 /robocar_01/imu topic)
  - Ultrasonic distance sensors (HC-SR04 via GPIO or /robocar_01/range)
  - Camera (reuses core VideoCapturePipeline; no duplication)
  - Encoder odometry (/robocar_01/odom)

All reads are non-blocking. Each sensor returns the last known value when
a live read is not available.

Status: STUB — hardware packed (CA relocation).
"""
from __future__ import annotations
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class IMUData:
    ax: float = 0.0; ay: float = 0.0; az: float = 0.0
    gx: float = 0.0; gy: float = 0.0; gz: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class OdomData:
    x: float = 0.0; y: float = 0.0; yaw: float = 0.0
    vx: float = 0.0; vyaw: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class RangeData:
    distance_m: float = float('inf')
    timestamp: float = field(default_factory=time.time)


class SensorInterface:
    """Read-only facade over all robotics sensors.

    Instantiate once in the robotics domain loader and pass to behaviors
    that need sensor data.
    """

    def __init__(self):
        self._imu:   IMUData   = IMUData()
        self._odom:  OdomData  = OdomData()
        self._range: RangeData = RangeData()
        log.info('[ROBOTICS] SensorInterface initialised (stub mode)')

    # ── public API ──────────────────────────────────────────────────────

    def imu(self) -> IMUData:
        """Latest IMU reading."""
        return self._imu

    def odom(self) -> OdomData:
        """Latest odometry reading."""
        return self._odom

    def range(self) -> RangeData:
        """Latest ultrasonic range reading."""
        return self._range

    # ── update hooks (called by ROS subscriber callbacks or serial parser) ──

    def update_imu(self, ax, ay, az, gx, gy, gz):
        self._imu = IMUData(ax=ax, ay=ay, az=az, gx=gx, gy=gy, gz=gz)

    def update_odom(self, x, y, yaw, vx=0.0, vyaw=0.0):
        self._odom = OdomData(x=x, y=y, yaw=yaw, vx=vx, vyaw=vyaw)

    def update_range(self, distance_m: float):
        self._range = RangeData(distance_m=distance_m)
