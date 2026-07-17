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

### Fri Jul 17 2026 (cont'd)
**[FEAT] — Open-vocabulary auto-labeling + single class-registry source of truth**

What: `tools/claude_autolabel.py` — the labeler AutoTrainer's automatic
retrain cycle actually calls — forced Claude to pick a class from a
frozen, already-stale 43-class snapshot (the real deployed
`data/yolo_dataset/data.yaml` had 53), so a genuinely new object had no
path to becoming a trainable class; it also called `api.anthropic.com`
directly with its own separate key file, the one call site in the whole
codebase not going through `core/llm_router.py`. Rewrote it to route
through a new `core.llm_router.try_claude()` and to propose a new class
name when nothing on the known list fits, instead of forcing a bad
match. While fixing that, found a second, currently-live bug:
`tools/yolo_finetune.py`'s `class_id()` canonicalized labels through
`skills.skill_system._canonical()` — a function built for fuzzy
skill-trigger matching that intentionally collapses synonyms (e.g.
`creeper`/`zombie`/`skeleton` all map to the shared group name `mob`).
Reused for class-ID resolution, it silently misfiled every mob
detection under a nonexistent `mob` class instead of its own real
trained id, corrupting labels on every survey-saved frame containing
one — confirmed and regression-tested against the fix. Consolidated
three separate, drifted-apart copies of the class list (the stale
hardcoded one, `claude_autolabel.py`'s import of it, and a fallback
snapshot in `core/feature_extractor.py`) into one real source of truth:
new `core/class_registry.py`, backed directly by `data.yaml`.

Why it matters: this is what actually answers "can it learn about
things it's never seen" — before this, the answer was no, structurally,
regardless of how well the survey/retrain loop ran. It still isn't
"detects blazes next session" — a class discovered from one encounter
has a handful of examples, nowhere near enough to generalize, and needs
repeated exposure across sessions like every one of the current 53
classes did. And it doesn't create Nether/End exposure on its own; the
agent still has to actually get there.

File(s): `AKSUMAEL/core/class_registry.py` (new), `AKSUMAEL/core/llm_router.py`,
`AKSUMAEL/tools/claude_autolabel.py`, `AKSUMAEL/tools/yolo_finetune.py`,
`AKSUMAEL/core/feature_extractor.py`.

---

### Fri Jul 17 2026 (cont'd)
**[CLEANUP] — Removed Raspberry Pi / 7" monitor / I2C joystick references**

