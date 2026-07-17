# DEVLOG — VonDuke Builds

Daily engineering journal. New entries go at the TOP.
Format: what I built, why it matters, where it lives.

---

<!-- New entries go at the TOP -->

### Fri Jul 17 2026
**[FIX] — AKSUMAEL vision-decision starvation fix + cognitive.py dead-code removal**

What: Two AKSUMAEL fixes. (1) `core/vision_brain.py`'s `ask_vision()` — the
per-tick gameplay decision call, the single highest-stakes LLM call in the
whole loop — was still running the pre-thinking-model budget
(`max_tokens=400`, an 8s timeout) that commit `0a7a18d` already found and
fixed on every *other* local-LLM call site (inventory reader, chest
manager, command parser, code-skill generator, inner monologue,
curriculum — all bumped to 800-1400 tokens / 45-60s). The local mesh-llm
model burns 200-300+ tokens on hidden reasoning before it answers, so the
one call site left on the old budget kept getting cut off mid-thought,
failing to parse as JSON, and silently defaulting to
`{"action": "wait", "confidence": 0.0}` — the agent standing around
instead of committing to an action. Brought it in line with its sibling
calls (`max_tokens=1200, timeout=45, local_retries=3`) and deleted the
now-dead `LOCAL_LLM_TIMEOUT` config value. (2) `core/cognitive.py`
carried a "five-component cognitive architecture" (belief state, goal
stack, episodic memory, inner monologue, reflection loop) per the old
README description — but a full-codebase grep showed `BeliefState`,
`GoalStack`, and `EpisodicMemory` were write-only: persisted to their own
JSON file every tick, never read back by anything. `GoalStack`'s reactive
rules (creeper → flee, diamond_ore → mine_diamond) duplicated logic
`core/fsm.py` already runs directly off the same YOLO detections, and its
`EpisodicMemory` shared a name with (but was entirely separate from) the
real, actually-used `core/episode_memory.EpisodeMemory`. Deleted all
three; kept `InnerMonologue`, the one component whose output actually
flows back into the vision-LLM prompt.

Why it matters: the vision-decision starvation bug meant AKSUMAEL's
highest-level planning call (EXPLORE/EAT ticks, and periodic MINE
check-ins) was disproportionately likely of every LLM call in the
codebase to silently no-op, which reads exactly like "won't commit to an
action" / "gets lost in low confidence" from the outside. The
cognitive.py cleanup removes ~150 lines that looked load-bearing (three
of five advertised "cognitive architecture" components) but did nothing
— disk I/O every tick for state nothing ever consulted, and a real risk
of a future change assuming `cognitive.episodic`/`cognitive.belief` fed
into a decision when they never did.

File(s): `AKSUMAEL/core/vision_brain.py`, `AKSUMAEL/config.py`,
`AKSUMAEL/core/cognitive.py`, `AKSUMAEL/test_cognitive.py`, `README.md`
(corrected the AKSUMAEL project description to match, removed
`stacktrack/`/`slabscout/` rows — both ship in their own separate repos,
not this one), `plan/365-commit-plan.md` (marked the Day 2/3 Stacktrack/
SlabScout rows as external, not owed here).

---

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
