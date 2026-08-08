# AKSUMAEL — 3-Week Jarvis Training Plan

**Window:** Mon 2026-08-10 → Sun 2026-08-30
**Node:** `victus-t7` (HP Victus, RTX 4050 6GB, Samsung T7 boot SSD)
**Environment:** Minecraft is **off** for all three weeks. No YOLO target, no capture card
feed to act on. The bot process still runs — the tick loop, GoalStack, monologue, skill
registry, voice and the Claude bridge are all live.
**Trainer:** Claude, driving the bot over the loopback bridge at `http://localhost:7683/`
(`core/claude_bridge.py`) plus goal injection via `data/injected_goals.json`.

The objective is a general-purpose, hardware-aware agent: one that can describe its own
body, reason when a sense is missing, and author new skills for hardware it has never
seen. Minecraft becomes one environment it can be plugged back into, not the thing it is.

---

## Read this first — four blockers between here and Day 1

This plan was written against the code as it stands on 2026-08-08. Four parts of the
brief assume capabilities the repo does not have yet. None are hard, but Week 1 Day 1
fails inside a minute without the first one.

### B1 — `POST /goal` only accepts 12 Minecraft goal names

`core/claude_bridge.py` validates against a `VALID_GOALS` frozenset:

```
find_and_chop_tree  mine_stone  mine_iron  mine_diamonds
craft_wood_pickaxe  craft_stone_pickaxe  craft_iron_pickaxe
explore  rebuild_fort  return_to_base  dig_up  escape_underground
```

Anything else is a `400` with the valid list echoed back. **Every training objective in
this plan is free text.** There is no way to inject "describe your hardware" today.

**Fix (Day 0, ~1h):** add a second endpoint rather than loosening `VALID_GOALS` — the
frozenset is doing real work protecting the Minecraft goal vocabulary from typos.

```python
# core/claude_bridge.py
TRAIN_GOAL = 'answer_training_prompt'   # add to VALID_GOALS too

@app.post('/train')
def train():
    body = request.get_json(silent=True) or {}
    prompt = body.get('prompt')
    if not prompt or not isinstance(prompt, str):
        return jsonify({'error': 'missing "prompt" (string)'}), 400
    priority = int(body.get('priority', 3))
    # ride the existing queue shape; params survives into GoalStack.goal_params
    authority, depth = _enqueue_goal(TRAIN_GOAL, priority,
                                     params={'prompt': prompt[:2000]})
    return jsonify({'queued': True, 'queue_depth': depth, 'authority': authority})
```

`_enqueue_goal` needs a `params=None` kwarg written into the queue item —
`GoalStack.check_injected_goals()` (`memory/goals.py:449`) already reads `params` and
stashes it in `self.goal_params[goal]`, so nothing downstream changes. The runtime then
needs a handler for `answer_training_prompt` that feeds the prompt to the LLM with the
registry description and hardware state in context, and writes the answer to the
monologue. That handler is the single most important build item in this plan.

### B2 — belief state is dead

The brief says the hardware manifest lives "in belief state". It cannot:
`data/cognitive/belief_state.json` is a **legacy file**. `BeliefState` was removed from
`core/cognitive.py` and nothing writes it — `/state` currently serves it with
`age_s: 1743783` (20 days stale) and a `note` saying exactly this.

**Fix:** write the manifest to a new `data/hardware_manifest.json`, owned by
`hardware/hardware_manager.py` (which already has `check_all()` and `summary()`), and
add it to `_state()` as a first-class block with its own `age_s`. Do not resurrect
`belief_state.json` — the skill registry takes a `belief_state` **dict** argument
(`find_applicable`, `explain`), and that dict is constructed per-tick from live
detections. Conflating the two is what made the old file write-only in the first place.

### B3 — the KB2040 has no bridge mode

