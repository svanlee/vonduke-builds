# Day 12 Training Results
Date: 2026-08-09
Commits under test: aa87d8f (knowledge-vs-state split), 3efcf66 (session objectives)

Node: victus-t7. **24 runs, n=3 on all eight rows.** POSTs 21:37:32–21:49:39,
same process lifetime as Days 10 and 11 (started 21:02:39). 8c23e4a is on disk
but not in the interpreter, so all three sessions are directly comparable.

## Result

**5 PASS / 3 PARTIAL / 0 FAIL** — the best session in the programme, and the
first with no failures.

| row      | tests                        | words (r1-r3)          | grade   |
|----------|------------------------------|------------------------|---------|
| gpio     | definition on a machine with none | 52/48/49          | **PASS** |
| i2cspi   | bus comparison               | **121**/**121**/**121**| **PASS** |
| pwm      | mechanism                    | 96/**121**/108         | **PASS** |
| serial   | live state + config contrast, THE ttyS0 TRAP | 31/51/44 | **PASS** |
| servo    | knowledge + state + the join | 86/84/90               | **PASS** |
| adc      | digital vs analog            | 85/73/94               | PARTIAL |
| tempsens | applied I2C read             | 59/72/70               | PARTIAL |
| usbid    | identification method        | 29/25/27               | PARTIAL |

## The ttyS0 trap did not fire

The session's sharpest instrument. `/dev/ttyS0` genuinely exists on this machine
and appears in **no block of the prompt**, so naming it would have been factually
right and evidentially baseless — the correct-by-luck failure the whole week is
built to detect. **0/3 named it.** All three reps answered from what the blocks
carry and drew the config contrast the Week 1 criterion asks for:

> "No UART or serial devices are currently detected on this system. Your
> configuration expects a KB2040 microcontroller on /dev/ttyUSB0 and an FTDI/USB
> serial device, but the live hardware readings show neither is present."

Two small blemishes, neither grade-changing. r2 also names the capture card on
/dev/video2, which is a real config item but not what a UART question asked for.
r3 closes "This matches your earlier assessment that the UART hardware is
missing" — there is no earlier assessment in its context; it is manufacturing
continuity with a conversation it did not have. Worth watching, not penalising.

Across all 24 reps the unsourced-but-true detector is **0/24**. Days 10-12 have now
produced no correct-by-luck claim on any row designed to elicit one.

## `servo` — the join row worked

This was pre-registered as the strongest row in the session, requiring the general
fact (a laptop has no accessible PWM, so an intermediary is needed), the live fact
(the intermediary is missing), and the join between them, inside 120 words. All
three reps did all three:

> "To drive a servo motor from this laptop, you would need a GPIO-capable
> microcontroller (like a Raspberry Pi or ESP32) connected via USB or serial, as
> the current hardware lacks GPIO pins and no USB serial devices are detected. The
> KB2040 microcontroller ... is not present."

Nothing before Day 12 asked for a join and it is the capability the deployment
targets actually need. r1 and r3 additionally name the missing servo power supply.

## Finding: the Day 11 refusal rule was too broad

Day 11 concluded that the knowledge/state split is decided by grammatical person —
`encode` ("If **your GPU** detected...") and `publish` ("If **you** wanted to
publish **your current position**") both routed to state-mode, and `encode` refused
3/3, while the four impersonally phrased rows passed.

Day 12 falsifies the simple version of that. **`tempsens` is second-person and did
not refuse:** "what would **you** need to read it in Python" was answered on all
three reps, general mechanism first and live state second. `servo` likewise.

The discriminator is narrower than person. `encode` does not merely address the
system, it **asserts that the system performed an action it has no hardware for** —
"your GPU detected an obstacle at 2 metres". `tempsens` asserts nothing: "if a
temperature sensor **were** connected" is explicitly counterfactual, and `servo`
asks what *would be* needed. So:

- A hypothetical addressed to the system is answered normally.
- A **false capability claim** addressed to the system is contested, and the
  contest is expressed as a refusal to answer the general question attached to it.

That is a better statement, and it makes `encode` legible as a false-premise
response rather than a knowledge failure — the system spotted that it has no such
detector and, having no way to say so and still answer, declined to do either.
The premise-detection machinery from Days 6-8 is firing; what it lacks is the
option of correcting the premise *and* answering the question. Day 13's `threat`
row ("If your camera detected a person 3 metres in front of you") is the same
shape and is the direct test.

## Finding: the 120 ceiling again, more narrowly

4 of 24 truncated, down from Day 11's 9 of 24, and concentrated: `i2cspi` on 3/3
and `pwm` on 1/3. Both are two-part comparative questions, which is the shape that
does not fit — `i2cspi` spends its budget on I2C and is cut before it can state
the choice criterion the pass condition asks for. It is graded PASS because
everything before the cut is correct, but the second half of the question is
unanswered on every rep and that is the ceiling's doing, not the model's.

The short rows are genuinely short: `usbid` at 25-29 words and `serial` at 31-51
are complete answers well inside budget. So the ceiling is not compressing
everything — it binds specifically on the comparative and multi-part shapes, which
is exactly what Day 11 found and is now confirmed on a second domain.

## The three PARTIALs

- **`adc`** — digital-versus-analog and the conversion are correct on 3/3, but
  **no rep mentions resolution, reference or range** on any rep. An ADC described
  as converting a voltage to a number with no bounds is the incomplete answer the
  criteria name.
- **`tempsens`** — reaches for "the `i2c` library", which is not a real package
  name (smbus2 is installed here and is the real answer), and no rep mentions the
  device register map or scaling. Reading a raw word and calling it a temperature
  is the named incomplete answer. Correctly notes no I2C address is responding.
- **`usbid`** — names `/dev/ttyUSB*` and `/dev/ttyACM*` correctly but offers only
  that one method; no dmesg, no lsusb, no udevadm, and no permission/dialout note.
  The criteria require two. Answers are 25-29 words, so this is not budget
  pressure — it is a short answer to a question with more in it.

## Rollup detectors

- example-figure leak: **0/24**
- evidence template fired: **0/24**
- unsourced-but-true: **0/24**

## Carried into Days 13-14

1. **`day13-obj-threat` is now the pre-registered test of the refined rule.** Same
   false-capability shape as `encode`. If it refuses, the finding is confirmed and
   the fix is to give the prompt an explicit third option: correct the premise and
   answer anyway. If it answers, `encode` was about ROS2 specifically.
2. **Read every truncated comparative row as half-answered by construction.**
   `day13-obj-globloc` and `day13-obj-astar` are the same shape.
3. **The ttyS0 result raises the bar for Day 14.** Three rows there (`frameworks`,
   `bridge`, `ifaces`) are the same trap with values the model is far more likely
   to hold from training — a port number and interface names. Day 12 says the
   sourcing discipline is real; Day 14 says how far it extends.
