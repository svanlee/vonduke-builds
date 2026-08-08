# VNAV Study Map — MIT 16.485 Labs → AKSUMAEL / AK-01 Open Issues

**Source:** <https://vnav.mit.edu/labs/lab1..lab5/exercises.html> (VNAV 2024 offering)
**Written:** 2026-08-08
**Purpose:** Decide which labs to actually do, in what order, and which blocked ticket
each one unblocks. Skip anything not on this list.

---

## Quick sequencing verdict

| Do this | Before touching this |
|---|---|
| **Lab 2** (tf2 / frames / quaternions) | EKF integration, `view_frames` audit, lidar `laser` frame, GPS fusion |
| **Lab 5** (features / LK tracking) | rtabmap localization mode, AKSUMAEL visual odometry |
| **Lab 3** (SE(3) control, state→command loop) | Nav2 controller tuning, any closed-loop gain work |
| **Lab 4** (min-snap QP, bag recording) | Nav2 path smoothing; **and do the bag section first** to debug lidar starburst offline |
| **Lab 1** | Nothing. Do the C++ half only if you intend to patch `robot_localization` / rtabmap source. |

**Do Lab 2 before you touch the EKF.** Every symptom in the EKF integration ticket
(filtered odom drifting sideways, heading inverted, TF extrapolation errors) is a frame
or sign bug, and Lab 2 is the only lab that drills frame composition order.

---

## Lab 1 — Shell, git, C++ warm-up, simulator smoke test

Environment validation plus C++ fundamentals: pointers, references, integer/float
conversion, signed vs unsigned, and a `RandomVector` class implemented without `<algorithm>`.

- **Weakest tie to the stack. Do the C++ section only, and only conditionally.**
  `imu_publisher.py` is Python and Nav2 is configured in YAML, so nothing on the current
  board requires C++. The moment you need to *patch* rather than *configure* —
  `robot_localization`, rtabmap, or a Nav2 plugin — all three are C++ and you will be
  reading templated Eigen. That's the trigger to come back here.
- **Signed/unsigned and integer-truncation exercises → MPU-6050 register decoding.**
  `imu_publisher.py` reads 14 contiguous bytes at `REG_ACCEL_XOUT_H` and must sign-extend
  16-bit two's-complement words. Lab 1's `unsigned char c = -1` question is that exact bug
  class. If IMU values ever read as huge positives instead of small negatives, it's this.
- **Skip:** the git/GitHub-Enterprise submission exercises and the TESSE simulator download.
  We have no Unity simulator in this stack and no VNAV org repo.

---

## Lab 2 — tf2, rigid transforms, rViz, ROS node anatomy

Two drones on parametric trajectories; publish `world→av1`/`world→av2` transforms, look up
the relative transform `av2` in `av1`'s frame, then prove analytically that the relative
trajectory is an ellipse. Plus quaternion algebra (Ω₁/Ω₂ orthogonality) and intrinsic vs
extrinsic rotation composition.

- **AK-01 EKF integration (`config/ekf.yaml`) — this is the prerequisite lab.**
  `imu0_config` fuses `vyaw` and `ax` only, and both are interpreted in `base_link`, not
  `imu_link`. If the GY-521 is mounted rotated (very likely — it's a breakout on a milled
  chassis), the EKF silently fuses the wrong axis and the filter drifts in a way that looks
  like bad process noise but isn't. Deliverable 6 (intrinsic vs extrinsic, pre- vs
  post-multiply) is exactly the reasoning that tells you which way the static
  `base_link→imu_link` transform has to go.
- **AK-01 `view_frames` audit / REP-105 tree — Deliverable 3 is the drill.**
  Looking up `dest_frame` relative to `ref_frame` and getting the argument order backwards
  is called out explicitly in the handout ("try swapping `ref_frame` and `dest_frame`").
  That is the single most common cause of a map that builds mirrored. Do this before the
  wheel-slip and kidnap tests in the `ekf.yaml` validation sequence.
- **AK-01 lidar starburst — covers one of the two candidate causes.**
  A starburst pattern is either (a) the driver emitting wrong angle-per-sample / a stale
  intensity threshold, or (b) scan points being transformed with a mismatched timestamp
  while the base is rotating, which smears a straight wall into radial spokes. Lab 2's
  timed `TransformStamped` publishing at 50 Hz and the tf lookup semantics are how you
  distinguish the two. **Actionable test:** hold the robot still and re-scan. If the
  starburst vanishes when stationary, it's (b) and it's a tf/timestamp problem, not the
  Delta-2A.
