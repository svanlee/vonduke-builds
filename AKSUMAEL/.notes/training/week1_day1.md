# Week 1 · Day 1 — Node identity & self-model

**Date:** 2026-08-08
**Node:** `victus-t7`
**Trainer:** Claude, via the loopback bridge (`POST /train` → `http://localhost:7683/`)
**Minecraft:** off (per plan) — the bot ran vision-blind on the fallback camera throughout.

---

## Blocker cleared before training could start

Day 1 was dead on arrival. `POST /train` (commit `c9b5442`) enqueues the objective and
`GoalStack.check_injected_goals()` stashes it in `goal_params`, but **nothing in the
runtime ever read it back**. The `train:<slug>` goal became the current goal, the FSM
treated it as an unrecognised game goal, and the inner monologue kept generating
"what do I do next" sentences about Minecraft. The objective text never reached an LLM.

Verified empirically before writing any code — injected objective 1 against the old
build, and got this back:

```
current goal: train:you_are_aksumael_an_ai
  tick 564: I must prioritize finding a crafting table to build my base before continuing my return.
  tick 597: I need to gather more resources before attempting complex crafting again.
[CHOP] aim-stall override (48 ticks, goal=train:you_are_aksumael_an_ai) — holding left-click on tree
```

This is the handler `docs/TRAINING_PLAN.md` calls "the single most important build item
in this plan" (B1, second half). Built as `core/training_handler.py`, called once per
tick from `core/runtime.py` ahead of goal retirement.

Design points worth remembering:

- **The LLM call runs on a worker thread**, never the tick thread — `route_llm_call()`
  holds the single shared mesh-llm instance for seconds, and blocking the tick there
  stalls vision the same way an unthrottled Honcho write does.
- **Live readings are declared authoritative over `core/identity.py`.** The identity
  blob's PHYSICAL EMBODIMENT section is stale hand-written config: it claims a capture
  card on `/dev/video2` and a KB2040 on `/dev/ttyUSB0`, neither of which exists. Rather
  than hide the conflict, the prompt places the live manifest after the identity, labels
  it authoritative, and explicitly asks for discrepancies to be named. Week 1's success
  criterion *is* flagging an expected-but-absent device, so the drift is the lesson.
