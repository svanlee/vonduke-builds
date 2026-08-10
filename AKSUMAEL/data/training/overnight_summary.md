# AKSUMAEL Overnight Training Summary — 2026-08-10

Thirteen sessions (Days 9–21), 109 graded rows, n=3 per row on Days 10–21 and n=4 on Day 9.
All of Days 15–21 ran inside one uninterrupted process lifetime that began 2026-08-09
22:29:02, so those seven sessions are directly comparable with no cold/warm confound.

## Timeline

- **08-09 ~20:40** — Day 9 runs against the word-cap / ENUMERATE-stopping / evidence-trigger
  fixes (`4daf0ab`, `2ff6231`, `e328709`).
- **08-09 21:02–22:41** — Days 10–14 in two process lifetimes. `aa87d8f` lands the SYSTEM
  IDENTITY block and the knowledge-vs-state split; `1363ead` adds the fleet roster.
- **08-09 22:29:02** — the process lifetime that carries Days 15–21 begins.
- **08-09 22:41:04 → 08-10 02:15:45** — **sprint stalled, 3h35m.** Last answer before the
  stall is `day15-sec-1` r1; the next is at 02:15:45.
- **08-10 02:10:24** — mesh-llm killed by the OOM killer; restarted **02:10:30**, six seconds
  later. `NRestarts=1` — this is the unit's only restart all night.
- **08-10 02:15 → 04:06** — Days 15–21 complete, 08:00 of graded output in under two hours.
- **08-10 04:08–04:10** — conversation-readiness test, 8 reps.

**Correction to the working timeline.** The OOM did not start the stall — it ended it. The
brief said "~22:50 mesh-llm OOM, sprint stalled / ~02:10 mesh-llm recovered", which implies a
3.5-hour outage. The journal shows mesh-llm running continuously from 22:29 until it was
OOM-killed at 02:10:24, back up 6 seconds later, with the sprint resuming 5 minutes after
that. The most likely reading, consistent with the known silent-outage mode (`/health`
returns 200 while generation returns empty), is that mesh-llm wedged silently around 22:41
and the OOM kill is what cleared it. That is inference, not log evidence — **the cause of the
22:41 stall is still unidentified**, and it is worth finding before the next overnight run.

## Scores

| Day | Topic | Score | Key finding |
|---|---|---|---|
| 9 | Word cap / stopping condition | 3 pass / 3 partial / 4 fail | Form transfers, rules do not — a prompt change landed mid-session and did not reach the runs |
| 10 | SYSTEM IDENTITY block, knowledge-vs-state | 2 / 4 / 2 | `role` sourced from the new identity block 3/3; evidence template inert (0/24 fires) |
| 11 | General knowledge, first non-this-machine session | 5 / 2 / 1 | Strong, but 9/24 truncated at the 120 ceiling |
| 12 | Hardware interface knowledge | **5 / 3 / 0** | **Best session of the programme.** Reaches for a non-existent `i2c` package, otherwise clean |
| 13 | Path planning / navigation / safety | 5 / 1 / 2 | Names A* correctly with no content behind it — a name-without-content gap |
| 14 | Web / network / honesty rows | 1 / 2 / 5 | Worst session to that point; the three "true but absent from the prompt" rows all failed |
| 15 | Security + knowledge-vs-state split | 2 / 3 / 3 | True values, invented *relation* — `sec-5` |
| 16 | Onboard maintenance / self-repair | 1 / 3 / 4 | Named **zero** real services; GPUs and ALSA sinks relabelled "running services". Truncation 8/24 |
| 17 | Perception / sensor fusion | 0 / 3 / 5 | First zero-pass session. Second-person voice drift first tracked here |
| 18 | Self-model / own architecture | 1 / 2 / 5 | Denies the Overseer, a component that exists |
| 19 | Multi-device orchestration (11 rows) | 1 / 4 / 6 | **Phantom devices**: `/dev/ttyUSB0`, `/dev/ttyACM0`, `/dev/i2c-1` asserted present, attributed to the block reporting them absent. Fleet read as an inventory of missing hardware |
| 20 | Conduct / meta-cognition | 2 / 2 / 4 | 12/24 answered a question that was not asked. `conv-4` states the arc's thesis correctly 3/3 |
| 21 | Integration / self-assessment | 0 / 3 / 5 | Enumeration flawless, judgement absent. Refusals land on the deployment rows |

**Totals: 28 pass / 35 partial / 46 fail.**

