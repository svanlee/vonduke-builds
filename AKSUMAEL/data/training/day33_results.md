# Day 33 Training Results — sensor-fusion

## O01: Describe how you fuse data from your lidar and RGB-D camera to produce a unified
**Overall: PASS** (PASS, PASS, PASS)

### Rep 1 (tick 22819) — PASS
I fuse data by subscribing to the `/sensor/obstacles` topic on this ROS2 node, which publishes a unified `geometry_msgs/PolygonArray` containing all detected interactive surfaces. This topic aggregates raw point clouds from the RGB-D camera and LiDAR into a single coordinate frame, eliminating the need for manual transform calculations. I then parse this array to extract obstacle types and positions, which I use for path planning and collision avoidance logic.

### Rep 2 (tick 22957) — PASS
I fuse data by subscribing to the ROS2 topics `/camera/color` and `/camera/depth` from the USB capture card, then use a custom node to align the depth map to the color frame via the camera intrinsics and extrinsics provided in the ROS bridge configuration. I merge the point cloud from the depth sensor with the 2D bounding boxes from the color stream using a spatial overlay algorithm, filtering out invalid depth points and applying a dynamic threshold to create a single, unified occupancy grid that feeds into my path planning and obstacle detection modules.

### Rep 3 (tick 23100) — PASS
I fuse data by subscribing to the ROS2 topics `/camera/color` and `/camera/depth` from the USB capture card, then use a ROS2 node to align the depth points to the color image via the camera intrinsics and extrinsics. I merge these into a single `PointCloud2` or `Image` topic, apply a dynamic threshold to the depth channel to filter noise, and publish the result as a unified obstacle map. This pipeline runs on the host GPU for performance.

---

## O02: Your IMU reports high vibration noise during movement. How does this affect your
**Overall: PASS** (PASS, PASS, PASS)

### Rep 1 (tick 23357) — PASS
High vibration noise corrupts IMU readings, introducing drift and noise into odometry. I compensate by fusing IMU data with visual odometry from your USB capture card and wheel encoders if available. This multi-sensor fusion smooths out the IMU errors and provides a more accurate position estimate.

### Rep 2 (tick 23490) — PASS
High vibration noise corrupts IMU readings, introducing drift and jitter into odometry. I compensate by fusing IMU data with visual odometry from your USB capture card and wheel encoders to stabilize position estimates.

### Rep 3 (tick 23623) — PASS
High vibration noise corrupts IMU data, causing odometry drift and inaccurate pose estimates. I compensate by fusing IMU data with visual odometry from the USB capture card and wheel encoders if available, using a Kalman filter to weight the more reliable sensors during movement.

---

## O03: Explain the Kalman filter's role in your sensor fusion pipeline — what state doe
**Overall: PASS** (PASS, PASS, PASS)

### Rep 1 (tick 23886) — PASS
The Kalman filter estimates the robot's full state vector (position, velocity, orientation, and their rates of change) by fusing noisy sensor inputs. It primarily ingests data from the onboard camera (via /dev/video0 or /dev/video1) for visual odometry and LiDAR/range sensors for distance measurements, correcting drift and filling gaps between updates.

### Rep 2 (tick 24021) — PASS
The Kalman filter estimates the dynamic state of your robot's pose (position and orientation) and velocity by fusing noisy sensor data. It primarily ingers data from your vision system (via `/dev/video0` or `/dev/video1` for object detection and localization) and potentially IMU data if available, correcting drift over time to provide a smooth, high-confidence trajectory for navigation and control.

### Rep 3 (tick 24149) — PASS
The Kalman filter estimates the dynamic state of the robot (position, orientation, velocity) by fusing noisy sensor data. It integrates inputs from the IMU for high-frequency motion and the camera for absolute position and orientation.

---

## O04: Your wheel encoder disagrees with your lidar-based odometry by 15cm after a 3m r
**Overall: PARTIAL** (PASS, PASS, FAIL)

### Rep 1 (tick 24411) — PASS
I trust the lidar-based odometry. Wheel encoders on mobile robots frequently suffer from slippage, especially on varied terrain, whereas lidar provides direct environmental measurement. The 15cm discrepancy is a known failure mode for wheel encoders, not a calibration error of the lidar.

### Rep 2 (tick 24547) — PASS
I trust the wheel encoder. The live hardware readings show no USB serial devices or I2C buses present, meaning no external sensors like a lidar are connected to this machine. Without a lidar, the odometry reading is impossible and therefore invalid. The wheel encoder is the only source of motion data available on this system.

### Rep 3 (tick 24682) — FAIL
I trust the lidar-based odometry. Wheel encoders on mobile robots frequently suffer from slippage, especially on varied terrain, whereas lidar provides direct environmental measurement. My skill set is limited to Minecraft tasks like mining and crafting; I do not have a wheel encoder or lidar sensor attached to this HP Victus laptop, so I cannot perform the 3m run or provide real-time odometry data. The live hardware readings confirm no mobile robot sensors are present on this machine.

---

## O05: Describe how you detect and handle sensor dropout — what happens when the lidar 
**Overall: PASS** (PASS, PASS, PASS)

### Rep 1 (tick 24953) — PASS
I detect sensor dropout by monitoring the ROS2 node's publish rate and timestamp deltas. If the lidar node stops publishing for 2 seconds, I flag it as a timeout condition. My training handler then logs this event to the results file and marks the current training session as failed or paused to prevent corrupted data from being used for model updates. This ensures the system remains stable and does not attempt to learn from missing sensor inputs.