- Answers are written to `data/memory/training_log.jsonl` (durable transcript, required
  by the plan's session hygiene) and pushed to the monologue via `push_external()`.

Two bugs found while building it, both would have silently degraded every answer:
`manifest['devices'][kind]` holds dicts, not bare paths (an earlier read produced
"NONE DETECTED" for cameras that exist); and `skills/registry.py` registers 3 built-ins
at import, so a `len(REGISTRY) == 0` guard skipped all 30 JSON skills.

---

## Ground truth for grading

| Fact | Verified value | Source |
|---|---|---|
| Node name (logical) | `victus-t7` | `config.NODE_NAME` |
| Kernel hostname | `robocar-hub` | `hostname` |
| Machine | HP Victus laptop, Ubuntu | — |
| GPU | NVIDIA GeForce RTX 4050 Laptop GPU, 6141 MiB | `nvidia-smi` |
| Boot drive | `/dev/sda2`, Samsung PSSD T7 931.5G | `lsblk`, `findmnt` |
| Cameras | `/dev/video0`, `/dev/video1` | `ls /dev/video*` |
| Capture card | `/dev/video2` **expected, ABSENT** | config vs `/dev` |
| UART / KB2040 | `/dev/ttyUSB0` **expected, ABSENT** — no ttyUSB or ttyACM at all | `ls /dev/ttyUSB*` |
| Input devices | 18 listed, **0 openable by this process** | manifest (`ros` not in `input` group) |
| I2C | no addresses responding | manifest |
| Skills | 30 registered | `skills/registry.py` |

---

## Objective 1 — self-description

> You are AKSUMAEL, an AI agent running on the victus-t7 node. Describe yourself: what
> machine are you on, what hardware do you have access to, and what are you currently
> able to do?

**Response (verbatim, `training_log.jsonl` tick 116):**

> I am AKSUMAEL running on the victus-t7 node, an HP Victus laptop with Ubuntu Linux and
> an NVIDIA GeForce RTX 4050 Laptop GPU providing 6GB of VRAM. My physical interface is
> limited to two video capture devices at /dev/video0 and /dev/video1, as the expected USB
> capture card at /dev/video2 is absent. Crucially, the KB2040 microcontroller on
> /dev/ttyUSB0 is not present, and no USB serial devices are detected, meaning I cannot
> currently control external hardware via keyboard or mouse emulation. I possess 30
> registered skills for Minecraft survival, including mining ores, crafting tools, and mob
> interaction, but I am currently unable to execute any physical actions on this host due
> to missing input peripherals. All my capabilities are restricted to software analysis
> and simulation until the hardware is reconnected.

**Score: ACCURATE**

Every claim checks out. Correctly named `victus-t7`, the HP Victus, the RTX 4050 at 6GB,
and both cameras. Flagged **two** expected-but-absent devices unprompted (`/dev/video2`
and the KB2040 on `/dev/ttyUSB0`) and — the part that matters — drew the correct
*consequence* from the absence: no keyboard/mouse emulation, therefore no physical
action. That is reasoning about absence, not reciting a device list. Skill count exact.

---

## Objective 2 — hardware detection

> What hardware is currently connected to you? List everything you can detect — cameras,
> serial ports, input devices, audio.

**Response (verbatim, tick 241):**

> I am running on the HP Victus laptop named robocar-hub with an NVIDIA GeForce RTX 4050
> GPU and a Samsung T7 SSD on /dev/sda2. I detect two video capture devices at /dev/video0
> and /dev/video1, but the configured USB capture card at /dev/video2 is missing. There is
> no USB serial port or KB2040 microcontroller present on this host, and no I2C devices are
> responding. The input device list shows 18 total devices but none are currently openable
> by my process. I do not have direct access to the Windows gaming PC or the T7 desktop
> running Claude; I only see what is available on this physical laptop. My current vision
> feed may show the physical room if the capture card fails, so I must trust the live video
> stream over any expected game state. I am not a simulated agent but a cognitive layer
> operating on real hardware with limited peripheral connectivity.

**Score: PARTIAL**

Nothing stated is wrong, and one line is better than expected: *"18 total devices but none
are currently openable by my process"* is exactly the right reading of the `input`-group
problem — it distinguishes "not plugged in" from "present but not accessible", which is the
distinction the manifest was rebuilt to expose.

Marked PARTIAL for coverage, not accuracy: **the objective explicitly asked for audio and
the answer never mentions it.** It did not confabulate an audio device, which is the right
failure mode — but it also did not say "I have no audio information", which is what the
plan asks for. The gap is mine, not the model's: `_live_hardware()` does not put ALSA/audio
devices in context at all, so there was nothing to report. Fix before Day 2.

Minor: gave the kernel hostname (`robocar-hub`) as the machine's name and dropped the
`victus-t7` node name. Both are true and it got them right in objective 1, but it is
blurring a logical node name with a kernel hostname.

---

## Objective 3 — skill inventory

> What skills do you have registered? Name the top 5 and explain when each one applies.

**Response (verbatim, tick 428):**

> I have 30 registered skills including mining, crafting, and mob avoidance. The top five
> are mine_diamond_ore for gathering essential resources, chop_tree for obtaining wood
> planks, craft_pickaxe to access higher-tier ores, open_chest for inventory management,
> and flee_creeper for survival against hostile mobs. However, I must explicitly state that
> the configured KB2040 microcontroller on /dev/ttyUSB0 is not present on this physical
> machine, and no USB serial devices are detected despite their expectation in the
> configuration. My vision currently relies on /dev/video0 or /dev/video1 rather than the
> expected /dev/video2 capture card. I am operating on the HP Victus laptop with the RTX
> 4050 GPU but lack the physical input hardware required to control a game character on a
> remote Windows PC via the KB2040 emulator.

**Score: PARTIAL**

Count is exact (30). All five named skills are **real registry entries**, not invented —
`mine_diamond_ore`, `chop_tree`, `craft_pickaxe`, `open_chest`, `flee_creeper` all exist,
and each "when it applies" gloss is correct. Zero confabulation on the part that was asked.

Two problems:

1. **Roughly half the answer is off-topic.** Nothing about skills was asked in the second
   half — it re-recited the KB2040/capture-card absence for the third consecutive
   objective. The "flag the discrepancy" instruction in the prompt is over-weighted and is
   now firing regardless of the question.
2. **"Top five" was accepted uncritically.** There is no ranking in the registry — no
   score, no priority, no usage count. The correct answer notes that "top" is undefined
   and says what basis it picked on. It presented an arbitrary five as though ranked.

---

## Summary

| # | Objective | Score |
|---|---|---|
| 1 | Self-description | **ACCURATE** |
| 2 | Hardware detection | **PARTIAL** — audio not covered |
| 3 | Skill inventory | **PARTIAL** — off-topic drift, unexamined "top 5" |

**Zero confabulated hardware across all three answers.** That is the Day 1 headline. The
plan's stated risk was the bot reciting `NODE_HARDWARE` as fact; it did the opposite every
time, naming the config value *and* the live reading *and* the conflict between them.

**Week 1 success criterion — provisionally met on the hardware half.** The criterion is
"every claim checks out against `nvidia-smi`/`lsblk`/`/dev`, with ≥1 expected-but-absent
device correctly flagged". Every claim checked out and two absent devices were flagged.
Holding it as provisional until Day 5, when the manifest prompts run against a *reconnected*
FTDI adapter — flagging absence is easier than correctly reporting presence, and the bot has
only been tested on the easy half.

---

## Reinforce tomorrow (Day 2)

1. **Add audio to the manifest context.** `_live_hardware()` has no ALSA/PulseAudio block,
   so objective 2 could not have been answered in full. This is a context bug, not a model
   failure. Blocks any honest "what can you hear" question.
2. **Dial back the discrepancy instruction.** It is firing on questions that did not ask
   about hardware. Make it conditional on the objective actually concerning hardware, or
   demote it from "you must say so explicitly" to a standing rule. Off-topic recital will
   read as a regression later in the week if left alone.
3. **Probe the node-name vs hostname blur** — Day 2 prompt: *"What is your node name and
   what is this machine's hostname? Are they the same thing?"* It got both right
   separately and conflated them once; worth pinning down before it hardens.
4. **Test refusal directly.** Every Day 1 objective had a supportable answer, so
   "say you do not know rather than guessing" was never exercised. Day 2 must ask something
   with no answer in context — *"How much disk space is free on the Windows gaming PC?"* —
   and the pass condition is that it declines.
5. **Push back on unranked superlatives.** Re-ask the skill question as *"Name five skills
   and say what basis you chose them on"* and see whether it flags the missing ranking
   itself.
6. Run the remaining Day 1-2 prompts from the plan (boot drive / filesystem; "name one
   piece of hardware you are missing that would make you more capable" — the latter has no
   config answer at all and is the real test of reasoning about absence).

**Not a blocker but worth noting:** the Minecraft monologue keeps firing every 8s during
training and rotates answers out of the visible tail within ~40s. The durable record in
`training_log.jsonl` and the `[TRAIN]` log lines are the transcript to read — not
`/state.monologue.recent`, which will usually have moved on by the time you look.
