# VonDuke Builds — 365 Commit Plan (Days 1–30)

Renumbered at repo launch: **Day 1 = Fri Jul 03 2026.**
The daily-nudge Action reads this table — keep the `| N | ... |` row format.

## Commit Hygiene Rules

1. Every commit has a real file change — no README-only commits after week 1.
2. Conventional commits: `feat:` `fix:` `docs:` `test:` `data:`
3. DEVLOG.md gets a weekly summary entry.
4. No "WIP" / "misc" / "stuff" commits — name what you built.
5. Demo artifacts count — screenshots, logs, wiring photos are real commits.
6. Miss a day → double up tomorrow with two real things. Don't pad.

---

## Week 1 — Ship the Foundation

| Day | Date | Project | Commit Title | Artifact |
|---|---|---|---|---|
| 1 | Jul 3 | Launch | `day-1: init VonDuke Builds` | Repo, DEVLOG, RoboCar README + IMU publisher + ekf.yaml |
| 2 | Jul 4 | _(external)_ | Stacktrack v2.1 shipped in its own separate repo, not part of this plan | — |
| 3 | Jul 5 | _(external)_ | SlabScout v1.0 shipped in its own separate repo, not part of this plan | — |
| 4 | Jul 6 | _(superseded)_ | Planned "five-component cognitive architecture" doc — never committed; the actual Jul 12 architecture overhaul (`82fad8c`) took a different shape (goal stack in `memory/goals.py`, episode memory in `core/episode_memory.py`, inner monologue in `core/cognitive.py`) | — |
| 5 | Jul 7 | _(superseded)_ | Planned belief-state schema — never committed. A `BeliefState` class was added later (part of the Jul 12 overhaul) but turned out to be write-only dead code (nothing ever read it back) and was deleted Jul 17 — see DEVLOG | — |
| 6 | Jul 8 | RoboCar | `data: i2cdetect scan log — GY-521 confirmed at 0x68` | `robocar/logs/i2c_scan.txt` |
| 7 | Jul 9 | General | `docs: week 1 recap` | DEVLOG.md weekly entry |

## Week 2 — RoboCar Sensor Stack

| Day | Date | Project | Commit Title | Artifact |
|---|---|---|---|---|
| 8 | Jul 10 | RoboCar | `docs: BE-880 GPS/compass I2C wiring diagram` | `robocar/hardware/be880_wiring.md` |
| 9 | Jul 11 | RoboCar | `docs: Yahboom encoder driver I2C/UART pinout reference` | `robocar/hardware/yahboom_encoder_driver.md` |
| 10 | Jul 12 | RoboCar | `feat: IMU publisher — raw data validation pass` | `robocar/nodes/imu_publisher.py` (validation update) |
| 11 | Jul 13 | RoboCar | `feat: Delta-2A LiDAR launch file — /robocar_01/scan` | `robocar/launch/lidar.launch.py` |
| 12 | Jul 14 | RoboCar | `feat: EKF bringup launch — odom→base_link live` | `robocar/launch/ekf.launch.py` |
| 13 | Jul 15 | RoboCar | `test: first SLAM session log — slam_toolbox output` | `robocar/logs/slam_session_01.txt` |
| 14 | Jul 16 | General | `docs: week 2 recap — sensor bus bring-up notes` | DEVLOG.md weekly entry |

## Week 3 — AKSUMAEL Vision Pipeline

| Day | Date | Project | Commit Title | Artifact |
|---|---|---|---|---|
| 15 | Jul 17 | AKSUMAEL | `feat: migrate predict() → track() with ByteTrack` | `aksumael/vision/tracker.py` |
| 16 | Jul 18 | _(superseded)_ | Planned belief-state bridge — same concept as Day 5, never built this way; see that row | — |
| 17 | Jul 19 | AKSUMAEL | `feat: trajectory history — defaultdict(track_id → [(x,y)])` | `aksumael/cognition/trajectories.py` |
| 18 | Jul 20 | AKSUMAEL | `feat: HUD renderer — label + confidence overlay` | `aksumael/vision/hud.py` |
| 19 | Jul 21 | AKSUMAEL | `data: Train-6 results — mAP50 comparison vs Train-4` | `aksumael/training/train6_results.md` |
| 20 | Jul 22 | AKSUMAEL | `data: tracked gameplay demo capture` | `aksumael/demos/bytetrack_demo.png` |
| 21 | Jul 23 | General | `docs: week 3 recap — vision pipeline notes` | DEVLOG.md weekly entry |

## Week 4 — Hive Groundwork

| Day | Date | Project | Commit Title | Artifact |
|---|---|---|---|---|
| 22 | Jul 24 | Hive | `docs: hive node BOM + architecture` | `hive/README.md` |
| 23 | Jul 25 | Hive | `feat: ESP32-S3 firmware skeleton — FreeRTOS dual-task pattern` | `hive/firmware/main.cpp` |
| 24 | Jul 26 | Hive | `feat: MOSFET PWM motor driver test sketch` | `hive/firmware/motor_test.cpp` |
| 25 | Jul 27 | Hive | `feat: LM393 speed sensor ISR read` | `hive/firmware/speed_sensor.cpp` |
| 26 | Jul 28 | Hive | `data: CN3065 solar charge curve log` | `hive/logs/solar_charge_test.md` |
| 27 | Jul 29 | Hive | `feat: UDP telemetry broadcast test` | `hive/firmware/udp_telemetry.cpp` |
| 28 | Jul 30 | DWEEB | `docs: DWEEB concept document + block diagram` | `dweeb/CONCEPT.md` |
| 29 | Jul 31 | FeatherPLC | `docs: FeatherPLC prototype writeup — nine-machine simulator` | `featherplc/README.md` |
| 30 | Aug 1 | General | `docs: month 1 recap + month 2 plan` | DEVLOG.md + this file extended |

---

## Month 2 Focus Areas (Days 31–60)

- **RoboCar:** EKF tuning (wheel-slip + kidnap validation), URDF + Gazebo sim, Nav2 bringup.
- **AKSUMAEL:** Inner monologue JSON, reflection loop, first demo video commit.
- **Hive:** First mini-tank physically assembled, micro-ROS ping test.
- **DWEEB:** NVIDIA Inception eligibility research.

## Month 3 Focus Areas (Days 61–90)

- **RoboCar:** Unitree L1 integration (EllipseLIO path), first full SLAM map.
- **AKSUMAEL:** DAgger-style corrective dataset, recovery state training data.
- **Hive:** Two bots running, first swarm coordination test.
- **LinkedIn:** One demo video per week, linked from DEVLOG commits.
