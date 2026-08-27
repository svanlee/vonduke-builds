# AKSUMAEL + RoboCar — Agent Handoff
**Date:** 2026-08-11  
**Owner:** Scott Van Leeuwen  
**Context:** Scott is relocating over the next two weeks. This document captures the exact current state of both projects so any agent can pick up without losing ground.

---

## Ground Rules (non-negotiable)

1. **Do not silently change interfaces.** Topic names, message types, config schema, file layout are load-bearing. Propose first, then change.
2. **Namespace every ROS2 topic under `/robocar_01/`.** Multi-agent hive is the long-term target — un-namespaced topics become expensive debt.
3. **No unvetted installs.** Check npm/pip `prepare`/`postinstall` hooks before installing anything.
4. **Verify writes.** MCP terminal must be live for the entire write. Re-verify files exist before reporting completion.
5. **Commit small, commit often.** Daily-commit streak project (`svanlee/vonduke-builds`).
6. **When blocked, leave a `HANDOFF.md`** in the repo describing where you stopped and what to do next.
7. **Security:** API keys at `~/.config/anthropic/key`, `~/.config/google/key`, `~/.config/roboflow/key` — never print values, never commit. `.notes/` is gitignored — never commit.
8. **Service management:** `systemctl --user restart aksumael` NOT `bash run.sh`. LLM_TEMPERATURE stays at 0.2 in `core/llm_router.py`.
9. **Protected dirty files — never touch:** `config.py`, `data/skills/*.json`, `day5_results.md`, `mine_*_ore.json`, `overnight_monitor.sh`.

---

## 1. AKSUMAEL — AI Assistant (Jarvis)

### Hardware
- **Host:** HP Victus laptop (Ryzen 7 7445HS, RTX 4050 6GB VRAM)
- **IP:** 192.168.0.156, hostname: robocar-hub
- **VRAM constraint:** YOLOv8n inference runs concurrently — budget any new model against this
- **Access:** MCP terminal through Claude Desktop (must be open and paired). Use ttyd at `http://localhost:7682/` via Claude in Chrome for bash.

### Repo
- Path: `/home/ros/vonduke-builds/AKSUMAEL`
- Branch: `inventory-sprite-matching` (main working branch)
- Also has: `rdkx5-stereonet-integration` (RDK X5 integration docs)

### Key Services
| Service | Command | Port/Notes |
|---|---|---|
| AKSUMAEL bot | `systemctl --user restart aksumael` | Main bot loop |
| mesh-llm | llama-server at localhost:9337 | Local LLM; health endpoint unreliable — use generation probe |
| Training bridge | POST http://localhost:7683/train {"text": "..."} | Warm-up gate: tick >= 1250 |
| Chat UI | `systemctl --user status aksumael-chat` | http://192.168.0.156:7684/ |
| Honcho | honcho-embed :9338, honcho-api :8000, honcho-deriver | Episodic memory; retrieval now wired into _build_prompt() |
| WireGuard | `systemctl --user status wg-quick@wg0` | VPN, UDP 51820 (needs router port forward) |
| Cloudflare tunnel | `systemctl --user status cloudflared-aksumael` | Public HTTPS URL for remote access |

### Architecture
- **Perception:** YOLOv8n fine-tuned (53 classes, 0.807 mAP), ByteTrack, `entity_id` → `track_id` mapping bridges to cognition
- **Cognition:** `core/cognitive.py` — Belief State, Goal Stack, Episodic Memory, Inner Monologue. Planner: BDI + HTN
- **LLM routing:** 3-tier local-first (`core/llm_router.py`). Temperature: 0.2
- **Action:** KB2040 microcontroller doing HID keystroke injection. `pad_bridge.py` + `code.py` arbitration is fragile — read before modifying
- **Frame source:** Capture card (/dev/video2 — frequently drops; fix: `sudo modprobe -r uvcvideo && sudo modprobe uvcvideo`)
- **Identity:** AKSUMAEL (bot name). Answers as a general-purpose engineering/robotics assistant. NOT a Minecraft bot.

### Training Arc — Current State (2026-08-11)
**Days 1–35 complete.** Branch: `inventory-sprite-matching`.

| Day | Topic | Score |
|---|---|---|
| 1–8 | Early word cap / identity | Baseline |
| 9–14 | ROS2, GPIO, path planning, web dev | Mixed |
| 15–21 | Threat, maintenance, sensor fusion, orchestration | 28P/36Par/45F overall |
| 22–26 | Post-fix regression | 0→4/24 as fixes landed |
| **27** | **Edge fleet coordination** | **24/24 PASS (first perfect score)** |
| 28–35 | Conversation, threat, code, orchestration, fusion, maintenance, path | Committed |

**Game domains trained (2026-08-11):**
- Minecraft (player/assistant framing — NOT bot FSM)
- Fallout 76
- Racing sim
- Flight sim