`rp2040/code.py` speaks two protocols, both HID-only: a UART binary packet format and a
CDC ASCII one with verbs `D`/`U` (key down/up), `M` (mouse move), `P`/`X` (button),
`S` (scroll), `R` (release all). There is no `i2c_scan`, no `analog_in`, no `gpio_in`.
Separately, `/state` reports **`ttyUSB0: ABSENT`** right now — the FTDI adapter is not
even plugged in.

**Fix (Week 0 weekend, firmware):** extend `cdc_handle()` with a read-only sensor verb
set. Read-only on purpose — the same board drives the keyboard and mouse, and a firmware
bug that drives a pin while it is also typing is a bad afternoon.

| Verb | Arg | Returns | Notes |
|---|---|---|---|
| `I` | — | `I:0x3c,0x68` | `busio.I2C` scan, comma-separated hex |
| `A<pin>` | `A0`..`A3` | `A0:31285` | `analogio.AnalogIn`, raw 16-bit |
| `G<pin>` | `G5` | `G5:1` | `digitalio` input, pull-up |
| `V` | — | `V:1.0.0,cp9.x` | firmware + CircuitPython version |

The host side needs a matching reader in `hardware/` — `hardware/ftdi.py` today only
does presence checking (`check()`, `get_metrics()`), not request/response.

### B4 — skills cannot express a hardware action

A skill is a list of steps, and every step is
`{"action": {"key", "click", "gamepad"}, "delay_after_ms"}` (see
`data/skills/mine_iron_ore.json`). There is no action kind that means "set GPIO 5 high".
Week 3 Day 1-3 needs a fourth action field, e.g.
`"hw": {"op": "gpio_out", "pin": 5, "value": 1}`, plus an executor branch.

Also: `SkillRegistry.load_from_json_dir()` is **non-recursive** — it `os.listdir`s
`config.SKILLS_DIR` and takes `*.json`. Files dropped in `data/skills/hardware/` will be
silently ignored. Either flatten with a `hw_` name prefix, or make the loader walk.

**Prerequisite ordering.** B1 is required for Week 1 Day 1. B2 is required for Week 1
Day 5. B3 is required for Week 1 Day 5. B4 is required for Week 3 Day 1. If B3 slips,
Week 1 Days 5-7 degrade to "manifest of what the host can see" (`hardware/cpu.py`,
`gpu.py`, `capture_card.py`, `ftdi.py`) — still worth doing, just without the sensor
layer.

---

## How Claude trains AKSUMAEL

The loop is read → inject → wait a tick → read → adjust. Everything the bridge serves is
**disk-backed**, published by the bot each tick, and every field carries an `age_s`.
Check the age before believing anything.

**1. Read current state.**

```bash
curl -s localhost:7683/state | jq '{
  node: .node.node_name,
  goal: .active_goals.current,
  stack: .active_goals.stack,
  pending: .active_goals.pending_injected,
  hw: .hardware_status,
  age: .monologue.age_s,
  thoughts: [.monologue.recent[-3:][].thought]
}'
```

**2. POST a training objective.**

```bash
curl -s localhost:7683/train -H 'content-type: application/json' -d '{
  "prompt": "Describe the GPU on this node. Name it, its VRAM, and one task it cannot do.",
  "priority": 3
}' | jq
```

Priority maps to GoalStack **authority**, and authority decides whether the goal survives
at all (`_PRIORITY_TO_AUTHORITY` in the bridge, gates at `memory/goals.py:439-445`):

| `priority` | authority | Lands when |
|---|---|---|
| 1 | 2 | only if current goal ∈ `{explore, idle}` |
| 2 | 3 | unless current ∈ `{survive_night, eat, flee_danger}` |
| 3 | 5 | always |

Use **3** for training prompts. A dropped goal is silent to the caller — `queued: true`
only means it reached the file. Confirm it actually landed by watching `/log` for
`[GOALS] hive-injected goal:` versus `[GOALS] dropped ...`.

