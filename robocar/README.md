# RoboCar

Ground-based ROS 2 platform — first node in a planned multi-agent hive/swarm
architecture. Built on Raspberry Pi 4, ROS 2 Humble. All topics namespaced
under `/robocar_01/` from day one for multi-agent compatibility.

---

## System Block Diagram

```
                          ┌─────────────────────────────┐
                          │      Raspberry Pi 4          │
                          │      ROS 2 Humble            │
                          │                              │
  ┌────────────────┐      │  ┌────────────────────────┐  │
  │  Delta-2A      │ UART │  │ lidar driver           │  │
  │  2D LiDAR      ├──────┼─▶│ → /robocar_01/scan     │  │
  └────────────────┘      │  └────────────────────────┘  │
                          │                              │
  ┌────────────────┐      │  ┌────────────────────────┐  │
  │  GY-521 (MPU-  │ I2C  │  │ imu_publisher.py       │  │
  │  6050) @ 0x68  ├──┐   │  │ → /robocar_01/imu/     │  │
  └────────────────┘  │   │  │      data_raw          │  │
                      │   │  └───────────┬────────────┘  │
  ┌────────────────┐  │   │              ▼               │
  │  BE-880 GPS +  │  │   │  ┌────────────────────────┐  │
  │  compass       ├──┼───┼─▶│ robot_localization EKF │  │
  └────────────────┘  │   │  │ (config/ekf.yaml)      │  │
                      │   │  │ odom → base_link TF    │  │
  ┌────────────────┐  │   │  │ → /robocar_01/         │  │
  │  Yahboom 4-ch  │  │   │  │    odometry/filtered   │  │
  │  encoder motor ├──┘   │  └───────────┬────────────┘  │
  │  driver (STM32 │shared│              ▼               │
  │  co-processor) │ bus  │  ┌────────────────────────┐  │
  └───────┬────────┘      │  │ slam_toolbox           │  │
          │               │  │ map → odom TF          │  │
     ┌────▼─────┐         │  │ (static identity until │  │
     │ 4× motors │        │  │  first SLAM session)   │  │
     │ + encoders│        │  └────────────────────────┘  │
     └──────────┘         └─────────────────────────────┘
```

---

## Hardware Manifest

| Device | Interface | Address / Port | Role |
|---|---|---|---|
| Delta-2A 2D LiDAR | UART | `/dev/ttyUSB*` | Planar scan → SLAM |
| GY-521 (MPU-6050) IMU | I2C bus 1 | `0x68` | Accel + gyro → EKF |
| BE-880 GPS/compass | I2C bus 1 | TBD — verify | Global position + magnetometer |
| Yahboom 4-ch encoder motor driver | I2C bus 1 / UART (Type-C) | TBD — verify | Motor control + wheel odometry (STM32 co-processor — **not** a GPIO HAT) |

**Shared I2C bus:** three devices on SDA (Pin 3) / SCL (Pin 5). Address
conflicts must be verified post-wiring with `i2cdetect -y 1`.

---

## Frame Tree (REP 105)

```
map ──▶ odom ──▶ base_link ──▶ { imu_link, laser, gps_link }
 │        │
 │        └─ LOCAL: robot_localization EKF (continuous, smooth, drifts)
 └─ GLOBAL: slam_toolbox / AMCL (accurate, jumpy)
    Static identity map→odom is the correct placeholder before
    real localization is running.
```

Validation sequence: `view_frames` audit → wheel-slip test → kidnap test
in Gazebo.

---

## Software Stack

- **OS:** Ubuntu 22.04 / ROS 2 Humble
- **Localization:** `robot_localization` EKF — wheel odom + IMU fusion
- **SLAM:** `slam_toolbox` (first mapping session pending)
- **Namespace:** `/robocar_01/` — hive-ready from day one

## Roadmap

1. ✅ Chassis reassembled post-milling; wiring plan set (screw terminal adapter, shared I2C bus)
2. IMU bring-up → `imu_publisher.py` → verify with `ros2 topic hz`
3. EKF fusion (`config/ekf.yaml`) → wheel-slip + kidnap validation
4. First SLAM session with `slam_toolbox`
5. Nav2 integration
6. Perception upgrades: Unitree L1 3D LiDAR → EllipseLIO, Depth Anything V2 monocular depth
7. Hive expansion: ESP32-S3 ground nodes (micro-ROS), aerial node (F450 + MAVROS2)
