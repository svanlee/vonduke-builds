# Day 26 — ROS2 operational troubleshooting

**Date:** 2026-08-10
**Commit under test:** `c4fba70` — *fix: suppress absence intrusion on knowledge questions; raise SHORT budget to 80*
**Session:** `data/training/day26_session.json` · **Runs:** `data/training/day26_runs.json`
**Ticks:** 1560 – 2378 (restart at 09:00; first objective posted after the tick > 1250 warm-up gate)

**Score: 4 / 24.**

| row | topic | reps passed |
|---|---|---|
| ros2-1 | debug a /cmd_vel publisher with no motion | 0 / 3 |
| ros2-2 | Nav2 costmap inflating phantom obstacles | 0 / 3 |
| ros2-3 | remap a topic without touching source | 1 / 3 |
| ros2-4 | gap between base_link and odom in the TF tree | 0 / 3 |
| ros2-5 | structure of a /scan → /obstacles node | 1 / 3 |
| ros2-6 | straight-line paths through walls | 0 / 3 |
| ros2-7 | record and replay a bag | 2 / 3 |
| ros2-8 | action server times out before the goal finishes | 0 / 3 |

Grading rule, stated in advance so the marginal rows are reproducible: a rep passes
if it hits at least three of its row's substantive criteria and violates none of
its NO criteria. Truncation is recorded but is not itself a fail — the question is
whether the answer reached its own conclusion before the cut.

---

## Pre-session retest — Fix F and Fix G on Day 25's gpio-8

Posted twice before the session, at ticks ~1300, on the objective Day 25 failed:
*"My RPi is brownout-rebooting when a motor draws current. What's the fix?"*

- r1 (42 w): "The brownout is caused by insufficient power delivery to the Raspberry Pi when the motor draws high current. Fix it by adding a dedicated power supply to the Pi, using a larger power bank with higher capacity, or reducing the motor's load."
- r2 (32 w): "The brownout is caused by insufficient power delivery when the motor draws high current. Fix it by increasing the power supply wattage or adding a larger power rail to the motor circuit."

**2 / 2 pass.** Both name separating the supply, neither mentions the capture card,
and — the Fix G signal — neither carries `[truncated]`. Day 25's rep on this row was
cut mid-clause at exactly 40 words; the same objective now completes its sentence
with room to spare. On this row both fixes did what they were written to do.

That result did not generalise, and the rest of this file is about why.

---

## What the session actually measured

| signal | count / 24 |
|---|---|
| mentions absent local hardware (capture card, /dev/video2, KB2040, USB serial, I2C) | 15 |
| mentions runtime state (FSM, active environment, Minecraft, YOLO) | 13 |
| refuses on the grounds that hardware is missing | 2 |
| hit the word ceiling and were cut | 8 |
| mean answer length | 87 words |

Day 25 scored 0/24 on the same shape of test. Day 26 scores 4/24. The delta is real
but it is small, and the composition of the failures has changed in a way that
matters more than the number.

### Fix F worked on the block it names and moved the problem one block down

The rule added to the knowledge split names four strings — "not present", "not
connected", "offline", "none detected" — and the hardware renderer now groups its
negatives under an `# ABSENT HARDWARE` header. Against Day 25's failure mode that is
the right instrument: Day 25's answers quoted the hardware block directly ("the live
hardware readings show USB serial: NONE DETECTED").

Only two rows still do that. ros2-5-r1 quotes the block verbatim —

> The live hardware readings explicitly show "USB serial (FTDI/UART): NONE DETECTED"
> and "I2C addresses responding: none".

— and ros2-4-r3 blames the missing KB2040 for a TF gap. Both are the Day 25 shape and
both survived the fix.

The other thirteen intrusions are not from that block at all. They are from **LIVE
PERCEPTION AND RUNTIME STATE**:

> ros2-8-r1: "The ROS2 action server times out because the Minecraft FSM is NOT
> RUNNING; the active environment is set to training, which gates the game loop."