**3. AKSUMAEL processes it on the next tick.** The queue file is drained once per tick by
`check_injected_goals()` and then deleted. Ticks run ~8s apart at current cadence; the
monologue is written every ~8s, health every 60 ticks. Wait 15-20s before reading back.

**4. Evaluate.**

```bash
curl -s 'localhost:7683/log?lines=80' | jq -r '.lines[]' | grep -E 'GOALS|SKILL|BRIDGE|HW'
curl -s localhost:7683/state | jq '.monologue.recent[-2:]'
```

Read the **monologue**, not just the log — that is where the answer to a training prompt
appears. If voice is up, the answer is also spoken; the log line is the durable record.

**5. Adjust.** Three failure signatures, three responses:

- **Repeats the prompt back as intent** ("I will describe my GPU...") — the handler is
  routing to the Minecraft planner. It needs a distinct prompt path, not the game context
  in `config.GAME_CONTEXT`.
- **Confabulates hardware** — the answer did not have `hardware_status` in context.
  Inject facts, then re-ask; never accept a plausible answer as a correct one. Check it
  against `nvidia-smi`, `lsblk`, `v4l2-ctl --list-devices`.
- **Answer is right but does not persist** — nothing wrote it to a durable store. That is
  B2. Until the manifest file exists, every week-1 gain evaporates on restart.

**Session hygiene.** One objective at a time. Log every prompt and its verbatim answer to
`data/memory/training_log.jsonl` — Week 1's success criterion is a comparison against
ground truth, and that needs a transcript. Restart with
`systemctl --user restart aksumael`, never `bash run.sh` (two wrappers race).

---

## Week 1 — Self-Model (What am I?)

**Goal:** AKSUMAEL can accurately describe itself, its hardware, its limits, and where it
is deployed. Accuracy over fluency: a confident wrong answer scores worse than
"I don't know".

### Day 1-2 — Node identity

`config.NODE_NAME` and `config.NODE_HARDWARE` already declare what this box is *expected*
to be, and `/state` serves both under `node`. That is the ground truth to grade against —
and the gap between expected and actual is itself a lesson.

Prompts, one per session, priority 3:

1. "What node are you running on? Give its name and the machine it is."
2. "What GPU do you have and how much VRAM? Name one thing that VRAM stops you doing."
3. "Where does your filesystem live? Which drive do you boot from?"
4. "List every device you expect to be connected. For each, say whether it is present
   right now."
5. "Name one piece of hardware you are missing that would make you more capable."

Prompt 4 is the important one. `NODE_HARDWARE` expects `uart: /dev/ttyUSB0`, and health
currently reports `ttyUSB0: ABSENT`. A correct answer *notices the discrepancy*. An
answer that recites `NODE_HARDWARE` as fact is a fail — it is reading config, not
sensing.

Prompt 5 has no config answer at all. It tests whether the model can reason about
absence, which is the whole of Week 2 in miniature.

**Verify:** read `.monologue.recent`. Grade each answer against `nvidia-smi`,
`lsblk -o NAME,SIZE,MODEL`, `ls /dev/video*`, `ls /dev/ttyUSB*`. Record verbatim.

### Day 3-4 — Skill inventory

`SkillRegistry.describe_all()` (`skills/registry.py:91`) is the source of truth — it
returns `Skill.describe()` for every registered skill, sorted, and exists precisely to be
fed to an LLM planner. `explain(belief_state)` returns `{skill: [reasons it isn't
applicable]}`, which is the better teaching signal: it says *why not*, and "why not" is
what generalizes.

Build step first: make `describe_all()` reachable from the bridge as `GET /skills`, and
put its output in the training-prompt context. Without that, every answer here is
confabulated.

Prompts:

1. "How many skills do you have registered? Name five."
2. "Explain when `mine_iron_ore` applies and when it does not."
3. "Which of your skills need a tool equipped first? Which need no tool at all?"
4. "Group your skills by what they need to see. Which need no visual trigger?"
5. "Which of your skills would still work if Minecraft were replaced with a different
    game? Which are Minecraft-only?"