**Final regression (5 questions, 2026-08-11): 5/5 Jarvis-level.**
- Q4 was a direct Minecraft bait ("build a base") — rejected cleanly, reframed as robotics deployment.
- Zero hardware-state contamination.

### Prompt Fixes Applied (commits on branch)
- **Fix A:** ENUMERATE routing moved to Python `_is_enumeration()` gate
- **Fix C:** False-premise block Python-gated on whether figures appear in live blocks
- **Fix D:** Conversational branch added at 120 words before SHORT branch
- **Fix F:** Absence markers (`_live_hardware()`) suppressed from knowledge answers
- **Fix G:** SHORT word budget 40 → 80; floor warning if < 60 words with technical term
- **Fix H:** `_is_state_question()` gate — LIVE PERCEPTION block suppressed for knowledge questions
- **Minecraft erased:** All game-FSM framing, "deaths" example, Minecraft context removed from knowledge prompts
- **Camera broadened:** CAMERA OFFLINE guard is general-purpose vision, not game-only
- **Identity fixed:** `core/identity.py` — AKSUMAEL, no "Jarvis" in prompts

### Open Issues (AKSUMAEL)
1. **llama.cpp CLIP SIGSEGV** — vision-capable LLM backend rebuild stalled on SIGSEGV in vendored CLIP loader. **[VERIFY current state before restarting]**
2. **reid_bridge.py** — DINOv3 appearance embeddings on ByteTrack (dual work/stable banks, gated-EMA, CONFIRMED/AMBIGUOUS/NEW lifecycle). **[VERIFY: in live loop or prototype-only?]**
3. **meta-2 premise rejection** — bot rejects "400+ training objectives" because count isn't in any live block. Small fix pending.
4. **mesh-llm knowledge gaps** — wrong ESP32 APIs, wrong paho-mqtt platform. Not prompt-fixable; needs fine-tuning or model swap.
5. **Honcho retrieval** — wired into `_build_prompt()` but never tested in live conversation. Needs eval.
6. **Camera/KB2040 physical** — /dev/video2 drops after restart (uvcvideo fix above). ttyUSB0 (KB2040) also drops. Physical reseating needed when back on-site.
7. **Controller permissions** — `sudo usermod -aG input ros && reboot` pending.

### Priority gaps toward full AI/ML agent
1. Close the learning loop: log `(belief_state, chosen_action, outcome)` tuples before choosing a learning algorithm
2. Reflection gate before action in Inner Monologue loop
3. Vision-capable LLM backend (blocked on CLIP SIGSEGV — verify first)
4. Re-ID persistence (`reid_bridge.py`)
5. HUD output (YOLOv8n overlay for demo + debugging)

### Chat UI
- Live at `http://192.168.0.156:7684/` (LAN) and via Cloudflare tunnel (public URL in service logs)
- Flask backend (`chat_server.py`), dark theme, mobile-friendly, 8-message context window
- Systemd service: `aksumael-chat.service`
- WireGuard gives full LAN access remotely; Cloudflare tunnel gives public HTTPS access for off-site testing

---

## 2. RoboCar — ROS2 Autonomous Rover

### Hardware
| Component | Detail |
|---|---|
| Queen node | Pi 4, ROS2 Humble, namespace `/robocar_01/` |
| Compute upgrade | D-Robotics RDK X5 8GB (10 TOPS BPU, Ubuntu 22.04, TROS.b Humble, WiFi 6) |
| Stereo camera | D-Robotics RDK Stereo Camera GS130W (dual SC132GS global shutter, 1280×1080 @ 120fps, 80mm baseline, MIPI) |
| LiDAR | Delta-2A, 2D |
| IMU | GY-521, I2C 0x68 |
| GPS/compass | BE-880 |
| Motor driver | Yahboom 4-Channel Encoder Motor Driver — STM32 co-processor over I2C/UART. NOT a GPIO HAT. |

### Critical Platform Notes
- **RDK X5 ≠ Pi ≠ Jetson.** Use `Hobot.GPIO` not `RPi.GPIO`. I2C bus indices differ from Pi.
- Shared I2C bus: GY-521, BE-880, Yahboom STM32. Run `i2cdetect` after any rewiring.
- Motor driver speaks a protocol — off-the-shelf diff-drive controllers assuming Pi GPIO will NOT work.

### RDK X5 Integration (committed 2026-08-11)
**Branch:** `rdkx5-stereonet-integration`  
**Path:** `docs/rdkx5_integration/`

| File | Purpose |
|---|---|
| `rdkx5_setup.sh` | Run on X5 after flash — installs hobot_stereonet, creates stereonet.service |
| `gs130w_stereonet.launch.py` | GS130W + hobot_stereonet launch with intrinsics; remaps to /stereonet/depth |
| `hub_nav2_costmap_patch.yaml` | Nav2 ObstacleLayer config for hub — subscribes /stereonet/depth |
| `hub_bridge.launch.py` | Hub-side liveness check + optional RViz2 |
| `INTEGRATION.md` | Step-by-step guide |