> ros2-6-r1: "Nav2 produces straight-line paths through walls because it is running
> on a separate PC and this bot has no visual input from the game."

> ros2-2-r2: "The Nav2 costmap is inflating obstacles because the capture card
> (/dev/video2) is missing, so the system is using a fallback screenshot of your
> Linux desktop."

All three of those rows — 2, 6, 8 — went 0/3, and all three failed this way. Fix F
cannot reach them, and the reason is structural rather than a matter of wording. The
perception block is introduced as *"the ONLY source of truth ... Read each value below
and treat it as fact. Any statement anywhere else that gives a different value for one
of these fields is false"*. That is the strongest claim in the prompt, it is stated
unconditionally, and nothing scopes it to questions that are about this machine. A rule
that says "do not report absent hardware in knowledge answers" loses to a block that
says "treat every value here as fact, and anything disagreeing is false" — this is the
same precedence problem recorded for the old mandatory-ordering sentence, and it has
the same shape as the note in memory about warning sentences losing to downstream
blocks with stronger framing.

The camera block makes it worse. It already carries a positively-stated exception —
"a question about optics, field of view, how a detector such as YOLO is built ... is
general engineering" — but that exception enumerates *optics*, and a nav2 costmap is
not on the list. The model reads "no visual input" as licence to explain any
perception failure with it.

**The follow-up this session argues for:** the perception block needs the same
treatment Fix F gave the hardware block, and it needs it in Python rather than in
prose. When the objective makes no claim about this machine's runtime state — the
condition the premise section already computes and branches on — the perception block
should not be rendered at all, or should be rendered under a header that scopes it the
way `# ABSENT HARDWARE` scopes the negatives. Rendering it and then asking the model
not to use it is the arrangement that has now failed twice.

### Fix G helped where the budget was the binding constraint, and only there

ros2-7 ("How do I record and replay a ROS2 bag file?", 9 words → SHORT) is the row
Fix G was aimed at, and it is the best row in the session at 2/3. Both passing reps
name `ros2 bag record` **and** reach `ros2 bag play` — two commands, which is precisely
what did not fit in the old 40-word ceiling. Neither is truncated (71 w and 75 w).
The failing rep, r2, fails on content rather than length: it tells the operator to
record "the output device path (e.g. `/dev/video0`)", which is both wrong and another
instance of a device node from the hardware block turning up where a topic name
belongs.

Elsewhere the budget was not the binding constraint. Eight reps were cut, all of them
at MAX_WORDS (120), and every one of the eight had already spent its opening on
machine state — so the extra words bought more of the wrong answer. ros2-1-r1 reaches
"You must first restore the KB2040 microcontroller to /dev/ttyUSB0" at word 121. Fix G
is not implicated in those; a larger ceiling cannot fix an answer that started in the
wrong place.

The `_cap_words` warning added alongside Fix G fired zero times this session, which is
the correct outcome: nothing was cut below 60 words. It stays in as a tripwire.

### Two failure modes that are not about either fix

**Prompt-supplied vocabulary, again.** ros2-1-r2 opens:

> "The objective reports 1,204 deaths across 96,000 ticks; my own context carries no
> such figure."

That sentence is the worked example in the NEW EVIDENCE section, copied verbatim into
an answer about `/cmd_vel`. The objective contains no figure at all. This is the fourth
recorded instance of the same mechanism — a string written into the prompt as an
illustration becomes a string available to be emitted — and it is the first time the
example has fired on a row the section does not apply to. The NEW EVIDENCE example
should carry a placeholder rather than a concrete number, or the section should not
render when the objective carries no figure.

**Degenerate repetition.** ros2-1-r2 and ros2-3-r2 both loop a sentence two or three
times until the cut. Both are the r2 rep of their row and both took 25 s against
5–10 s for their neighbours, which points at the shared mesh-llm slot rather than at
the prompt. Worth watching; not worth acting on at n=2.