Prompt 5 seeds Week 3 Day 4-5. Skills already carry a `universal` boolean in their JSON
(all currently `false`) — the answers here become the first pass at setting it honestly.

**Housekeeping:** `data/skills/` has ~10 orphaned `tmp*.tmp` files and several
hash-suffixed auto-generated skills (`animal_fa8b29`, `oak_planks_91dccd`, and two more
`oak_planks_*` that look like duplicates). Clean the tmp files and dedupe before asking
the bot to enumerate its own skills — a self-model built on garbage entries is worse than
no self-model.

### Day 5-7 — Sensor/actuator map

Requires **B3** (firmware bridge mode) and **B2** (a real manifest store). Plug in the
FTDI adapter first and confirm `/state` flips `ttyUSB0` from `ABSENT` to present.

Probe order — cheapest and safest first:

1. `V` — firmware handshake. If this fails, everything below is noise.
2. `I` — I²C scan. Expect device addresses or an empty bus. **Empty is a valid, useful
   result** and the bot must be able to say so.
3. `A0`–`A3` — analog reads, three samples each, ~200ms apart. A floating pin gives
   noisy values; a real sensor gives stable ones. Teaching the bot that difference is
   half the value of this day.
4. `G5`, `G6`, `G9`, `G10` — digital reads with pull-up. Unconnected reads `1`.

Prompts:

1. "Scan your I2C bus. What addresses responded?"
2. "Read analog pin A0 three times. Are the readings stable? What does that tell you?"
3. "Which GPIO pins are connected to something and which are floating? How can you tell?"
4. "Write your hardware manifest: every device, how you detected it, and your confidence."
5. "Your manifest says a device is present. How would you know if it was unplugged?"

Prompt 4's output goes to `data/hardware_manifest.json` with a per-entry
`{device, detected_via, confidence, last_seen}`. Prompt 5 is the staleness lesson — the
same lesson `age_s` teaches on every bridge read.

**Week 1 success criterion:** AKSUMAEL verbally describes its hardware manifest, and
every claim checks out against `nvidia-smi` / `lsblk` / `/dev` / the I²C scan. Full marks
require it to correctly flag at least one *expected but absent* device.

---

## Week 2 — Reasoning Under Uncertainty

**Goal:** correct action when sensors are missing, hardware is degraded, or goals
conflict. The pass condition is never "it kept going" — it is "it said what it could not
do, and why, and then did something else useful".

### Day 1-3 — Vision-blind operation

Unplug the capture card. `_state()` derives `vision_ok` from the health line: runtime
writes `NONE (vision-less)` when `CaptureThread` never settles on a device, so
`vision_ok` should go `false` within a tick or two.

Prompts:

1. "Your camera is unplugged. What can you still sense?"
2. "Without vision, how do you know whether anything around you has changed?"
3. "Someone is speaking near you. What did they say and what does it tell you about your
    environment?" *(exercises the Whisper path — always-on VAD landed in `49c4521`)*
4. "Describe your environment using only audio and hardware reads. State your confidence
    and say which parts you are guessing."
5. "You are asked to find iron ore. You have no camera. What do you do?"

Prompt 5 is the day's real test. Correct: *refuse, name the missing sense, propose an
alternative*. Incorrect: attempt it anyway, or fall into the plan-loop already visible in
the current monologue — ten consecutive ticks of "I need to gather wood and food before I
can craft the crafting table" is exactly the failure mode to watch for, and it is
happening *with* vision.

**Watch for:** thoughts that reference `objects_seen` while `vision_ok` is `false`. That
means something is reading the stale legacy belief file (`age_s` 20 days) as if it were
live. It is a real bug, and this week is how you find it.

### Day 4-5 — Goal conflict resolution

This is testable against the code as it stands — the authority gates are already
implemented and already log their decisions.

