# DEVLOG — VonDuke Builds

Daily engineering journal. New entries go at the TOP.
Format: what I built, why it matters, where it lives.

---

<!-- New entries go at the TOP -->

### Day 1 — Fri Jul 03 2026
**[LAUNCH] — Repo initialized with four days of stacked artifacts**

What: Repo scaffolded with the full project structure, plus the RoboCar
system architecture README, the GY-521 IMU publisher node, and the
robot_localization EKF config — the first three planned RoboCar artifacts
landing together.

Why it matters: The IMU publisher (`robocar/nodes/imu_publisher.py`)
publishes `sensor_msgs/Imu` at 50 Hz to `/robocar_01/imu/data_raw` with
boot-time gyro bias calibration and DLPF filtering for motor vibration.
The EKF config (`robocar/config/ekf.yaml`) consumes that topic and fuses
it with wheel odometry for the `odom → base_link` transform — the local
half of the REP 105 two-stage localization architecture. Committing the
publisher and its consumer together documents the data path end to end.

File(s): `README.md`, `robocar/README.md`, `robocar/nodes/imu_publisher.py`,
`robocar/config/ekf.yaml`

---