- **AK-01 BE-880 GPS — Lab 2 teaches the architecture the fix lands in.**
  Fixing the baud rate only gets you a fix message. Using it means a *second* EKF instance
  in the `map` frame plus `navsat_transform_node`, which is precisely the two-stage
  `map→odom→base_link` split the handout drills ("global localizer is jumpy, local filter
  is smooth and drifts"). Don't fuse GPS into the existing `odom`-frame filter — that
  breaks the continuity guarantee `ekf.yaml` already documents.
- **AKSUMAEL spatial self-awareness without F3 — this is the formal replacement.**
  F3 gave the bot absolute XYZ for free. Without it, "where am I" has to become "what is
  the transform from my start frame to my body frame," which is Deliverable 3 verbatim.
  The two-drone setup is also the right mental model for the hive roadmap: `av1`/`av2`
  is `robocar_01`/`robocar_02`.
- **Platform: CycloneDDS smoke test.** Deliverable 1 (`ros2 node list`, `rqt_graph`,
  reproducing a launch file as individual `ros2 run` calls) is the fastest way to catch a
  misconfigured `RMW_IMPLEMENTATION`. Under a broken DDS config, nodes run but never see
  each other, and `rqt_graph` shows the disconnection immediately.

---

## Lab 3 — Geometric SE(3) controller for a quadrotor

Individual: convert between `geometry_msgs::msg::Quaternion`, `tf2::Quaternion`, and Eigen
3×3; extract yaw; quadrotor allocation-matrix rank. Team: implement the Lee et al. geometric
controller, build the wrench-to-rotor-speed matrix, and tune `kp/kv/kr/komega` until a
circular trajectory tracks stably.

- **Platform: Nav2 controller tuning — same loop shape, different plant.**
  Lab 3's graph is `/desired_state` + `/current_state` (`nav_msgs/Odometry`) → controller →
  `/rotor_speed_cmds`. AK-01's is Nav2 path + `/robocar_01/odometry/filtered` → controller
  → `/cmd_vel` → Yahboom STM32. Identical structure. The lab's payoff is learning that when
  tracking is bad you must first decide *whether the state estimate or the gains are wrong* —
  Nav2 gives you no help with that, and tuning DWB/MPPI against a broken EKF is how weeks
  disappear.
- **The tuning-tips symptom table is the transferable artifact.** "`komega` too high →
  this failure, `kr` too low → that failure" is a discipline: change one gain, name the
  symptom you expect, observe. Apply it verbatim to `process_noise_covariance` in
  `ekf.yaml`, where the comment already says raise index 11 (`vyaw`) if pose lags turns and
  lower it if heading is jittery — but there's currently no procedure for deciding which.
- **Deliverable 1 (individual) is directly reusable code.** The `tf2::fromMsg` /
  `tf2::toMsg` / quaternion-to-Eigen conversions are the glue in every node that touches
  the EKF output or the BE-880 compass heading. 20 minutes, immediately useful.
- **The z-up/z-down caveat is the bug you are going to hit.** The handout spends a full
  section on the paper using z-down while ROS uses z-up, and which equations change sign.
  Same failure mode as an inverted `ax` in `imu0_config`. Read that section even if you
  skip the controller implementation.
- **Skip the TESSE/Unity half if time is short.** The simulator, `mav_comm`, and
  `tesse_ros_bridge` are Humble-pinned and are not worth porting. Deliverable 1, Deliverable 2,
  and the conventions/tuning prose carry the value.

---

## Lab 4 — Min-snap trajectory optimization and drone racing

Individual: formulate minimum-derivative polynomial trajectory optimization as a QP
(`min pᵀQp s.t. Ap = b`), then count constraints for a k-segment minimum-snap problem.
Team: generate a smooth multi-segment trajectory through race gates with
`mav_trajectory_generation` and record a ROS 2 bag of the run.

- **Do the bag-recording section first — it is the highest-value 30 minutes in all five labs
  for AK-01 right now.** `ros2 bag record /current_state /desired_state` is the pattern that
  unblocks both hardware tickets:
  - **Lidar starburst:** record `/robocar_01/scan` + `/tf` for 60 s, then replay offline as
    many times as you need. You currently debug this by re-running the robot, which is slow
    and non-reproducible.
  - **IMU Errno 121:** record `/robocar_01/imu/data_raw` and measure the actual dropout
    rate. Right now "Errno 121 errors" is anecdotal. A bag turns it into a number, which
    tells you whether it's a rare bus collision (tolerable, retry in software) or a
    sustained fault (electrical, must fix the bus).
  - Note ROS 2 bags are a *directory* (`metadata.yaml` + `.db3`), not a single file.
- **Platform: Nav2 path smoothing.** Nav2's Smoother server solves the same problem the QP
  in Deliverable 1 solves — a planner emits a jagged grid path, and something has to make it
  dynamically feasible. Lab 4 is the theory under that server. Worth doing before you tune
  smoother parameters, not before you get Nav2 running at all.
- **AKSUMAEL: waypoint decomposition mirrors GoalStack.** "Gate poses → optimized trajectory
  → desired state at time *t*" is structurally the same as "goal → skill sequence → action
  this tick." The constraint-counting exercise (Deliverable 2) is a useful frame for thinking
  about which waypoints are hard constraints versus free derivatives the planner may choose —
  the analogue of fixed goal parameters versus fields the LLM fills in.
- **Lower priority than 2, 3, and 5.** Trajectory optimization matters for a fast quadrotor
  and much less for a skid-steer ground robot that cannot execute an aggressive trajectory
  anyway. Do the individual QP deliverables and the bag section; leave the racing.

---

## Lab 5 — Feature detection, matching, and tracking

Perspective projection and vanishing points; then SIFT/AKAZE/ORB/BRISK detection and
description, FLANN matching, Lowe's 0.8 ratio test, inlier/outlier statistics on real
rosbags, and Harris + Lucas-Kanade sparse optical flow tracking.

- **AKSUMAEL visual odometry for real-world deployment — this lab *is* the front end.**
  Deliverable 7 (Harris corners + `calcOpticalFlowPyrLK`) is the tracking stage of a VO
  pipeline. Everything downstream (essential matrix, pose recovery, scale) sits on top of
  frame-to-frame correspondences that are good enough. Start here, not with a VO library.
- **AK-01 rtabmap localization mode — Deliverable 6 gives you the diagnostic vocabulary.**
  rtabmap in localization mode fails when it cannot match enough features against the stored
  map, and it reports this as loop closures simply not firing. Lab 5's table — keypoints,
  matches, good matches, inliers, inlier ratio — is exactly the instrumentation you need to
  tell "no features detected" from "features detected but all outliers." Those have opposite
  fixes (lighting/texture/detector threshold vs. matcher and RANSAC settings). The handout
  even flags ORB with "we will use it for SLAM later" — rtabmap's default feature strategy is
  in that family, so run the comparison table on **your** environment before accepting the
  default `Kp/DetectorStrategy`.
- **The LK assumption is the failure mode you will hit on a ground robot.** Deliverable 7
  asks what assumption LK relies on: small inter-frame motion and brightness constancy. A
  skid-steer platform scrubbing through a turn violates the first badly. Knowing this in
  advance tells you to cap angular velocity during mapping runs rather than blaming the
  camera.
- **AKSUMAEL spatial self-awareness without F3 — Lab 5 is the sensor, Lab 2 is the frame.**
  LK tracks give ego-motion from images alone, which is the closest real replacement for
  F3's free coordinate readout. Pair them: Lab 5 produces the measurement, Lab 2 defines the
  frame it lands in.
- **AKSUMAEL Week 2 (reasoning without sensors) — Lab 5 bounds the problem rather than
  solving it.** Week 2 trains the bot to reason when a sense is missing; Lab 5 tells you
  concretely what visual odometry can and cannot deliver (no absolute scale from a monocular
  camera, degenerate under pure rotation, fails on textureless walls). Those limits are
  exactly the gaps Week 2's reasoning has to cover, so doing Lab 5 first makes the Week 2
  curriculum specific instead of hypothetical.
- **Perspective projection / vanishing points (Deliverables 1–2)** underpin camera intrinsics
  and the monocular-depth item on the AK-01 roadmap (Depth Anything V2). Short, do them.

---

## Not covered by any lab — don't wait on VNAV for these

- **IMU Errno 121 (I2C remote I/O error).** This is an electrical and bus-arbitration
  problem, not a robotics-algorithms problem. Three devices share bus 1 (GY-521 `0x68`,
  BE-880, Yahboom) per the AK-01 README, and two of the three addresses are still marked
  "TBD — verify." Run `i2cdetect -y 1`, confirm no address collision, check pull-up
  resistors and total bus capacitance, and try dropping the I2C baud rate in
  `/boot/firmware/config.txt`. Lab 4's bag recording is the only lab content that helps, and
  only for measuring the dropout rate.
- **BE-880 GPS baud fix.** Serial configuration. No lab covers it. What the labs *do* cover
  is where the fix goes afterward (Lab 2, `map`-frame EKF).
- **CycloneDDS tuning.** Lab 2 Deliverable 1 will detect a broken RMW setup but teaches
  nothing about configuring one.

---

## Version drift — read before running any lab code

The VNAV 2024 labs target **ROS 2 Humble** and install `ros-humble-*` packages directly
(`ros-humble-tf-transformations`, `ros-humble-ackermann-msgs`, `ros-humble-rviz-imu-plugin`).
The AK-01 README also still says Humble / Pi 4, while the platform target is **ROS 2 Jazzy**.
Consequences:

- Swap `ros-humble-*` → `ros-jazzy-*` in every lab's apt install line.
- `rclcpp` / `rclpy` node structure, `tf2`, launch files, and `colcon build --symlink-install`
  are unchanged across Humble → Jazzy. Labs 2 and 5 port cleanly.
- `mav_comm`, `mav_trajectory_generation`, and `tesse_ros_bridge` (Labs 3–4) are pinned to
  Humble forks and will not build on Jazzy without work. Their value is the theory and the
  individual deliverables, not the executables — treat them as reading.
- The lab pip pins (`numpy==1.26.4`, `rospy2`) will fight a Jazzy system environment. Use a
  venv, or skip the simulator packages entirely per the notes above.