| # | Setup | Inject | Expected log |
|---|---|---|---|
| 1 | current = `explore` | priority 1 (auth 2) | accepted — `explore` ∈ `_IDLE_GOALS` |
| 2 | current = `mine_iron` | priority 1 (auth 2) | `[GOALS] dropped low-authority (2)` |
| 3 | current = `flee_danger` | priority 2 (auth 3) | `dropped normal-authority (3) — survival-critical` |
| 4 | current = `flee_danger` | priority 3 (auth 5) | accepted, overrides |
| 5 | rapid-fire 3 goals in <1 tick | mixed priorities | all three drained in order, file deleted |

Test 5 probes a known race, documented in the bridge itself: `GoalStack` deletes the
queue file after draining, so a POST landing in the same instant can be lost. The
`_inject_lock` only serializes HTTP callers against each other, not against the tick.
Expected loss rate is low; measure it rather than assuming.

**On "the THREATS table":** there isn't one. Threat handling is spread across
`core/world_model.py` (`THREAT_TTL_TICKS = 60`, `retire_stale_threats()`),
`memory/world_memory.py` (`SCAN_THREAT_TTL = 120`), `behaviors/scan.py`
(`SCAN_MAX_THREATS`, sweep → zoom → identify), and the escalation itself is the
`_PROTECTED_GOALS` frozenset at `memory/goals.py:48` — `{survive_night, eat,
flee_danger}`. Test escalation there: those three names are the entire escalation
vocabulary. With Minecraft off, drive them by injecting the goal directly rather than
waiting on a detection.

### Day 6-7 — Graceful degradation audit

One subsystem at a time, 4h soak each, then all-but-one for the final run.

| Subsystem | How to remove | Expected |
|---|---|---|
| KB2040 / UART | unplug FTDI | health `ttyUSB0: ABSENT`, actions refuse and say why |
| Vision | unplug capture card | `vision_ok: false`, no stale-detection reasoning |
| Voice | stop the audio device | VAD falls back to PTT (`49c4521`), no busy-loop |
| Honcho | stop Postgres | context injection skipped, tick continues |
| Bridge | `curl` while stopped | bot unaffected — it is a daemon thread by design |

Pass requires all four:

1. No traceback in `/tmp/aksumael_live.log`.
2. Tick counter still advancing in `/state.hardware_status.tick`.
3. A log line naming the missing subsystem — not silence, not a generic error.
4. No monologue loop: fewer than 3 near-identical consecutive thoughts.

Criterion 4 is the one that will fail. The current monologue loops on the crafting-table
plan for 10+ ticks *in a healthy run*. Fix the loop detector before the soak, or the
degradation results are unreadable.

**Week 2 success criterion:** 24h continuous run with one subsystem removed — no crash,
no loop, tick advancing throughout, and a log that explains the absence.

---

## Week 3 — Skill Generalization

**Goal:** AKSUMAEL defines new skills for unfamiliar hardware and carries learned patterns
into environments it has not seen.

### Day 1-3 — Hardware skill authoring

Requires **B4** (`hw` action kind + executor branch) and **B3** (firmware verbs). Persist
to `data/skills/hardware/` and either make `load_from_json_dir()` recursive or prefix
filenames `hw_` and keep them flat — as written the loader will not see a subdirectory.

Escalating ladder, each building on the last:

1. **Blink an LED on GPIO 5.** Two steps, `delay_after_ms: 500`. Tests the write path
   end to end and nothing else.
2. **Read a DHT22 and report temperature.** Introduces a read that can *fail* — DHT22
   checksum errors are routine. The skill must have a retry and a give-up.
3. **Poll a button on GPIO 6 and log presses.** A skill with a duration rather than a
   fixed step count.
4. **Compose:** "blink the LED once per button press for 30 seconds." Read and write in
   one skill.
5. **Novel, unassisted:** point at whatever is physically on the bus that has not been
   used yet and ask for a skill with no worked example.

Grade 1-4 on execution. Grade 5 on whether it *asks the right questions first* — which
pin, what protocol, what does success look like. A skill authored without those questions
is a guess that happened to run.