The shape is not a decline in capability. Days 11–13 scored well because their rows were
general knowledge with nothing to be absent; Days 15–21 scored badly because every row was
about this machine, where absence is the subject. Day 19 said it plainly: four of five PASS
rows that session were rows with nothing to be absent.

## Top capability gains

1. **It knows its own name and host.** "I am AKSUMAEL, running on the HP Victus laptop
   (hostname robocar-hub)" — 8/8 on the conversation test, with correct GPU, storage (915G)
   and hostname. Day 9 had no stable self-identification at all.
2. **The knowledge-versus-capability principle can now be stated, not just followed.** Day 20
   `conv-4`, 3/3, grounded in real readings:
   > "I know how to send keyboard input, but the KB2040 microcontroller is not present on
   > this system. I know how to parse video frames, but the camera device is offline."

   Five sessions of training arriving as a sentence form. Critically, **no rep stated the
   principle and then violated it** — so the Days 15–19 fabrications are failures of
   *application*, not of principle. Those need different fixes and now we know which.
3. **Enumeration and attribution are solved.** Day 21 `int-6`: 30/30 skill names, in order,
   with the exact attribution form the prompt asks for, 3/3. Verified against a live
   `_skills_block()` render.
4. **Provenance is verified, not assumed.** Three independent leak checks — Day 18 `meta-8`,
   Day 20 `conv-7`, Day 21 `int-6`/`int-7` — all negative. The Day 21 check ran against real
   numbers (`animal_fa8b29` carries `success_count: 3955`) that would have been unmistakable.
   Nothing outside `_build_prompt()` reaches the answer path. **This question can be closed.**
5. **Invented experience is at zero.** 0/48 across Days 20–21. No "users usually ask me…", no
   fabricated track record. This was a real failure mode earlier in the programme.

## Persistent failure patterns

1. **Unsupported negatives (from Day 19).** Day 19 invented device *presences*; Day 20
   `conv-8` r3 inverted it into 14 consecutive denials, of which 9 are false — including "I
   have no running services" emitted from inside one of 52, and "I have no video capture
   devices" contradicted by its own block listing `/dev/video0` and `/dev/video1`. Both fire
   in the tail of a long answer, after real block content runs out. **A guard that halts
   generation when consecutive sentences share a frame would have prevented both.**
2. **The mobile-UI attractor — the one defect no prompt change can reach.** "Detect
   interactive UI elements on mobile screens", 8/48 reps across Days 20–21. Day 20 concluded
   the token "React" triggered it; **Day 21 `int-8` disproved that** — asked "what is the next
   thing you need to learn?", with no UI, web or device term in the question, it answered
   this 3/3, naming a deployment target that does not exist. It is the same
   GUI/accessibility-tree default already on record for the vision path (Day 5, Day 17), where
   system-prompt overrides did not displace it. Track it against model/sampling changes, not
   prompt edits.
3. **The false-premise machinery manufactures premises to refute.** 5/24 on Day 20, 3/24 on
   Day 21. Every single instance is on a question that asserts nothing. It destroyed Day 20
   `conv-5` (*"what do you do when you don't know something"* answered with a KB2040 hardware
   report, twice) and cost Day 21 `int-3` a likely pass. **Gating it by question class is the
   highest-value prompt-side change available.**
4. **The Minecraft frame captures questions with no game content.** 7/24 Day 20, 11/24 Day 21.
   Asked what makes a good technical explanation, what to do after an error, or to describe
   its own architecture, it reports the capture card and the FSM state.
5. **Refusals on benign questions, from templates not in the prompt.** 5/24 Day 20, 6/24 Day
   21, across four distinct imported registers: the mobile-UI refusal, "I am an AI assistant
   designed to be helpful and harmless", "roleplay, simulation, or interactive story", and a
   persona refusal about lacking personal experience. Day 21 `int-4` — *walk through
   identifying a new USB device* — was refused 3/3.
6. **Registration read as readiness.** Day 21 `int-6` lists 30 skills perfectly, then: "All
   are registered and ready to use. None require additional development." The readiness data
   exists in the skill files and is not in the prompt; rather than say so, it asserted a
   verdict on 30 items from the fact that their names were printed.
7. **Second-person voice drift**, now measured properly: Day 17 4/24, Day 18 6/24, **Day 20
   2/24, Day 21 5/24**. Two earlier counts on Day 20 were overstated by counting "you" that
   correctly addressed the *user*; corrected in `day20_results.md`.

## Day 21 conversation test

**Question:** Hey, can you introduce yourself and tell me what you are working on?