**Pipeline:** GS130W (MIPI) → hobot_stereonet (X5 BPU, 15-25 FPS) → `/stereonet/depth` (PointCloud2) → ROS2 DDS (DOMAIN_ID=42) → hub → Nav2  
**TODO before first run:** Update camera XYZ mount offset in `gs130w_stereonet.launch.py`. Add `/robocar_01/` namespacing to all topics.

**Second step (when ready):** `dstereo_occnet` for 3D occupancy grid — currently ZED-2i only but GS130W config is a swap.

### Software Conventions
- REP 105: `odom → base_link` (continuous, drifts ok), `map → odom` (globally anchored). Static identity `map → odom` is correct placeholder.
- EKF: `robot_localization` (`ekf.yaml`), fusing wheel odometry + IMU
- Navigation: Nav2 + `slam_toolbox`
- URDF: xacro composition — separate files for base/wheels/sensors/gazebo

### Roadmap
1. SLAM with `slam_toolbox` on Delta-2A (2D) first
2. Unitree L1 upgrade → 3D + EllipseLIO (LiDAR-inertial odometry)
3. Perception: Depth Anything V2 (monocular depth), LocateAnything (open-vocab detection)
4. `yolo_ros` (mgonzs13, Humble = `main` branch): `Mask.msg` has polygon points not bitmask; `Detection.id` is string; no persistent entity ID field

---

## 3. Hive / Swarm Nodes

- Platform: ESP32-S3 Supermini mini tank bots
- Firmware: FreeRTOS dual-task (stream + command pinned to separate cores). Any tight loop touching hardware gets `vTaskDelay(1)`.
- Video: UDP not TCP — drop frames, never stall pipeline
- Support hardware: CN3065 solar LiPo chargers, LM393 speed sensors, MOSFET PWM, ST7789 displays
- **Build config matters:** debug SysTick took 7200 cycles vs 2096-cycle budget; release: 427 cycles. Check debug vs release before auditing logic.
- Node UI pattern: device hosts own WiFi AP + browser page. Zero cloud, zero app.
- Patternflow plugin ABI viable for behavior updates without reflash — needs watchdog if pursued.

---

## 4. Cross-Project Items

- Gesture control (MediaPipe, no extra hardware): Palm Up=Stop, Thumbs Up=Forward, Fist=Hold, Point L/R=Turn, WiFi
- AR robot dashboard: floating per-node telemetry, maps to `/robocar_01/` namespacing
- Portable command station: CM5 + 5-7" DSI + BlackBerry/KB2040 keyboard + 5V/5A UPS + INA219. Ref: Pironman 5.

---

## 5. Remote Access Setup (2026-08-11)

### WireGuard (full LAN access)
- Installed on robocar-hub, UDP port 51820
- **Router action needed:** Forward UDP 51820 to 192.168.0.156
- Client config + QR code at `docs/wireguard_client.conf` (check if gitignored)
- Gives access to everything on 192.168.0.x including SSH, chat UI, ROS2

### Cloudflare Tunnel (off-site testing, no router config)
- Service: `cloudflared-aksumael` (systemd user service)
- Exposes port 7684 (AKSUMAEL chat UI) publicly
- Quick tunnel URL changes on restart — check logs: `journalctl --user -u cloudflared-aksumael -n 30`
- For a permanent URL: free Cloudflare account + domain → `cloudflared tunnel create aksumael`

---

## 6. Immediate Next Steps (priority order)

1. **Verify WireGuard + Cloudflare tunnel** are running and reachable
2. **Forward UDP 51820** on router to 192.168.0.156 (WireGuard)
3. **Update camera XYZ mount offset** in `gs130w_stereonet.launch.py` before running RDK X5
4. **Add `/robocar_01/` namespacing** to RDK X5 launch files
5. **Verify llama.cpp CLIP SIGSEGV status** before restarting vision backend work
6. **Verify reid_bridge.py** — in live loop or prototype?
7. Continue training arc — Days 36+ (integration tests, multi-turn conversation, Honcho retrieval eval)
8. When back on-site: reseat capture card and KB2040 USB

---

## 7. Open Questions for Scott

1. llama.cpp vision backend — SIGSEGV in CLIP loader resolved, or still blocked?
2. reid_bridge.py — integrated into live AKSUMAEL loop or still prototype?
3. RoboCar compute — still Pi 4 as queen, or RDK X5 started physically?
4. Does Scott have a Cloudflare account/domain for a permanent tunnel URL?
5. Relocation window — is there a period where only laptop-side (AKSUMAEL) work is possible (no robocar hardware)?

---

*Generated: 2026-08-11. Branch: inventory-sprite-matching. All training days 1-35 committed. Chat UI live. WireGuard + Cloudflare tunnel installed.*