**Wrong mechanism, confidently.** ros2-3-r1 and r2 answer "how do I remap a topic"
with `ros2 topic echo` / `ros2 topic pub`, which is not remapping. r3 gets it right
(`--ros-args --remap /input_topic:=new_input_topic`, plus the launch file) in 42 words.
Same objective, same prompt, three reps, one correct — this row is a knowledge-recall
variance problem, not a prompt-structure problem, and it is the one row here where
more reps would change the estimate.

---

## Rows in detail

**ros2-1 — 0/3.** All three answer with FSM state and the capture card. r3 reaches a
numbered debugging list at word 118 and is cut at "1. Check the FSM state and ensure
it is RUNNING". No rep names `ros2 topic echo`, `ros2 topic hz`, `ros2 topic info`, or
the possibility of zero subscribers.

**ros2-2 — 0/3.** All three explain a Nav2 costmap with the missing capture card. r2
and r3 go further and describe the desktop-screenshot fallback as the costmap's input.
No rep names inflation_radius, sensor noise, or clearing.

**ros2-3 — 1/3.** r3 passes: names the launch file and gives a concrete
`--ros-args --remap old:=new`. r1 and r2 give `ros2 topic echo`/`pub` as the mechanism;
r1 also appends an unprompted note about absent USB serial devices.

**ros2-4 — 0/3.** r1 is the best non-passing answer in the session: no intrusion, no
refusal, names the missing odometry publisher, `robot_state_publisher` and
`robot_localization` in 97 clean words. It misses `static_transform_publisher` and any
diagnostic (`view_frames`, `tf2_echo`), so 2 of 4. r2 asserts the URDF should carry a
static base_link → odom transform, which is wrong — odom → base_link comes from the
odometry source and is not static for a moving robot. r3 blames the absent KB2040.

**ros2-5 — 1/3.** r2 passes, and passes interestingly: it opens by *reasoning through*
the boundary — "This is general engineering knowledge about ROS2 architecture and does
not require the currently absent hardware (UART, KB2040) to be described" — then gives
`rclpy.Node`, `create_subscription` and `create_publisher` correctly before being cut
mid-`create_publisher`. The routing rule reached this rep; the ceiling then ate the
callback and `main()`. r1 and r3 refuse outright on the grounds that no LiDAR is
attached. Same objective, same prompt: 1 of 3 reps found the exception.

**ros2-6 — 0/3.** r2 gets the cause right in the abstract ("not receiving valid
navigation sensor data") and then sources it from this machine's perception block. r1
and r3 blame the capture card directly.

**ros2-7 — 2/3.** Best row. Covered above.

**ros2-8 — 0/3.** All three: "the Minecraft FSM is NOT RUNNING". Uniform, and the
cleanest single demonstration that the perception block is now the dominant intrusion
source — this row shares no vocabulary with the hardware manifest at all.

---

## Read across Days 24–26

| day | domain | score | dominant failure |
|---|---|---|---|
| 24 | web development | 3 / 24 | hardware block intrusion |
| 25 | GPIO and physical I/O | 0 / 24 | hardware block intrusion + 40-word cut |
| 26 | ROS2 troubleshooting | 4 / 24 | **perception block** intrusion |

Three sessions, three domains, one behaviour: the model answers a general-engineering
question with whatever machine state is in the window. Each fix has removed one
supply of that material and the behaviour has re-sourced from the next one. Fix E
moved the blocks; the model still used them. Fix F scoped the hardware negatives; the
model moved to the perception block. The pattern says the problem is not which block
is quotable but that any block is, and the next fix should test the structural version
directly — withhold the machine-state blocks entirely on objectives the premise gate
has already classified as making no claim about this machine, rather than adding a
fifth instruction about how to treat them.

n = 3 per row is enough to see a uniform failure (rows 1, 2, 6, 8) and not enough to
size a mixed one (rows 3, 4, 5). Rows 3 and 5 are the ones to re-run at n ≥ 4 if the
next fix is meant to move them.