What: The rig migrated from a Raspberry Pi 4 to a laptop (HP Victus,
RTX 4050) a while back — `config.py`'s own header already documented
this — but `AKSUMAEL/README.md`, `install.sh`, and `config.py` still
described the old Pi-based setup: GPIO-pin UART wiring, `raspi-config`/
`/boot/config.txt` steps, a 7" Pi display for the labeling UI, and an
I2C joystick fallback, none of which are physically present anymore.
Set `ENABLE_I2C_JOY = False` in `config.py` (the code already handles
it being off cleanly — no logic change needed). Rewrote the
architecture diagram, hardware table, and wiring section in
`AKSUMAEL/README.md` to describe the actual current path (capture card
+ FTDI USB-TTL adapter → laptop → KB2040); dropped the Pi setup and
joystick steps from the Day 1 test plan and the 7" monitor claim from
the labeling UI section. Trimmed the same dead steps out of
`install.sh` (raspi-config/UART-enable, serial0/ttyS0 fix, gpio/i2c
group grants, i2c-tools package, I2C joystick hardware check) and
renumbered it. Deleted `tools/joystick_harness.py` (manual test script
for hardware that's gone) and its dangling "what to run next" reference
in `tools/kb2040_test.py`.

Why it matters: docs and an installer that describe hardware you don't
have are worse than no docs — they actively mislead the next setup
attempt (or the next re-read of this repo) into chasing wiring/config
steps that don't apply to the actual rig.

File(s): `AKSUMAEL/config.py`, `AKSUMAEL/README.md`, `AKSUMAEL/install.sh`,
`AKSUMAEL/tools/kb2040_test.py`; deleted `AKSUMAEL/tools/joystick_harness.py`.

---

### Tue Jul 14 2026
**[FIX/REFACTOR] — Train/inference VRAM race fixed, LLM routing centralized**

What: Three commits closing out the training-infrastructure work. (1)
`1586032` — AutoTrainer was only stopping mesh-llm before spawning a
training subprocess, not AKSUMAEL itself, so the two still competed for
GPU/VRAM; now both stop first, and the training lockfile is written
*before* either service stops so the watcher waits instead of racing
training for the GPU if it tries to restart mid-run. (2) `c41a75d` —
autotrain was stopping mesh-llm for training but never restarting it
afterward, leaving the local-LLM tier dead until someone noticed and
restarted it by hand; also added a 422 fallback in `vision_brain.py`
(some text-only local models reject multimodal payloads with 422
instead of the 400 the retry logic already handled). (3) `b73425b` —
centralized every local/Gemini/Claude call in the codebase behind one
choke point, `core/llm_router.route_llm_call()`: local mesh-llm primary
for everything, Gemini pinged in the background every 15th successful
local call purely for availability monitoring (never blocks, never
overrides a good local response), Claude tried only if both local and
Gemini fail.

Why it matters: this is the same VRAM-contention class of bug as
`7d939d5`/`4f3e6bc`/`0cb4e67` the day before — training and inference
fighting over one GPU — closed out by making the stop/restart symmetric
instead of half-done. The router centralization replaced seven
near-duplicate local/Gemini/Claude call implementations (one per module)
with a single tested one, which is what made the Jul 17 fix possible as
a one-file change instead of a seven-file hunt.

File(s): `AKSUMAEL/core/llm_router.py` (new), `AKSUMAEL/core/vision_brain.py`,
`AKSUMAEL/behaviors/{chest_manager,inventory_reader}.py`,
`AKSUMAEL/core/{cognitive,curriculum,code_skill_generator}.py`,
`AKSUMAEL/axon/command_parser.py`, `AKSUMAEL/config.py`.

---

### Mon Jul 13 2026
**[FEAT] — Hive coordinator, voice control, neural policy, 3-tier LLM routing, unattended-ops hardening, 50-epoch retrain**

What: Second full day of iteration, roughly three threads running in
parallel. New subsystems: `mastermind/` — an MQTT-based hive coordinator
(`coordinator.py`) plus a per-agent sidecar (`agent_client.py`) so
multiple AKSUMAEL instances could eventually be orchestrated as one hive,
goal assignments delivered through the same injected-goals queue Axon
uses; `axon/` — an offline voice hub (local Whisper wake-word detection,
a regex-first/Claude-fallback command parser, offline TTS) delivering
commands into `data/injected_goals.json`; `core/neural_policy.py` +
`core/rl_trainer.py` + `core/policy_blender.py` — a PPO-trained low-level
policy that blends with the rule-based skill/FSM decision, off by
default. Operational hardening: `f42d69a` added capture-thread retry on
a missing capture card, a hotplug watchdog, and the `/tmp/aksumael_health.txt`
status file; `84f8c86` shipped the first (pre-centralization) version of
3-tier LLM routing; `0a7a18d` found and fixed the "thinking model" token-
starvation bug (see Jul 17) on six call sites; `a044914` rerouted six
modules that called `api.anthropic.com` directly with no fallback (they
broke outright once the Claude key ran out of credit) through the local
tier instead. Training infra: a `/tmp/aksumael_training.lock` file so
AutoTrainer's own training subprocess can't collide with a separately-
launched run (`7d939d5`), fixed so the startup wait checks the lock
owner's actual liveness instead of a fixed timeout shorter than a real
~100min run (`4f3e6bc`), a VRAM/inventory-timing/goal-loop pass
(`0cb4e67`), and a ConnectionResetError chase that turned out to be a
second CUDA context from DataLoader workers, not a transient pipe glitch
(`a6934e1`) — closed out with a clean 50-epoch YOLOv8s GPU retrain
(`11259d0`, mAP50=0.767, mAP50-95=0.626 on 53 classes / 3459 train
images), deployed and committed (`ca3076c`).

Why it matters: `5510d8a`'s session-state commit is worth calling out on
its own — it documents a live audit run where the vision-LLM pipeline
was down on *all three tiers simultaneously* (local mesh-llm stopped,
Gemini 403, Claude 400 credit balance) and the training lock correctly
blocked a collision attempt mid-audit. That's exactly the total-failure
case `route_llm_call()` exists to survive gracefully, hit for real on day
two.

File(s): `AKSUMAEL/mastermind/`, `AKSUMAEL/axon/`, `AKSUMAEL/core/neural_policy.py`,
`AKSUMAEL/core/rl_trainer.py`, `AKSUMAEL/core/policy_blender.py`,
`AKSUMAEL/core/{capture,vision_brain,cognitive}.py`, `AKSUMAEL/data/models/aksumael_mc.pt`,
~8,200 dataset frames (image+label pairs) added under `AKSUMAEL/data/yolo_dataset/`.

---

### Sun Jul 12 2026
**[FEAT] — Self-improving architecture overhaul: skills, world model, planner, episode memory, curriculum, multi-env layer**

What: The single largest day in the repo — 32 commits, the jump from a
basic capture→YOLO→act loop to essentially the current architecture.
Headline commit `82fad8c` landed in three phases: (1) foundation — Voyager-
style skill pre/postcondition verification with auto-blacklisting on
repeated failure, `WorldModel` spatial chunk memory (`nearest_ore()`,
threat TTL expiry), age-tracked goal retirement logged to
`data/memory/retired_goals.jsonl`; (2) compositional planning —
`core/planner.py` (HTN-style tech-tree planner), `core/episode_memory.py`
(JARVIS-1-style episodic recall — past attempts at a similar goal fed
into the LLM prompt), `core/code_skill_generator.py` (optional LLM-
generated Python skills, off by default given the risk of auto-executing
generated code); (3) self-improving loop — `core/curriculum.py` (suggests
the next goal when the goal stack goes idle), `skills/skill_system.py`
`evolve_skills()` (marks proven skills, blacklists chronic failures,
merges duplicates), and a real LLM-backed inner monologue instead of a
template string. Same day, `3db7251` added a multi-environment adapter
layer (`core/environment.py` ABC + `core/env_registry.py`) with
Minecraft/Fallout76/driving/robocar adapters so the planner/curriculum/
episode-memory core isn't Minecraft-specific, even though `core/runtime.py`
still drives Minecraft directly today. The rest of the day was rapid live-
iteration on top of that foundation: adaptive 270°/360° scan sweeps;
several rounds of vision-provider cost/quality tuning (Gemini primary →
Claude Haiku fallback → Claude-Haiku-only → Gemini vision + Haiku
inventory, chasing a Gemini 403); a full inventory reader + crafting
rewrite (`1211998`, `1d53d13`, `2f36f41` — all recipe variants, 2x2/3x3
crafting, slot-aware reads) plus chest interaction at base (`fef9a7f`);
goal-aware skill gating, craft-goal auto-push, and anti-stuck/CARRY
logic (`f5830f3`, `b4265f3`, `75fa596`); GPU auto-detection and explicit
device selection for YOLO train/inference after finding it hardcoded to
CPU (`1cb6400`, `d61f5d3`); docs stripped of Pi-only assumptions to
reflect the actual Victus/RTX 4050 dev platform (`ff4929d`); and four
separate passes purging YOLO-label-contaminated ("HUD-contaminated")
junk skills, ending with `emerald_ore` skill creation blocked outright
since the current biome can't generate it (`42fb8dd` — the same
`_UNSUPPORTED_ORE_LABELS` guard later seen hard-coded in `core/fsm.py`).

Why it matters: this is the day AKSUMAEL stopped being a scripted loop
and became the system documented in `AKSUMAEL/README.md` — the skill
verification/retirement/evolution triad plus curriculum-driven goal
selection is what "self-improving" actually refers to. It's also the day
the biggest latent bugs got introduced (the token-budget-for-thinking-
models issue that took until Jul 13/17 to fully chase down started here,
with the local model's chain-of-thought behavior not yet accounted for).

File(s): `AKSUMAEL/core/{planner,episode_memory,code_skill_generator,curriculum,cognitive,world_model,environment,env_registry}.py`,
`AKSUMAEL/environments/`, `AKSUMAEL/data/envs/*.yaml`,
`AKSUMAEL/skills/skill_system.py`, `AKSUMAEL/behaviors/{inventory_reader,crafting,chest_manager}.py`,
`AKSUMAEL/memory/goals.py`, ~2,600 dataset frames (image+label pairs) added under `AKSUMAEL/data/yolo_dataset/`.

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
