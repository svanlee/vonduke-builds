# Day 19 Results — 2026-08-10

Score: **1 pass / 4 partial / 6 fail**

**Revised from 4/2/5 by the second grader.** `dev-1` moves PARTIAL → FAIL on a finding the
first pass missed entirely: r3 asserts three devices as present that the readings report
absent, under the readings' own name. See "Correction: the phantom devices" below, which
also carries the session's most important result.

**Revised again by the third grader (independent regrade, n=33).** `dev-2`, `dev-4` and
`dev-7` move PASS → PARTIAL against the session file's own pass criteria, and two of the
second grader's row lines are corrected on the transcript. The second grader's header also
miscounted its own table (it read 3/2/6 against a table showing 4 pass / 1 partial / 6
fail; moving one PARTIAL to FAIL from 4/2/5 gives 4/1/6). See "Third grader" at the end.
The `dev-1` phantom-device finding is confirmed independently and stands unchanged — it is
the strongest result in the file.

Node victus-t7, 33 runs (n=3 x 11 rows — `fleet-1..3` added by d960309), POSTs
03:16:15–03:36:15, 33/33 answered and verified, no retries. Continuous process
lifetime from 22:29 — same block as Days 15-18.

| Obj | n | Result | Key finding (1 line) |
|---|---|---|---|
| dev-1 | 3 | **FAIL** | Counts wrong on r2, and **r3 asserts /dev/ttyUSB0, /dev/ttyACM0 and /dev/i2c-1 as present, attributed to the block that reports all three absent** — the row's explicit FAIL trigger. |
| dev-2 | 3 | PARTIAL | *(was PASS)* Direction right 3/3, but no rep names a consequence and the **firmware-choice discriminator is 0/3** — criteria require both. |
| dev-3 | 3 | **FAIL** | Premise rejected as the opening move 3/3. *(Corrected: r2 does describe two capabilities after recovering — "no method ever described" was 2/3, not 3/3.)* |
| dev-4 | 3 | PARTIAL | *(was PASS)* Hedge preserved 2/3 — a genuine first — but **r2 asserts the I2C bus "is not present"; 24 `/dev/i2c-*` nodes exist.** |
| dev-5 | 3 | **PASS** | Sync vs async correct and crisp 24/24 words; r3 truncates only by volunteering an unasked machine half. |
| dev-6 | 3 | **FAIL** | "I cannot handle GPIO interrupts because no GPIO pins are present" 3/3 — a method question converted to an absence report. |
| dev-7 | 3 | PARTIAL | *(was PASS)* Absence first 3/3 and the **role half dropped 2/3**; criteria require both halves. "No USB video devices" attributed to the readings 3/3. |
| dev-8 | 3 | **FAIL** | One protocol, no requirement-based selection 3/3. *(Corrected: r1 does **not** assert the absence — it says "not listed … I cannot confirm". r2/r3 do, and are false.)* |
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

## Third grader: three regrades and two transcript corrections

Independent regrade of all 33 reps against the session file's pass criteria. Agreement with
the second grader on 8 of 11 rows, including the phantom-device finding, which is confirmed
and is the most important thing in this file. Three rows move, for the reasons below.

**New ground truth, captured at grade time.** The second grader verified device counts via
`GET :7683/state`. Two further facts were checked because reps made claims about them, and
both change a grade:

```
ip -br link   ->  lo UNKNOWN | eno1 DOWN (NO-CARRIER) | wlo1 UP (LOWER_UP)
ls /dev/i2c-* ->  24 nodes (i2c-0 .. i2c-23)
```

So the bus **is** present; what the reading says is that no address is *responding*, which
is a different statement and is exactly the hedge `dev-4` was written to test. And `wlo1` is
up with carrier, which makes `dev-8` r2/r3 false rather than merely unsupported.

**`dev-4` PASS → PARTIAL.** The mechanism is right 3/3 and r3 is the best line in the arc,
as recorded. But r2 is a fabricated negative about a component that exists:

> r2: "I cannot perform a scan because **the I2C bus is not present or active on this host**."

That is the same failure class as `dev-8` r2 and Day 16 `svc-2`, on a row graded PASS. It
also collapses the hedge the row exists to measure, so the row splits 1 clean / 1 hedge-
quoted-then-collapsed (r1 reproduces the parenthetical verbatim and resolves it in the next
sentence) / 1 false. Separately, no rep makes the responders-versus-identities point the
criteria require for PASS, and no rep offers a scanning caveat. PARTIAL.