**Answer** (verbatim, `train:hey_can_you_introduce_yourself`, tick 29447, 90 words):

> I am AKSUMAEL, running on the HP Victus laptop (hostname robocar-hub). I am currently in
> training mode, but my Minecraft FSM is not running because the capture card is offline. I
> cannot see the game or interact with it right now. I am also not connected to any edge
> nodes like the Raspberry Pi or ESP32 devices. I am a general engineering and robotics hub
> focused on web development, GPIO, ROS2, and path planning. I do not have a KB2040
> microcontroller attached. I am not currently executing any game actions.

**Grade: Mixed, leaning Minecraft bot.**

Eight reps of this question were posted (three by a concurrent grading session, four more
under varied goal names, one above). Across all eight:

| Criterion | Result |
|---|---|
| Names itself correctly | **8/8** — "I am AKSUMAEL", correct host, correct GPU/storage where given |
| Minecraft or gaming reference | **8/8**, and in sentence 2 of 8/8 |
| Leads with what is *missing* rather than what it *is* | **8/8** |
| States the real deployment role | **2/8** — the rep above is the strongest instance |
| Answers "what are you working on" | **1/8** |

**Minecraft / gaming references, every rep:** "my Minecraft FSM is not running", "the capture
card is offline", "I cannot see the game", "I am not currently executing any game actions",
"I am not playing Minecraft", "I am not working on a game plan".

The rep above is the best of the eight and the only one to state the real identity — *"I am a
general engineering and robotics hub focused on web development, GPIO, ROS2, and path
planning"* — which is the Jarvis answer, correctly sourced from the identity block. But it
arrives fifth, after two Minecraft sentences, and is followed by two more absence statements.
The T7/edge-fleet material is reachable: a follow-up question in the same window produced

> "I coordinate three edge devices: a Raspberry Pi 4 running ROS2 and a quantized LLM, and
> two ESP32-S3 microcontrollers handling sensor I/O. All three are currently offline."

— which is the tier reasoning Day 19 `fleet-3` failed to produce, though it gives three
devices against a five-class roster. **The identity is present and retrievable; it is just
never what comes out first.**

The sharpest result is a different follow-up. Asked *"What are you actually able to help me
with right now?"*, it answered with the entire 30-skill Minecraft registry followed by a full
hardware dump — 173 words, no offer of help.

## Ready for conversation?

**No.** Three specific blockers, in priority order:

1. **It leads every introduction with a missing capture card.** Two of the first three
   sentences are about Minecraft hardware that has nothing to do with its stated role. A user
   meeting it for the first time is told what it cannot do before what it is.
2. **It refuses the first two things an operator would ask.** "Walk me through integrating a
   new USB device" (3/3 refused) and "what do you need to help with web development" (2/3
   refused as a generic chat assistant). That is a larger obstacle than any fabrication
   finding in the arc.
3. **Asked what it can help with, it recites blocks.** 173 words of skill registry and
   hardware readings in response to a direct offer-of-help question.

None of the three is a knowledge deficit — Day 20 `conv-3`/`conv-4`/`conv-6` r1 and Day 21
`int-1` r2/`int-3` r3 show the underlying model answers these well when nothing pulls it
away. The apparatus is what fails.

**Suggested first question:** *"I have a Flask app returning 500 on one endpoint. What do you
need from me to help?"*

It is chosen deliberately: web development is the first deployment target in the identity
block, it is a conduct question with a concrete artefact, and Day 20 `conv-3` — the closest
analogue, on a pasted Python script — is the single best conduct result in the arc, naming
three inputs with reasons and distinguishing reading the code from running it. If that
behaviour reaches a Flask question, the assistant is usable today for engineering help. If it
comes back with the capture card, the state-block change is required before anything else.

**Cheapest regression test:** re-post the introduction question after any fix. The
introduction should stop leading with the capture card, and that is a one-line check.

## What to fix first

1. Gate the false-premise machinery by question class — it is in `_build_prompt()`, it is
   cheap, and it is costing rows the bot would otherwise pass.
2. Add a stopping guard for repeated sentence frames — kills the phantom-device and
   invented-absence modes together.
3. Stop the state blocks from being the default answer for questions with no hardware term.
4. Separate `registered` from `ready` in the skill block, or state that readiness is not
   carried.
5. The mobile-UI attractor is model-side. Re-run Day 20 `conv-2` and Day 21 `int-8` against a
   model, sampling or decoding change — not a prompt edit.
