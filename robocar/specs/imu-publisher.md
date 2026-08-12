# Spec: IMU Publisher (GY-521 → /robocar_01/imu/data)

**Status:** ready to build
**First target for /build + /review loop**

---

## Objective

Publish IMU data from the GY-521 (MPU-6050) sensor to ROS 2 so the EKF
node has a clean, namespaced, REP-145-compliant input to fuse with wheel odometry.

---

## Requirements

1. Node publishes `sensor_msgs/Imu` on `/robocar_01/imu/data`.
2. Topic publishes at 100 Hz ± 5 Hz under normal CPU load.
3. `header.frame_id` is `"imu_link"`.
4. `header.stamp` is the time the sample was read from the sensor, not
   the time it was published (use `self.get_clock().now()` immediately
   after the I2C read, before any processing).
5. `linear_acceleration` is populated in m/s². Raw register values
   divided by the sensitivity scale factor for ±2g range (16384 LSB/g).
6. `angular_velocity` is populated in rad/s. Raw register values
   divided by the sensitivity scale factor for ±250°/s range (131 LSB/°/s).
7. `orientation` is `(0, 0, 0, 1)` and `orientation_covariance[0]` is `-1`
   (signalling "orientation not estimated" per REP 145).
8. `linear_acceleration_covariance` and `angular_velocity_covariance` are
   populated with diagonal values — use datasheet noise figures or measured
   values; do not leave as all-zeros.
9. Node connects to GY-521 at I2C address `0x68`. If the device is not
   found at startup, log an error and shut down cleanly (do not spin with
   repeated errors).
10. Node is launchable from `robocar_bringup/launch/sensors.launch.py` with
    the `/robocar_01/` namespace applied.

---

## Edge cases

- **I2C bus unavailable at startup:** node should log clearly and exit, not spin.
- **Bus index differs on RDK X5 vs Pi 4:** bus index must be a launch argument
  (`imu_bus`, default `1`), not hardcoded.
- **Sensor read timeout:** if a read stalls > 10 ms, log a warning, skip the
  sample, do not block the publish loop.

---

## Definition of done

- [ ] `ros2 topic hz /robocar_01/imu/data` shows ~100 Hz with no large gaps
- [ ] `ros2 topic echo /robocar_01/imu/data --once` shows:
  - `frame_id: imu_link`
  - `orientation_covariance[0]: -1.0`
  - non-zero linear_acceleration values when board is tilted
  - non-zero angular_velocity values when board is rotated
- [ ] Covariance matrices are diagonal and non-zero on the diagonal
- [ ] Node exits cleanly with error log when I2C device not present
- [ ] `imu_bus` launch arg overrides default without code change