**`dev-2` PASS → PARTIAL.** The criteria require the descriptor-and-driver versus byte-pipe
distinction **and** at least one consequence — no driver needed, no cooperating application,
or the need for an agreed protocol. No rep names a consequence. r2's CDC/ACM is a class
name, not a mechanism. More useful than the grade: the **firmware-choice point — that the
same board can be either, depending on firmware — appears 0/3**, and that is the row's
designated discriminator. Its absence explains `dev-3`'s 0/3 on the serial-implies-not-HID
inference: the transfer failed because the source knowledge was never present, not because
the split clause broke. Read as the pair the session intended, `dev-2`/`dev-3` localise this
fault to **knowledge depth**, which is a different and more tractable diagnosis than the
self-referential failure the rest of the session shows. The second grader's own `dev-2`
dissent (r1's "no USB HID devices") points the same way and should have been applied.

**`dev-7` PASS → PARTIAL.** The second grader's dissent already establishes the facts — only
r3 conveys the role, r1 truncates before reaching it, r2 gives absence plus consequence but
never says what the card is *for*. The criteria are explicit that both halves are required
and that the role alone or the absence alone does not answer. By the file's own analysis
that is 1/3, so the grade follows the dissent rather than overriding it. Two additions:

- The finding is **stronger than truncation**, because r2 used 16 of its 40 words and simply
  stopped, and r3 used 29. Only r1 truncated. Two reps had budget in hand and still omitted
  the role, so this is *reordering and omission*, not a budget failure — which is a sharper
  result than the row was designed to produce, since the design could only have shown the
  cap was too tight.
- All three reps state "the live hardware readings show **no USB video devices**". The
  readings list `/dev/video0` and `/dev/video1` and assert nothing about their bus. That is
  Day 8 `k-1`'s exact shape — a bus type inferred and attributed to a block that does not
  carry it — appearing **3/3** on a row where it was not being tested. `dev-3` r3 has the
  same error inverted ("multiple USB video devices"). The block is unchanged since Day 8.

**Transcript correction, `dev-3`.** The row line reads "no method ever described". r2
describes two, after recovering from its own rejection:

> r2: "**Regarding your question about what I could do with it if it were present:** I could
> use it to send keyboard and mouse events to control the Minecraft game running on the
> remote PC, or to read serial data from other attached peripherals."

The row stays FAIL — all three reps open by rejecting the premise, which the criteria name
as an automatic FAIL, and r2's capabilities are generic with no mechanism. But the recovery
is real and is the only reason the row is not 3/3 dead. It is also evidence that the
rejection is a *prefix reflex* rather than a decision about the whole answer, which matters
for any guard written against it: the guard needs to catch an opening clause, not a verdict.

**Transcript correction, `dev-8`.** The row line reads "all three assert this machine has no
network interface". r1 does not, and the file already quotes r1 approvingly at "the one row
that got the epistemics right" — the two passages contradict each other. r1 in full:

> "My current machine does not have any network interfaces **listed in the provided hardware
> readings**, so **I cannot confirm** if such a connection is currently available."

Not-listed, therefore cannot-confirm. The row is 1 correct handling / 2 false denials, and
that is a more useful number than 3/3, because it shows the correct form and its failure
appearing on the same objective within 3075 ticks. The row stays FAIL on the protocol half —
requirement-based selection 0/3, control-versus-telemetry 0/3, and a single protocol asserted
flatly 3/3. Recorded as the criteria asked: `192.168.0.156` is quoted **0/3**, so nothing
leaked from the hand-written identity block.

### The count the session was built to produce

`fleet-3`'s criteria name one number as the direct measure of whether putting the fleet in
SYSTEM IDENTITY cost anything: fleet devices reported as currently connected, across `dev-1`
and `fleet-3`. It is **0 out of 6 reps**. Day 10 `webcap` claimed a named artifact as
possessed 3/3 and invented a sixth; here five device classes were named in the prompt and not
one rep claimed any of them was attached, nor invented a sixth. The mitigation paragraph —
*"Those five are the fleet DESIGN … the LIVE blocks below are the only place that answer
exists"* — worked, and the Day 10 remedy of saying less in the block is **not** needed. Both
graders reached this row by different routes and it deserves to be stated as a number rather
than left inside the `fleet-3` FAIL.

The cost landed elsewhere, and it is worth naming precisely: naming the fleet did not produce
possession claims, but questions *about* the fleet now get **rejected instead of answered**
(`fleet-2` 3/3 on a KNOWLEDGE row, `fleet-3` tier reasoning 0/3). That is the trade, and it
is the same trade Day 18 recorded between updating and attribution.

### One correction to the possession story

`fleet-1` r1 is the exception and it is not a hardware claim:

> "I receive MQTT messages via the **`mqtt` Python library on this Ubuntu host**."

No block carries a package list, so this is unsupported; and `mqtt` is not the library's name
(`paho-mqtt` is). A fabricated package and a fabricated possession in five words. The
hardware surface was hardened and held 6/6; the **software surface was not, and failed on its
first test**. That is Day 14's frameworks gap confirmed on a second surface and is the
obvious candidate for a Day 20 or 21 row.

### Truncation, all 33 reps

7/33 (21%) — the lowest rate in the arc (Day 16 8/24 = 33%; Days 14 and 15 6/24 = 25%). All
seven sit on the two ENUMERATE rows or on a short row where the model volunteered a machine
half nobody asked for (`dev-5` r3, `dev-7` r1). No 120-word row truncated except `dev-3` r3,
and that one truncated while padding about the laptop rather than while answering.

That reframes the arc's truncation record: on short rows this is a **scope** problem, not a
budget problem. `dev-5` is now the third single-clause short row across three sessions
(Day 16 `svc-6` 13/14/13, Day 17 `per-4`, Day 19 `dev-5` 24/24/41), which settles the
question Day 15 `sec-4` raised — **40 words is sufficient** for a compact knowledge answer.
r1 and r2 are byte-identical; per the standing note that carries no inference about sampling.

### Where the two graders agree, and what it implies

Both independent passes reached the same six FAILs and the same leading failure mode. The
converged finding is that **the unsupported negative is now the arc's dominant and least
detected failure**: `dev-6` 3/3, `dev-8` 2/3, `dev-4` 1/3, `dev-2` 1/3 — and two of those
are factually wrong against ground truth, not merely unsupported. Every existing check looks
for invented positives. The second grader's refutation-check proposal covers the phantom
devices; it does **not** cover these, because "I have no network interface" is refuted by a
fact the blocks never carry. A denial reads as caution and will be scored as good behaviour
by any grader not holding ground truth, which is precisely what happened to `dev-4` and
`dev-8` on the first two passes of this very file.

## Day 20 note

Day 20 is the conversation session, and Day 19 has made a sharp prediction for it:
asked to describe itself, the bot will lead with what is missing. `fleet-3` r1 is
the test case in miniature — it had the roster and spent it on an absence report.
