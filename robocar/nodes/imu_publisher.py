#!/usr/bin/env python3
"""
RoboCar IMU Publisher — GY-521 (MPU-6050) → /robocar_01/imu/data_raw

Reads raw accelerometer + gyroscope data from the MPU-6050 over I2C
(shared bus with BE-880 GPS/compass and Yahboom encoder motor driver)
and publishes sensor_msgs/Imu at 50 Hz.

Hardware:
    GY-521 breakout (MPU-6050), I2C address 0x68
    Raspberry Pi 4 — SDA Pin 3, SCL Pin 5 (bus 1)
    Verify bus with:  i2cdetect -y 1

Frame conventions (REP 103 / REP 105):
    frame_id: imu_link
    Orientation is NOT published here (data_raw = accel + gyro only).
    A downstream filter (robot_localization EKF via config/ekf.yaml)
    fuses this with wheel odometry for odom → base_link.

Topic:
    /robocar_01/imu/data_raw   (sensor_msgs/Imu)

Usage:
    ros2 run robocar imu_publisher
    ros2 topic hz /robocar_01/imu/data_raw     # expect ~50 Hz
    ros2 topic echo /robocar_01/imu/data_raw --once
"""

import math

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Imu

from smbus2 import SMBus

# ── MPU-6050 register map ────────────────────────────────────────────
MPU_ADDR         = 0x68
REG_PWR_MGMT_1   = 0x6B
REG_SMPLRT_DIV   = 0x19
REG_CONFIG       = 0x1A
REG_GYRO_CONFIG  = 0x1B
REG_ACCEL_CONFIG = 0x1C
REG_ACCEL_XOUT_H = 0x3B   # 14 contiguous bytes: accel(6) temp(2) gyro(6)

# ── Scale factors (default full-scale ranges) ────────────────────────
ACCEL_FS_G       = 2.0            # ±2 g
GYRO_FS_DPS      = 250.0          # ±250 °/s
ACCEL_LSB_PER_G  = 16384.0        # datasheet, AFS_SEL=0
GYRO_LSB_PER_DPS = 131.0          # datasheet, FS_SEL=0
G_TO_MS2         = 9.80665
DEG_TO_RAD       = math.pi / 180.0

PUBLISH_RATE_HZ  = 50.0
GYRO_CAL_SAMPLES = 200            # stationary samples for bias estimate


class ImuPublisher(Node):
    def __init__(self):
        super().__init__('imu_publisher', namespace='robocar_01')

        self.declare_parameter('i2c_bus', 1)
        self.declare_parameter('frame_id', 'imu_link')

        bus_num = self.get_parameter('i2c_bus').value
        self.frame_id = self.get_parameter('frame_id').value

        self.bus = SMBus(bus_num)
        self._init_mpu()

        self.gyro_bias = self._calibrate_gyro()
        self.get_logger().info(
            f'Gyro bias (rad/s): '
            f'x={self.gyro_bias[0]:+.5f} '
            f'y={self.gyro_bias[1]:+.5f} '
            f'z={self.gyro_bias[2]:+.5f}'
        )

        self.pub = self.create_publisher(Imu, 'imu/data_raw', 10)
        self.timer = self.create_timer(1.0 / PUBLISH_RATE_HZ, self._tick)
        self.get_logger().info(
            f'Publishing /robocar_01/imu/data_raw at {PUBLISH_RATE_HZ:.0f} Hz '
            f'(MPU-6050 @ 0x{MPU_ADDR:02X}, bus {bus_num})'
        )

    # ── Hardware setup ────────────────────────────────────────────────
    def _init_mpu(self):
        # Wake from sleep, clock source = X-gyro PLL (more stable than 8 MHz osc)
        self.bus.write_byte_data(MPU_ADDR, REG_PWR_MGMT_1, 0x01)
        # Sample rate = 1 kHz / (1 + 19) = 50 Hz
        self.bus.write_byte_data(MPU_ADDR, REG_SMPLRT_DIV, 19)
        # DLPF = 3 → accel 44 Hz / gyro 42 Hz bandwidth (kills motor vibration)
        self.bus.write_byte_data(MPU_ADDR, REG_CONFIG, 0x03)
        # Full-scale: gyro ±250 °/s, accel ±2 g
        self.bus.write_byte_data(MPU_ADDR, REG_GYRO_CONFIG, 0x00)
        self.bus.write_byte_data(MPU_ADDR, REG_ACCEL_CONFIG, 0x00)

    def _read_word(self, data, idx):
        val = (data[idx] << 8) | data[idx + 1]
        return val - 65536 if val >= 32768 else val

    def _read_raw(self):
        """Single burst read: ax ay az | temp | gx gy gz (SI units)."""
        d = self.bus.read_i2c_block_data(MPU_ADDR, REG_ACCEL_XOUT_H, 14)
        ax = self._read_word(d, 0)  / ACCEL_LSB_PER_G * G_TO_MS2
        ay = self._read_word(d, 2)  / ACCEL_LSB_PER_G * G_TO_MS2
        az = self._read_word(d, 4)  / ACCEL_LSB_PER_G * G_TO_MS2
        gx = self._read_word(d, 8)  / GYRO_LSB_PER_DPS * DEG_TO_RAD
        gy = self._read_word(d, 10) / GYRO_LSB_PER_DPS * DEG_TO_RAD
        gz = self._read_word(d, 12) / GYRO_LSB_PER_DPS * DEG_TO_RAD
        return (ax, ay, az), (gx, gy, gz)

    def _calibrate_gyro(self):
        """Robot must be stationary at boot. Averages gyro to estimate bias."""
        self.get_logger().info(
            f'Calibrating gyro bias — keep robot still ({GYRO_CAL_SAMPLES} samples)…'
        )
        sx = sy = sz = 0.0
        for _ in range(GYRO_CAL_SAMPLES):
            _, (gx, gy, gz) = self._read_raw()
            sx += gx; sy += gy; sz += gz
        n = float(GYRO_CAL_SAMPLES)
        return (sx / n, sy / n, sz / n)

    # ── Publish loop ──────────────────────────────────────────────────
    def _tick(self):
        try:
            (ax, ay, az), (gx, gy, gz) = self._read_raw()
        except OSError as e:
            self.get_logger().warn(f'I2C read failed: {e}', throttle_duration_sec=5.0)
            return

        msg = Imu()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.frame_id

        msg.linear_acceleration.x = ax
        msg.linear_acceleration.y = ay
        msg.linear_acceleration.z = az

        msg.angular_velocity.x = gx - self.gyro_bias[0]
        msg.angular_velocity.y = gy - self.gyro_bias[1]
        msg.angular_velocity.z = gz - self.gyro_bias[2]

        # data_raw carries no orientation estimate — mark invalid per REP 145
        msg.orientation_covariance[0] = -1.0

        # Conservative static covariances (tune after logging real noise floor)
        msg.linear_acceleration_covariance[0] = 0.04   # (m/s²)²
        msg.linear_acceleration_covariance[4] = 0.04
        msg.linear_acceleration_covariance[8] = 0.04
        msg.angular_velocity_covariance[0] = 0.0025    # (rad/s)²
        msg.angular_velocity_covariance[4] = 0.0025
        msg.angular_velocity_covariance[8] = 0.0025

        self.pub.publish(msg)

    def destroy_node(self):
        try:
            self.bus.close()
        finally:
            super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = ImuPublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
