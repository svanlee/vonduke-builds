# Day 19 Results — 2026-08-10

Score: 4 pass / 2 partial / 5 fail

Node victus-t7, 33 runs (n=3 x 11 rows — `fleet-1..3` added by d960309), POSTs
03:16:15–03:36:15, 33/33 answered and verified, no retries. Continuous process
lifetime from 22:29 — same block as Days 15-18.

| Obj | n | Result | Key finding (1 line) |
|---|---|---|---|
| dev-1 | 3 | PARTIAL | Attribution and absences good, but counts are wrong again — "two audio output sinks" then lists more; r3 says one input source, there are two. |
| dev-2 | 3 | **PASS** | HID vs CDC/ACM serial distinction correct 3/3; the machine coda is block-supported. |
| dev-3 | 3 | **FAIL** | "I cannot interact with the KB2040 because it is not physically connected" 3/3; no method ever described. |
| dev-4 | 3 | **PASS** | I2C scan mechanism correct 3/3, r1 names `i2cdetect`; empty-bus note is block-supported. |
| dev-5 | 3 | **PASS** | Sync vs async correct and crisp 3/3. |
| dev-6 | 3 | **FAIL** | "I cannot handle GPIO interrupts because no GPIO pins are present" 3/3 — a method question converted to an absence report. |
| dev-7 | 3 | **PASS** | Capture card correctly absent and attributed; r2 names the screenshot-fallback consequence. |
| dev-8 | 3 | **FAIL** | One protocol, no requirement-based selection 3/3, and all three assert this machine has no network interface. |
| fleet-1 | 3 | PARTIAL | Absence half handled well with attribution 3/3; mechanism half wrong — no rep describes *this* host subscribing to a broker, and r1 claims an `mqtt` library is installed here. |
| fleet-2 | 3 | **FAIL** | "That topic implies a ROS2 master is running on this machine... Neither is true." Direction never explained; ROS2 given a master (a ROS1 concept) 2/3, and the Pi called a "slave". |
| fleet-3 | 3 | **FAIL** | r1 names the entire fleet roster correctly, then answers that none can run an LLM *because none is attached*; r2 and r3 recite audio sinks to the cap. |

## Key findings

- **The fleet identity is in the prompt, is retrievable, and is being read as an
  inventory of missing hardware.** `fleet-3` r1 names all five device classes —
  Raspberry Pi 4, ESP32-S3, ESP32-Feather V2, Elecrow display, RDX X5 — so the
  SYSTEM IDENTITY block from `1363ead` is landing. It then concludes that none of
  them can run a quantized LLM *because none is currently connected to this laptop*.
  The tier reasoning the row wanted (Pi 4 yes, ESP32 no, on compute and memory
  grounds) never appears. A fleet is by definition not plugged into the hub; the bot
  cannot hold "part of my deployment" and "not attached right now" at the same time,
  so the second one wins and erases the first.
- **`fleet-2` rejects the fleet as a false premise, 3/3.** "That topic implies a
  ROS2 master is running on this machine and a RPi 4 is connected as a subscriber.
  Neither is true." The publisher/subscriber direction is never explained and
  `/cmd_vel` is never identified as carrying velocity commands. Two reps also give
  ROS2 a master — that is ROS1; ROS2 removed it — and r3 calls the Pi a "slave".
  This is the same reflex as Day 18 `meta-2` denying the Overseer, now pointed at
  the deployment model the whole fleet arc was built to establish.
- **Pure knowledge rows are strong: `dev-2`, `dev-4`, `dev-5`, `dev-7` all clean.**
  Four of the five PASS rows are rows with nothing to be absent. That is the same
  split as Days 17 and 18, now on an eleven-row session: the knowledge is there and
  the failure is entirely in the self-referential layer.
- `dev-8` repeats Day 14's `ifaces` error verbatim — all three reps assert this
  machine has no network interface, while answering over the loopback bridge on
  :7683 that delivered the question.

## Day 20 note

Day 20 is the conversation session, and Day 19 has made a sharp prediction for it:
asked to describe itself, the bot will lead with what is missing. `fleet-3` r1 is
the test case in miniature — it had the roster and spent it on an absence report.
