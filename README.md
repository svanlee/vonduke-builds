# VonDuke Builds

**365 days of building in public.** Scott Van Leeuwen · VonDuke Designs LLC
Systems engineer — 30 years of electromechanical, robotics, and embedded systems work across aerospace (AS9100/Nadcap/ITAR), automotive (IATF 16949), and industrial automation.

One real artifact committed per day: code, configs, wiring docs, test logs, demo captures. No filler. The full daily journal lives in [DEVLOG.md](DEVLOG.md).

---

## Active Projects

| Project | What it is | Status |
|---|---|---|
| [`robocar/`](robocar/) | ROS 2 Humble autonomous ground robot — Pi 4, Delta-2A LiDAR, GY-521 IMU, BE-880 GPS/compass, Yahboom encoder motor driver. First node of a planned multi-agent hive. | Sensor bring-up |
| [`AKSUMAEL/`](AKSUMAEL/) | AI game-playing agent (Minecraft) — YOLOv8/YOLO26n vision + FSM on a local/Gemini/Claude 3-tier LLM router, goal stack, episodic memory, inner monologue, skill mining/replay. | Training run 6 |
| [`hive/`](hive/) | ESP32-S3 Supermini mini tank swarm — FreeRTOS dual-task firmware, micro-ROS, solar LiPo charging. Ground swarm first, aerial node later. | BOM confirmed, build queued |
| [`featherplc/`](featherplc/) | ESP32 industrial machine simulator prototype — nine simulated machines, WebSocket HMI, fault state machine, E-Stop logic, PPM calc. | Prototype |
| [`dweeb/`](dweeb/) | Modular ESP32-S3 brain board concept for maker/robotics education. USB-C GPIO learning pad that snaps into robot chassis. | Concept |

Coin/collectible appraisal tools (Stacktrack, SlabScout) live in their own separate repos, not here.

---

## The Rules

1. Every commit has a real file change.
2. Conventional commits: `feat:` `fix:` `docs:` `test:` `data:`
3. Weekly summary entries in DEVLOG.md.
4. No "WIP" / "misc" / "stuff" commits — name what you built.
5. Demo artifacts count: logs, screenshots, wiring photos.
6. Miss a day → double up with two real things the next. No padding.

---

*GitHub: [@svanlee](https://github.com/svanlee) · LinkedIn: [linkedin.com/in/scottvanleeuwen](https://linkedin.com/in/scottvanleeuwen)*