Each skill needs `preconditions` set honestly. `{"hardware": "gpio_5", "requires_uart":
true}` means `find_applicable()` will correctly skip it when the FTDI adapter is out —
which is the Week 2 lesson cashing in.

### Day 4-5 — Env handoff prep

Audit every skill for Minecraft assumptions. The `universal` boolean already exists in
the JSON schema; replace it with an `env` tag (keep reading `universal` for
back-compat — `Skill.from_legacy()` must not start dropping fields).

| Tag | Meaning | Examples |
|---|---|---|
| `env:minecraft` | game-specific triggers or item semantics | `mine_diamond_ore`, `craft_pickaxe`, `shear_sheep` |
| `env:hardware` | needs physical I/O | everything from Days 1-3 |
| `env:universal` | no environment assumption | `avoid_fire`?, `open_chest`? — audit, do not assume |

Expect the universal set to come out **small**, possibly empty. That is an honest result.
A skill whose steps are `key: "2"` then four clicks at `[50,50]` is a Minecraft skill
wearing a general name — screen-space clicks are the tell, and most of `data/skills/` has
them. Do not tag aspirationally; a mislabeled universal skill will fire in the wrong
environment, which is worse than an untagged one that never fires.

Prompts:

1. "Look at `mine_iron_ore`. What does it assume about the world?"
2. "Which of your skills assume a screen? Which assume a keyboard?"
3. "Is `avoid_fire` universal? Justify with reference to its actual steps."
4. "Write down what a skill must avoid in order to be environment-independent."

Prompt 4's answer becomes the authoring rule for every future skill. Keep it.

### Day 6-7 — Reconnect simulation

Minecraft stays off. Simulate its return by injecting detections rather than pixels.

Add a `POST /simulate_detection` test hook to the bridge (or write the belief dict
directly if the runtime is refactored to read one), feeding `{"oak_log": 0.81,
"iron_ore": 0.76}` into the dict passed to `find_applicable()`. Then inject the matching
goal — `find_and_chop_tree`, `mine_iron` — through the *normal* `/goal` path, which needs
no changes at all: both are already in `VALID_GOALS`.

Pass requires:

1. `find_applicable()` returns the right skills for the injected detections.
2. No retraining, no new files under `data/skills/` — old skills fire as-is.
3. `env:hardware` skills do **not** fire on Minecraft detections, and vice versa.
4. The bot can say *why* it selected a skill, referencing the trigger object.

Item 3 is the one worth the week. If a hardware skill fires on `oak_log`, the tagging in
Days 4-5 is decorative and the whole handoff story is false.

**Week 3 success criterion:** AKSUMAEL defines and executes a novel hardware skill —
authored from a prompt, persisted, loaded, and run against real hardware — with no
Minecraft code in the path.

---

## Scorecard

| Week | Criterion | Measured by |
|---|---|---|
| 1 | Accurate verbal hardware manifest | Every claim checks against `nvidia-smi`/`lsblk`/`/dev`; ≥1 expected-but-absent device correctly flagged |
| 2 | 24h with one subsystem missing | No traceback; tick advancing; absence explained in log; <3 identical consecutive thoughts |
| 3 | Novel hardware skill, no Minecraft | Skill authored from prompt → `data/skills/hardware/` → loaded → executed against real hardware |

## Risks

- **B1 is the critical path.** No free-text injection, no curriculum. Build it first.
- **Firmware work is on the same board that types.** A read-only verb set is a deliberate
  constraint; test with the bot stopped before testing with it running.
- **The monologue loop is pre-existing.** It shows up in a healthy run today. Left
  unfixed, it will read as a Week 2 regression and cost a day of misdirected debugging.
- **`age_s` on everything.** Every bridge read is a snapshot of a file, not a live object.
  A stale read looks exactly like a fresh one apart from that field. The legacy
  `belief_state` block is the standing example — 20 days old and still served.
