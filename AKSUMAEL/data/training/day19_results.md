# Day 19 Results — 2026-08-10

Score: 3 pass / 2 partial / 6 fail

**Revised from 4/2/5 by the second grader.** `dev-1` moves PARTIAL → FAIL on a finding the
first pass missed entirely: r3 asserts three devices as present that the readings report
absent, under the readings' own name. See "Correction: the phantom devices" below, which
also carries the session's most important result.

Node victus-t7, 33 runs (n=3 x 11 rows — `fleet-1..3` added by d960309), POSTs
03:16:15–03:36:15, 33/33 answered and verified, no retries. Continuous process
lifetime from 22:29 — same block as Days 15-18.

| Obj | n | Result | Key finding (1 line) |
|---|---|---|---|
| dev-1 | 3 | **FAIL** | Counts wrong on r2, and **r3 asserts /dev/ttyUSB0, /dev/ttyACM0 and /dev/i2c-1 as present, attributed to the block that reports all three absent** — the row's explicit FAIL trigger. |
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

## Correction: the phantom devices (second grader)

The first pass graded `dev-1` PARTIAL on arithmetic — "counts are wrong again". The counts
are wrong, but that is not what happened on r3, and the real failure is a new class for
the arc. Verbatim:

> `dev-1` r3: "The LIVE HARDWARE READINGS block lists one USB serial device at
> **/dev/ttyUSB0**. The LIVE HARDWARE READINGS block lists one KB2040 microcontroller at
> **/dev/ttyACM0**. The LIVE HARDWARE READINGS block lists one I2C bus at **/dev/i2c-1**."

The block it names says `USB serial (FTDI/UART): NONE DETECTED`, `USB CDC serial: NONE
DETECTED`, `KB2040 microcontroller: NOT PRESENT`, `I2C addresses responding: none`.

Device state was captured from `GET :7683/state` at both ends of the run and is identical:

```
start (tick 17197)  counts: ttyACM 0, ttyUSB 0, i2c 0, video 2, input 18,
                            audio_sinks 9, audio_sources 2
                    kb2040: present False, responding False, i2c_addresses []
end   (tick 21601)  identical
```

Nothing was plugged in or unplugged. All 33 reps are comparable and r3's three devices do
not exist.

`fleet-3` r2 repeats the pattern with four: "lists one capture card: **/dev/video2** …
lists one USB serial port: **/dev/ttyUSB0** … lists one I2C bus."

**Why this matters more than a count error.** Every fabrication recorded in this arc so far
is invention into a *gap* — Day 18 `meta-4` placing Honcho on this host, Day 16 `svc-1`
relabelling devices as services, Day 15 `sec-5` inventing a relation. Those are claims the
blocks fail to support. These are claims the blocks **explicitly contradict**, asserted
under the contradicting block's own name. That is a different detector: the arc's tooling
checks whether a claim is *supported*, and nothing checks whether a claim is *refuted*. A
refutation check is cheaper and would have caught all six phantom claims here immediately.

**The mechanism is visible, and it is the degeneration.** Both phantom-producing reps fell
into a repeated sentence frame — "The LIVE HARDWARE READINGS block lists one X at Y",
fifteen times in `dev-1` r3 — and in both cases the phantoms appear **late, after the real
block content is exhausted**. `fleet-3` r2 recites eleven true sentences and then produces
`/dev/video2`, `/dev/ttyUSB0` and an I2C bus in sentences twelve through fourteen. The
template demands more X-at-Y pairs than the block contains and the model supplies them.

That connects two findings previously filed separately: Day 17's verbatim self-duplication
(3/24, called a possible decoding artifact) and the ENUMERATE recitation failure running
since Day 14. They are the same event. The duplication is not cosmetic — it is the state in
which the model starts inventing, and the tail of a capped ENUMERATE answer is where to
look for it. Day 18 recorded 0/24 duplication and also 0 phantom devices, which fits.

Detector counts for this class, n=33:
- device asserted present that the readings report absent: **2/33** reps, **6 claims**
- claim contradicting the block named in the same sentence: **2/33** — new detector
- degenerate sentence-template repetition to the cap: **3/33** (`dev-1` r3, `fleet-3`
  r2/r3); Day 18: 0/24, Day 17: 3/24
- `hw:N,M` range extension: **2/33** (`dev-1` r2, `fleet-3` r2)
- audio sink moved onto the sources line: **1/33** (`dev-1` r2) — Day 8 `k-1`'s exact
  failure, first recurrence since

## Second grader: two dissents recorded, not applied

`dev-2` (graded PASS): r1 states "the live hardware readings show **no USB HID devices**".
The readings carry no HID field at all — no device class is reported for anything — so both
that claim and its negation are unsupported. r2 is genuinely RECONCILED and earns the row;
r1 is a milder instance of the same denial reflex the session's other rows fail on. PARTIAL
would be defensible. Left as PASS.

`dev-7` (graded PASS): the question asks for the capture card's **role**. r2's full answer
is "The capture card is not attached; the system uses fallback screenshot mode because
/dev/video2 is missing" — the absence and a consequence, but no statement of what the card
is for. Only r3 conveys the role ("the system cannot receive game input from the remote
PC"), and r1 truncates before reaching it. Worth recording regardless of grade: **all three
reps put the absence first and lost or omitted the role**, the inverse of Day 15 `sec-4`,
where the definition survived the cap and the state half was sacrificed. Under the 40-word
cap the state half now outranks the knowledge half.

## Also worth carrying: the one row that got the epistemics right

`dev-4` r3 closes with the best line in the arc, unprompted, at 50 words:

> "the I2C bus is currently empty **or unreachable**, so no devices are responding. **This
> is a general engineering concept; it does not imply any specific hardware is attached
> here.**"

It answers the knowledge half, preserves the reading's own hedge instead of resolving it,
and refuses to convert either into a state claim. `dev-8` r1 produces the same form for the
network question — "does not have any network interfaces **listed in the provided hardware
readings**, so I cannot confirm" — 277 ticks after `dev-6` r1 asserted "no GPIO pins are
present on this machine" while naming, in the same answer, the instrument that shows it
cannot know that.

The correct form exists, in this session, on adjacent rows. Any guard written against the
false denial should use `dev-4` r3 as its target string.

## Day 20 note

Day 20 is the conversation session, and Day 19 has made a sharp prediction for it:
asked to describe itself, the bot will lead with what is missing. `fleet-3` r1 is
the test case in miniature — it had the roster and spent it on an absence report.
