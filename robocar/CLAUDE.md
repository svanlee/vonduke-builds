# CLAUDE.md — RoboCar Agent Rules

Rules accumulate from real failures. Add a rule any time you make a mistake.
Prune weekly; past ~50 lines rules get ignored.

---

## Namespacing

- Every ROS 2 topic, service, and action is namespaced `/robocar_01/` from
  day one. This is for multi-agent/hive compatibility. No exceptions,
  including throwaway debug topics.

---

## Hardware truths

- The Yahboom STM32 motor driver is an I2C/UART device, NOT a GPIO HAT.
  Do not generate GPIO pin-toggling code for motor control.
- GY-521 IMU is on I2C address `0x68`.
- BE-880 GPS/compass shares the same I2C bus. Run `i2cdetect` after any
  rewiring — do not trust this table without checking.
- Delta-2A is a planar 2D LiDAR. Any suggestion requiring 3D point clouds
  (e.g. EllipseLIO) is blocked until a 3D unit is installed.
- On the RDK X5: use `Hobot.GPIO`, not `RPi.GPIO`. I2C bus indices differ
  from the Pi. There is no `config.txt` and no `dtoverlay` mechanism.
- The motor driver speaks a protocol over I2C/UART — not GPIO. Off-the-shelf
  differential-drive controllers that assume Pi GPIO or Arduino Firmata will
  not work without an adapter layer.

---

## Localization (REP 105)

- REP 105 two-stage is mandatory: local EKF publishes `odom → base_link`
  (continuous, allowed to drift). Global SLAM publishes `map → odom` (jumpy,
  globally anchored). Never publish both from one node.
- A static identity `map → odom` is the correct placeholder until real
  localization is running. Do not "fix" it prematurely by faking data.
- EKF via `robot_localization` (`ekf.yaml`), fusing wheel odometry + IMU.

---

## URDF

- Clean xacro composition — separate files for base, wheels, sensors, gazebo.
  Do not write a monolithic URDF.

---

## Perception / YOLO

- Use `yolo_ros` (mgonzs13), Humble branch on `main`. `yolo.launch.py` takes
  a namespace argument — always pass `/robocar_01/`.
- `Mask.msg` contains polygon points, NOT a dense bitmask.
- `Detection.id` is a string, not an int.
- `Detection.msg` has no persistent entity identity field. Publish a parallel
  mapping topic rather than overloading an existing field.

---

## AKSUMAEL perception (shared rules)

- Use `model.track(persist=True, tracker="bytetrack.yaml")`. Never
  `model.predict()` — predict() does not produce stable track IDs.
- Track IDs are the join key between perception and cognition. Do not
  renumber them downstream.

---

## Firmware (ESP32 hive nodes)

- Any tight loop touching hardware gets `vTaskDelay(1)`. No exceptions.
- Camera-equipped nodes use the FreeRTOS dual-task pattern: stream task and
  command task pinned to separate cores.
- Video transport is UDP, not TCP. Dropping frames is correct; stalling is not.
- Always build firmware in Release mode for timing-sensitive work. Debug builds
  inflated a SysTick handler from 427 cycles to 7200 (caused self-preemption
  at 1 kHz). If firmware works on the bench but starves in the field, check
  Debug vs Release before auditing logic.

---

## Commit discipline

- Commit small and often. Prefer scoped commits with real messages over dumps.
- Namespace all topics before any commit that touches publishers/subscribers.
- If you leave a task incomplete, write `HANDOFF.md` — where you stopped,
  what you tried, what you'd do next.

---

## Definition of done — key milestones

| Milestone | Done when |
|---|---|
| IMU publisher | `/robocar_01/imu/data` publishes at rate, frame_id correct, covariances populated |
| Delta-2A scan | `/robocar_01/scan` publishes valid LaserScan, angle_min/max match datasheet |
| EKF | `robot_localization` produces continuous odom→base_link, no TF jumps over 60s drive |
| First SLAM session | `slam_toolbox` produces a map that closes a known loop |