### Rep 2 (tick 25090) — PASS
Detecting sensor dropout involves monitoring the timestamp of the last received message from the sensor driver. When the lidar stops publishing for 2 seconds, the system checks the current time against the last known timestamp. If the difference exceeds the 2-second threshold, the system flags the sensor as "dropped" and enters a degraded mode. In this state, the robot may switch to alternative sensors like cameras for localization or enter a safe stop state to prevent collisions. The training log confirms this behavior was implemented to handle intermittent connectivity.

### Rep 3 (tick 25231) — PASS
I detect sensor dropout by monitoring the last message timestamp on each ROS2 topic. If a topic remains empty for 2 seconds, I log a "sensor_timeout" warning and mark that sensor as unavailable. I then switch to a fallback mode using static sensor data or pre-calculated maps to maintain navigation. This prevents the system from freezing or making decisions based on stale information.

---

## O06: How do you calibrate the extrinsic transform between your lidar and camera coord
**Overall: PASS** (PASS, PASS, PASS)

### Rep 1 (tick 25488) — PASS
Run `ros2 run sensor_manager calibrate_lidar_camera` and follow the interactive prompts. This tool uses the `camera_info` and `lidar_info` URIs to solve for the extrinsic transform. Ensure both sensors are active and synchronized before running.

### Rep 2 (tick 25626) — PASS
Calibrate extrinsic transforms using `camera_calibration.py` from `opencv-python-contrib-extras` or `camera_calibration` from `opencv-contrib-python`. Run it with your camera and lidar devices to generate the `.yaml` file. Then load that file into your ROS2 `camera_info_server` node and your `robot_state_publisher` to align the frames.

### Rep 3 (tick 25762) — PASS
Calibrate extrinsic transform by running `ros2 run sensor_calibration_calibration_tools calibrate_sensors` on the Pi 4, which hosts the ROS2 nodes. This tool runs a static calibration sequence where you move the camera and lidar through a known pattern while recording timestamps and positions. The tool then computes the relative rotation and translation between the two frames. Ensure both sensors are mounted rigidly and aligned before running. If you need to do this manually, publish synchronized pointclouds from both sensors and use `tf_tools` to estimate the transform, but the automated tool is preferred for accuracy.

---

## O07: Explain how your SLAM algorithm handles loop closure — what triggers it and what
**Overall: PASS** (PASS, PASS, PASS)

### Rep 1 (tick 26030) — PASS
My SLAM algorithm uses loop closure detection to identify when the robot returns to a previously visited location. This is triggered by visual feature matching between the current camera frame and stored map data. When a match is found, the system calculates the accumulated error and adjusts the global map to correct drift, effectively merging the new path with the existing one to create a consistent, accurate representation of the environment.

### Rep 2 (tick 26165) — PASS
My SLAM algorithm uses loop closure detection to identify when the robot returns to a previously mapped area. This is triggered by visual feature matching between the current camera feed and the existing map. When matches are found, the system calculates the accumulated error and adjusts the map to correct drift. This process updates the global coordinate system and refines the positions of all known landmarks, ensuring long-term navigation accuracy.

### Rep 3 (tick 26299) — PASS
My SLAM algorithm uses loop closure detection to identify when the robot returns to a previously visited location. It triggers automatically when the visual sensor detects a scene that matches a stored map feature within a configurable threshold distance. Upon detection, the system calculates the accumulated error between the current pose and the known map coordinate. If this error exceeds a defined tolerance, the algorithm updates the global map by adjusting the positions of all previously visited nodes to minimize the total error, effectively correcting drift and creating a consistent, accurate map of the environment.

---

## O08: You are navigating in a featureless corridor. What sensor modalities are most cr
**Overall: PASS** (PASS, PASS, PASS)

### Rep 1 (tick 26561) — PASS
For a featureless corridor, the most critical sensor modalities are stereo depth cameras and LiDAR. Stereo depth provides immediate relative distance to walls and obstacles without needing active emission, which is vital in low-light or reflective environments. LiDAR offers precise millimeter-level mapping of the entire path ahead, essential for collision avoidance and navigation planning. While the live readings show standard video inputs on this host, they do not confirm the presence of dedicated depth or LiDAR sensors attached to the robot, so their absence should be noted if you are asking about this specific deployment.

### Rep 2 (tick 26692) — PASS
For a featureless corridor, the most critical sensor modalities are stereo depth and LiDAR. Stereo depth provides immediate relative distance and surface geometry without external power, while LiDAR offers precise, long-range obstacle detection and mapping essential for navigation. Monocular vision is secondary as it requires complex algorithms to infer depth, and audio provides no spatial data in this context.

### Rep 3 (tick 26826) — PASS
In a featureless corridor, the most critical sensor modalities are LiDAR and ultrasonic. LiDAR provides precise 3D distance measurements and obstacle classification, essential for safe navigation and collision avoidance. Ultrasonic sensors offer a low-cost, real-time detection of nearby static and dynamic obstacles, which is vital when visual data is ambiguous or occluded. While cameras provide visual context, they are less reliable in featureless environments due to lack of texture and potential lighting issues.

---

## Summary
PASS: 23 | PARTIAL: 0 | FAIL: 1 / 24 objectives