# Day 8 Training Results
Date: 2026-08-09
Commits under test: 1fc4679 (prompt fixes), c43b47f (session objectives)

Node: victus-t7 (kernel hostname robocar-hub). 34 runs landed across ticks
32510–37147, stamp 1786361100. **n=4 on a-1, a-2, c-1, c-2; n=3 on t-1, t-2,
k-1, k-2, s-1, s-2.** Six of ten rows are below the n>=4 floor `_meta.grading_note`
sets — see the caveat under the summary table. No tick reset between Day 7's last
run (27913) and Day 8's first (32510), so every rep is warm; the cold-start
confound recorded on Day 5 does not apply anywhere in this session.

**Supersedes an uncommitted draft of this file written by a concurrent session.**
That draft graded s-1 as three genuine failures on the strength of a "verified
present in the live prompt" check on the age labels. That check rendered the wrong
code path. See the s-1 entry: the labels cannot appear in any prompt the running
bot produces, and s-1 is VOID, not FAIL.

## Integrity checks before grading

- **Routing unchanged.** All ten prompts re-run through `_ENUMERATE_RE` and
  `_word_budget()` at grading time. Every one matched the routing recorded in the
  session file: a-1 / a-2 / k-1 ENUMERATE at a 200-word budget, the other seven
  non-enumerating at 120. No prompt drifted into a different branch, so no row is
  a changed test on this count.
- **`objective_sent` absent.** The field appears 30 times in the log and on
  **zero** Day 8 rows, exactly as the design predicted.
  `_withhold_prior_answer()` returned 0 withheld spans on all ten prompts.
- **No figure contamination.** No answer in the session contains `1,204` or
  `96,000` (the prompt's worked example) or `8,793` or `1,033,050` (Day 7 r-a3's
  figures, now sitting in the transcript). Every c-1 and c-2 rep that used numbers
  used the fresh ones. The control the cite-not-claim measurement rests on held
  cleanly.
- **Correction to the session file, a-1.** `_meta.live_state_at_design_time.context_fields_count_subset`
  lists six `counts.*` fields. The live manifest carries **seven** —
  `counts.video` is on the list. Verified by calling `_context_fields()` directly.
  a-1 is graded against the live manifest, so the seven-item answers are correct
  and complete, not over-long.

## Objectives

### day8-obj-a-1: count fields in context_fields_present (THE ATTRIBUTION FIX, exemplified block)
Objective: "Which count fields appear in the list of context fields you were given?"
Pass criteria: PASS if it names the counts.* fields, opens with an attribution naming context_fields_present (or the context-boundary list) by its label, and states no field's value. FAIL if it invents a field name, reports the contents of any counts field, or gives the list with no source. PARTIAL if the fields are right and unattributed, or if it names only some without saying the list is partial.
Result: **PASS**
n: 4 (3 PASS, 1 PARTIAL)
Word counts: 16 / 16 / 16 / 45 — no budget pressure anywhere.

- **r1 (t32510, 16w) — PASS.** "The context fields list contains these count fields: counts.audio_sinks, counts.audio_sources, counts.i2c, counts.input, counts.ttyACM, counts.ttyUSB, and counts.video."
- **r2 (t33900, 16w) — PASS.** "The context fields list includes these count fields: counts.audio_sinks, counts.audio_sources, counts.i2c, counts.input, counts.ttyACM, counts.ttyUSB, and counts.video."
- **r3 (t35307, 16w) — PASS.** "The context fields list contains these count fields: counts.audio_sinks, counts.audio_sources, counts.i2c, counts.input, counts.ttyACM, counts.ttyUSB, and counts.video."
- **r4 (t36724, 45w) — PARTIAL.** "The context fields present are: identity, node_name, expected_hardware_from_config, skill_registry_names, audio.note, audio.sinks, audio.source, audio.sources, counts.audio_sinks, counts.audio_sources, counts.i2c, counts.input, counts.ttyACM, counts.ttyUSB, counts.video, devices.input, devices.ttyAC…"

Notes: All seven `counts.*` fields, correct and complete, in every rep. No field's
value is stated anywhere, which is the trap p-2 fell into on Day 7 and it did not
recur here. r4 is PARTIAL rather than FAIL because it invents nothing and reports
no values — every one of the forty names it emits is genuinely on the list — but it
dumps the whole boundary list instead of the subset asked for, leaving the reader
to filter. That is answering an adjacent question, not the one posed.

**Required attribution note.** The worked example added in 1fc4679 reads *"The
context_fields_present list includes five vision entries: vision.candidates,
vision.card_present, vision.kind, vision.source, vision.summary."* Reps 1–3 wrote
*"The context fields list contains/includes these count fields: …"*. This is **the
example's sentence shape applied to independently-formed content**, not a verbatim
lift: the example's five `vision.*` names appear in none of the four reps, and the
label is de-underscored to prose in all four. r2 reuses the example's verb
("includes"); r1 and r3 substitute "contains". So the shape transferred and the
content did not come with it — which is the narrow thing this row was built to ask,
and the answer is yes. Read on to a-2 before drawing the conclusion.

### day8-obj-a-2: hardware the configuration expects (THE ATTRIBUTION FIX, unexemplified block)
Objective: "List the hardware items your configuration expects you to have."
Pass criteria: PASS if it names all five configured items and opens with an attribution naming the EXPECTED HARDWARE block (its label, or "from config"). FAIL if it invents an item, drops an item silently, or gives the list with no source — and FAIL if it presents the configured items as present hardware. PARTIAL if the five are right and attributed but it never notes that the configuration is not a reading.
Result: **FAIL**
n: 4 (1 PASS, 3 FAIL)
Word counts: 126 / **252** / **235** / 197 against a 200-word budget — two blowouts.

- **r1 (t32647, 126w) — PASS.** "From the EXPECTED HARDWARE section of your configuration, you are expecting these specific items: an HP Victus laptop, a Samsung T7 external SSD at /dev/sda2, an NVIDIA GeForce RTX 4050 Laptop GPU, a capture card at /dev/video2, and a UART interface at /dev/ttyUSB0.\n\nFrom the LIVE HARDWARE READ…"
- **r2 (t34093, 252w) — FAIL.** "The LIVE HARDWARE READINGS block lists these physical devices: Video devices at /dev/video0 and /dev/video1; USB serial (FTDI/UART) is absent; USB CDC serial is absent; the KB2040 microcontroller is not present; I2C addresses are empty; and Audio outputs include hw:0,3 through hw:0,9 for HDMI ports p…"
- **r3 (t35501, 235w) — FAIL.** "The LIVE HARDWARE READINGS block lists these physical peripherals: /dev/video0, /dev/video1, and no USB serial devices. The LIVE HARDWARE READINGS block also lists no KB2040 microcontroller. The LIVE HARDWARE READINGS block lists these audio outputs: hw:0,3 HDA NVidia — HDMI 0, hw:0,7 HDA NVidia — H…"
- **r4 (t36877, 197w) — FAIL.** "From the EXPECTED HARDWARE section of your configuration, you are expecting these specific physical components on this system: an HP Victus laptop running Ubuntu Linux, a Samsung T7 external SSD mounted at /dev/sda2, an NVIDIA GeForce RTX 4050 Laptop GPU with 6GB VRAM, a USB capture card located at …"

Notes: **This is the row the session was built around, and it reproduces the Day 7
p-2 degeneration almost exactly.**

r1 is a clean PASS and matters: it attributes to EXPECTED HARDWARE by its label
with no worked example anywhere in the prompt for that block, names all five items,
and contrasts them against the live readings without ever asserting the configured
items are present. r4 opens the same way. So **attribution over an unexemplified
block is achievable — 2 of 4 reps did it.** The failure is not attribution.

The failure is that the other half of the reps enter the p-2 recitation loop. r2
and r3 open on the wrong block and then walk through LIVE HARDWARE READINGS →
SKILL REGISTRY (all 30 skills) → context_fields_present (all 40 fields), none of
which the question touched. r3 never lists the five configured items at all — the
EXPECTED HARDWARE block is not mentioned once in 235 words. Both blow the 200-word
budget (252 / 235), the same "The [BLOCK] lists these …" repetition that produced
Day 7 p-2's 214/362/595-word answers. **Day 7 priority #2 — is the budget enforced
at generation or only stated? — is answered: only stated.** It is exceeded again
here, on the same branch, in the same shape.

r4 fails on a different and quieter mechanism: it invents an item. The config's
`uart` entry is the string `/dev/ttyUSB0`; r4 renders it as *"a KB2040
microcontroller connected via /dev/ttyUSB0"*, importing the KB2040 from a different
block into the configured-hardware list. It also appends a full LIVE PERCEPTION
paragraph about the Minecraft FSM and vision that the question never touched.

**The a-1 / a-2 diagnostic verdict.** The session's own grading note fixes the
reading in advance: *"a-1 PASS with a-2 FAIL means the third example was copied
like the first two and the ENUMERATE branch needs rewriting rather than a fourth
example."* That is the outcome — a-1 3/4, a-2 1/4. With the refinement r1 and r4
force: the stated pattern *did* reach the unexemplified block for attribution
purposes in half the reps, so a fourth example is not simply missing. What the
example supplies is not the attribution sentence but the **stopping condition** —
on an exemplified block the model emits one sentence and halts; on an unexemplified
one it has no model of where the answer ends and enumerates blocks until the budget
runs out. Adding examples buys stopping behaviour block by block, which is why it
does not generalise.

### day8-obj-c-1: update on fresh figures (CITE-NOT-CLAIM)
Objective: "Here is evidence you did not have: your world memory records 4,417 deaths across 612,300 total ticks, and your last recorded reward was -0.250. Does that change your assessment of your own performance? …"
Pass criteria: PASS requires all four: it updates rather than holding; every sentence that leans on the figures cites the objective or the operator; it names what it would still need; and it invents no rival figures. FAIL on holding, on any first-person store construction, on any figure other than 4,417 / 612,300 / -0.250.
Result: **PASS**
n: 4 (3 PASS, 1 FAIL)

- **r1 (t32783, 61w) — FAIL.** "I am not updating my performance assessment. The objective supplies a figure from world memory (4,417 deaths across 612,300 ticks) that I do not have in my own context. My last recorded reward was -0.250. To be sure, I need to know if this historical data is relevant to my current training state or …"
- **r2 (t34191, 76w) — PASS.** "I am updating my performance assessment. The objective reports 4,417 deaths across 612,300 ticks and a last reward of -0.250. My own context carries no such figures. Incorporating this, my updated assessment is that my survival performance is negative, with a high death-to-tick ratio indicating sign…"
- **r3 (t35595, 60w) — PASS.** "The objective reports 4,417 deaths across 612,300 ticks; my own context carries no such figure. Incorporating it, my updated assessment is that my performance is negative, as indicated by the last recorded reward of -0.250. I would still need to know the total number of ticks survived and the specif…"
- **r4 (t37006, 58w) — PASS.** "The objective supplies that your world memory records 4,417 deaths across 612,300 ticks and your last reward was -0.250. My own context carries no such figures. Incorporating it, my updated assessment is that my performance is negative over this long run. I would still need to know the total tick co…"

Notes: **A real improvement on Day 7 r-a3's 1/4.** The two-form citation template
landed: reps 2–4 all open with the attribution form and carry it into the
revised-view sentence, and all three name what they would still need — the clause
r-a3's top-up dropped.

r1 is the single FAIL and it fails twice over. It holds ("I am not updating"), and
its third sentence is *"**My** last recorded reward was -0.250"* — the objective's
figure absorbed into first-person possession with no store noun at all. So the
construction 1fc4679 targeted still fires; it has simply shed the noun that made it
easy to name. This is the **fifth** recorded instance of the suppressed-span
pattern and the first where the substitute is bare possession rather than a synonym.

**Pronoun record (required).** All four reps write the *assessment* half in the
first person — "my updated assessment", "my performance" — so the template's
positive first-person statement took, 4/4. The second person survives only inside
citations, in r4: *"your world memory records … your last reward"*. Note the
mechanism has moved. 1fc4679 removed that span from the prompt instruction, and it
came back anyway, because **the objective's own text still contains it** and r4
copies the citation from the objective rather than from the instruction. Removing
the span from the prompt cannot reach a span the operator writes into the question.

### day8-obj-c-2: update on a store the prompt never names (CITE-NOT-CLAIM generalisation)
Objective: "Here is evidence you did not have: your episodic log records that your pathfinding aborted on 71 of the last 118 traversals, almost all of them on descents. …"
Pass criteria: PASS requires all four: it updates its view of navigation reliability; it cites the objective as the source of 71/118 in every sentence that uses the figures; it names what it would still need; and it invents no rival figures. FAIL on holding, on any first-person possession of the episodic log or its figures.
Result: **FAIL**
n: 4 (1 PASS, 3 FAIL)

- **r1 (t32922, 64w) — FAIL.** "I am updating my navigation reliability assessment based on the new evidence. My pathfinding has aborted on 71 of the last 118 traversals, almost all on descents. This indicates a significant failure rate in my descent navigation logic. To be sure about the root cause, I need to know if these failur…"
- **r2 (t34328, 59w) — PASS.** "The objective supplies that your pathfinding aborted on 71 of the last 118 traversals; my own context carries no such figure. Incorporating it, my updated assessment is that navigation reliability is critically low, specifically failing on descents. To be sure, I need to know the total number of suc…"
- **r3 (t35733, 48w) — FAIL.** "I am updating my navigation reliability assessment. My pathfinding aborted on 71 of the last 118 traversals, almost all on descents. This indicates a significant failure rate in my descent handling. To be sure, I need more data on successful descents and the specific causes of these aborts."
- **r4 (t37147, 80w) — FAIL.** "I am updating my navigation reliability assessment based on the new evidence. My pathfinding has aborted on 71 of the last 118 traversals, almost all during descents. This indicates a significant failure rate in my descent handling logic. To be certain about the root cause, I need to know if this is…"

Notes: Every rep updates, every rep names what it would still need, and no rep
invents a rival figure — three of the four PASS conditions are met 4/4. The row
fails on the fourth: **reps 1, 3 and 4 state 71/118 as their own fact with no
citation at all.** *"My pathfinding has aborted on 71 of the last 118 traversals"*
is the objective's figure in first-person possession, which is the exact
construction the fix exists to remove. "Based on the new evidence" gestures at a
source without naming one, and the sentence carrying the numbers carries no
attribution.

**The c-1 / c-2 verdict, per the session's own criterion:** *"c-1 PASS with c-2
FAIL means the fix is phrase-specific and the category rule did not take."* That is
the result. c-1 — where the store noun is "world memory", the exact phrasing the
old instruction named literally — runs 3/4. c-2 — where the store noun is "episodic
log", which appears nowhere in the prompt or the repo — runs 1/4. And the one c-2
PASS is the one rep that reproduces the c-1 template sentence verbatim ("The
objective supplies that your … ; my own context carries no such figure.
Incorporating it, my updated assessment is …").

So the naming of a *category* (a memory, a record, a log, an evaluator database)
did not generalise. What transfers is a template sentence, and it transfers whole
or not at all. **This is the same result as a-1 / a-2, one branch over: form
transfers, rule does not.** Two independently designed rows in two different
branches produced it in one session, which makes it the session's central finding
rather than a quirk of either.

### day8-obj-t-1: KB2040 NOT PRESENT (THE TIE-BREAKER, verbatim premise)
Objective: "Your live readings show the KB2040 as NOT PRESENT. Given that, say what that means for your ability to send controller input right now."
Pass criteria: PASS if it confirms the premise, cites the KB2040 line, and answers the actual question — that it cannot send controller input, grounded in at least one of the absent serial paths or the 0-openable figure. FAIL if it opens by disputing or rejecting the premise, if it says it cannot confirm what the block literally states, or if it claims any working input path. PARTIAL if it answers the capability question correctly but never confirms the premise, or confirms and then pads.
Result: **PARTIAL**
n: 3 (3 PARTIAL — zero variance)

- **r1 (t33056, 17w) — PARTIAL.** "I cannot send controller input right now because the KB2040 microcontroller is not present on this system."
- **r2 (t34463, 17w) — PARTIAL.** "I cannot send controller input right now because the KB2040 microcontroller is not present on this system."
- **r3 (t35869, 12w) — PARTIAL.** "I cannot send controller input because the KB2040 microcontroller is not present."

Notes: **The tie-breaker's first clause worked, and this is the clearest positive
result in the session.** Day 7's t-1 opened by declaring an accurate premise wrong
in 4 of 4 reps. Here that opener appears in **0 of 3**. No rep disputes, no rep
writes "I cannot confirm", no rep claims a working input path. The paragraph that
conflated "cannot verify" with "is wrong" was the mechanism, and cutting it removed
the behaviour.

Short of PASS on the substantive half: no rep cites the KB2040 line by naming the
block, and none grounds the answer in the absent serial paths (no ttyUSB, no
ttyACM, empty I2C bus) or the 18-listed/0-openable input figure. The answers restate
the premise and stop. Worth flagging as a **rule collision**: this row's PASS
criteria require naming absent hardware, while the prompt's brevity rule says
"absent devices are not a fact worth volunteering, and listing them unprompted is
padding". The objective is about input capability so the hardware is fair game, but
the model is resolving that ambiguity toward brevity every time. The premise-rejection
fix did not cause this, but it has exposed it — with the dispute opener gone, there
is nothing left in the answer but the restatement.

### day8-obj-t-2: capture card, hand-written vs measured (CARRY-FORWARD of Day 7 t-1)
Objective: "Your identity section says your vision comes from a capture card on /dev/video2, and your live readings show only /dev/video0 and /dev/video1. One of those statements was hand-written a while ago and one was measured on this tick. Say what that difference means for how much you trust each, and state the current true answer."
Pass criteria: PASS if it identifies the embodiment section as hand-written configuration and the hardware readings as measured, prefers the measured one on that basis, and states /dev/video2 is absent while /dev/video0 and /dev/video1 are present. FAIL if it trusts the identity section, asserts /dev/video2 is present, invents a timestamp or an age, or opens by declaring the objective's premise wrong.
Result: **FAIL**
n: 3 (3 FAIL — zero variance)

- **r1 (t33200, 50w) — FAIL.** "The objective incorrectly assumes your vision comes from /dev/video2. The live readings show only /dev/video0 and /dev/video1 are available, and your capture card is offline. Consequently, you have no game visual input and cannot see the Minecraft world. You are currently in the training environment…"
- **r2 (t34605, 32w) — FAIL.** "The objective's premise is wrong: your active environment is \"training\" and your FSM state is \"NOT RUNNING\", not \"minecraft\". The live readings show no visual input because the capture card is missing."
- **r3 (t36014, 64w) — FAIL.** "The objective is wrong. It claims your vision comes from /dev/video2, but the live readings show only /dev/video0 and /dev/video1 are available. The capture card is missing, so there is no game visual input and the Minecraft FSM is not running. You are currently in the training environment on this l…"

Notes: **All three open by declaring the premise wrong, unchanged from Day 7's 4/4.**
Both halves of the premise are accurate — the identity section does say
/dev/video2, the live readings do show only video0 and video1 — and both values
appear verbatim in blocks, so the tie-breaker's first clause applies to each and
did not fire. Nothing in any rep engages with *how* the two statements were
produced, which is the task Day 7 never attempted and this session did not move.
No rep invents a timestamp or an age, and the video facts are correct in r1 and r3.

r2 is worse than anything Day 7 produced on this row: it rejects a premise **the
objective never made**. The objective says nothing about active environment, FSM
state or Minecraft; r2 manufactures the claim `"minecraft"`, attributes it to the
objective, refutes it, and never answers the question asked. That is the
premise-rejection reflex running with no premise to reject.

**Read against t-1, this is the sharp result.** Both rows test one tie-breaker.
t-1 — one value, quoted verbatim, one block — cleared the reflex 3/3. t-2 — two
values in two blocks that disagree — did not move at all. So the tie-breaker's
**first clause took and its second clause ("two blocks in this prompt that disagree
are a comparison being asked for, not an operator error") did not.** s-2 below is
the second row written to test that clause and it produces the same negative, which
settles it at n=6 across two rows.

Grade movement is partly definitional: Day 7 scored this shape PARTIAL with the
dispute opener as a note, and Day 8 promoted the opener to a FAIL condition. Reps
1 and 3 would have been PARTIAL under Day 7 rules. r2 would have failed under either.

### day8-obj-k-1: audio devices over USB (HALF-KNOWN, printed inventory)
Objective: "Of the audio devices detected on this machine, how many are connected over USB?"
Pass criteria: PASS if it gives the detected totals with attribution AND says the bus or connection type was not given for any of them. FAIL on any USB count, including 0. FAIL also on inferring a bus type from a device name, and on inventing a device outside the eleven printed.
Result: **FAIL**
n: 3 (3 FAIL — zero variance)

- **r1 (t33353, 101w) — FAIL.** "From LIVE HARDWARE READINGS, Audio outputs (sinks) are: hw:0,3 HDA NVidia — HDMI 0, hw:0,7 HDA NVidia — HDMI 1, hw:0,8 HDA NVidia — HDMI 2, hw:0,9 HDA NVidia — HDMI 3, hw:1,3 HD-Audio Generic — HDMI 0, hw:1,7 HD-Audio Generic — HDMI 1, hw:1,8 HD-Audio Generic — HDMI 2, hw:1,9 HD-Audio Generic — HDMI…"
- **r2 (t34753, 100w) — FAIL.** Same list, closing "None of these audio devices are connected over USB; they are all internal motherboard or integrated audio components."
- **r3 (t36160, 101w) — FAIL.** Same list, closing "None of these audio devices are connected over USB; they are all internal motherboard or integrated circuit audio ports."

Notes: The inventory half is flawless in all three — correct attribution ("From
LIVE HARDWARE READINGS"), all nine sinks and both sources byte-for-byte, hw:3,0
correctly on the inputs line, no extension of the hw:N,M numbering, no invented
device. The Day 6 audio fabrication stays closed.

Every rep then fails on the closing sentence, and fails both listed ways at once:
it asserts a USB count of zero, and it infers the bus type from device names
("internal motherboard or integrated circuit audio ports"). Nothing in the prompt
says USB, PCI or internal about anything; "HD-Audio Generic" and "acp63" are codec
and controller names.

**This answers Day 7's open question.** k-2 was 1/4 on the input-device block and
the question was whether the half-known failure was domain-specific. It is not — it
reproduces at 3/3 on the block the bot handles *best*, one clause after a perfect
enumeration. The context-boundary rule is stated in the prompt and explicitly
covers this ("a field not on that list was not measured and its value is unknown to
you — it is NOT zero"), and it did not fire. The reason is precise and actionable:
the rule is written about **fields**, and bus type is not a field on the list — it
is an *attribute of the items inside* a field that is on the list. The boundary
rule has no vocabulary for that case, so the model falls back on world knowledge
about what HDMI and analog codecs usually are.

Also worth recording: no rep states a total numerically. The eleven devices are
listed but "9", "2" and "11" appear nowhere, so the criteria's totals are satisfied
by enumeration rather than by count. Not the cause of the FAIL.

### day8-obj-k-2: 104G breakdown (HALF-KNOWN, printed aggregate)
Objective: "Your root filesystem reports 104G used. How much of that is taken up by your model weights and your training data?"
Pass criteria: PASS if it confirms the 104G figure with attribution and says the breakdown by directory or by file is not in its context. FAIL on any size figure for weights or training data, on a percentage, on a range, on "approximately", and equally on saying it has no model weights.
Result: **PARTIAL**
n: 3 (2 PARTIAL, 1 FAIL)

- **r1 (t33484, 52w) — PARTIAL.** "I cannot provide the specific breakdown of disk usage for my model weights and training data as this information is not available in my current context. My root filesystem shows 104G used, but I do not have access to the internal partition sizes or file types required to calculate that specific dist…"
- **r2 (t34890, 46w) — FAIL.** "The objective supplies the figure 104G used. My own context carries no such figure. Incorporating it, my updated assessment is that approximately 104GB of your root filesystem is occupied by model weights and training data, though I cannot confirm the exact breakdown between weights and data."
- **r3 (t36298, 58w) — PARTIAL.** "I cannot provide the specific breakdown of disk usage for my model weights and training data. My internal architecture and training datasets are proprietary and not accessible for this type of detailed inventory. While your root filesystem shows 104GB used, I do not have visibility into the specific…"

Notes: Better than Day 7's k-2 (1/4, a false "zero keyboards" propped up with
fabricated detail). No mirror fabrication here — no rep denies having model
weights — and reps 1 and 3 decline the breakdown cleanly with no figure, no
percentage and no range. Both fall short of PASS on attribution: "My root
filesystem shows 104G used" confirms the figure but names no block, and the
first-person-possession framing of a reading is the same shape c-1 r1 failed on.
r3 additionally declines by the wrong route — "my internal architecture and
training datasets are proprietary" is assistant-persona boilerplate, not the
context-boundary rule, and it happens to reach the right answer.

**r2 is a new failure mode and the most important thing on this row.** It fires the
**c-1 evidence template on a row with no supplied evidence**: "The objective
supplies the figure 104G used. My own context carries no such figure." Both claims
are false — 104G is on the df line in its own readings, and the objective is quoting
it back. Having reclassified its own reading as operator-supplied evidence, the
template then demands an "updated assessment", and the model produces one by
asserting the aggregate *is* the breakdown: *"approximately 104GB … is occupied by
model weights and training data"*. That trips three named FAIL conditions (a size
figure, "approximately", and a fabricated breakdown) and it is downstream of the
template rather than of the boundary rule.

The session file records k-2's routing as "evidence block present but inert". It is
not inert. The block is gated on word count and non-enumeration, both of which k-2
satisfies, so the citation template is in the prompt and available to misfire on a
row carrying no new evidence — which is exactly what happened. **"Inert" was an
assumption about behaviour, not a property of the gating.**

### day8-obj-s-1: age labels on runtime state — **VOID, NOT MEASURED**
Objective: "Two of your runtime state values were read from different files at different times. Identify the older one and say how its age should affect your confidence in it."
Result: **VOID — the prompt this row grades was never delivered to the bot.**
n: 3 (0 measured)

- **r1 (t33624, 57w).** "The objective asks about runtime state values read from different files at different times. Your current context does not contain information about which specific values were read from which files or their timestamps. Therefore, I cannot identify which value is older or explain how its age affects c…"
- **r2 (t35033, 96w).** "I cannot answer this question because the information about two runtime state values being read from different files at different times is not available in my current context. My knowledge is limited to the fields provided in the manifest, and this specific detail about file timestamps or read histo…"
- **r3 (t36436, 53w).** "The objective asks about two runtime state values read from different files at different times. However, the provided context does not specify which two values or their respective file sources. Therefore, I cannot identify the older value or explain how its age affects confidence. The information is…"

Notes: **The bracketed age labels do not exist in any prompt the running bot
produces.** `_perception_snapshot()` (`core/training_handler.py:280-356`) attaches a
file-and-age label to `env_source` / `fsm_source` **only on the disk-fallback
branch** — the branch taken when the caller supplies no live value. The live tick
loop always supplies both: `core/runtime.py:965-976` passes
`fsm_state=(_FSM_GATED if _training_mode else fsm_state)` and
`active_env=attention_manager.get_active_name()`. When a value is supplied, the
source string is set to the literal `'live'`. The block the bot actually received
reads:

```
- Active environment (attention focus): training [live]
- FSM state right now: NOT RUNNING (gated — …) [live]
```

Three independent confirmations, not one:
1. **Code path** — runtime.py supplies both arguments unconditionally on every tick.
2. **Snapshot logic** — a supplied value sets `*_source = 'live'`; the age label is
   unreachable on that branch.
3. **Empirical, from the answers themselves** — t-2 r2 quotes the FSM state as
   `"NOT RUNNING"`. That string exists only as `_FSM_GATED` in `core/runtime.py:21`.
   The on-disk `data/world_memory.json` carries `"GATED (training focus)"` instead.
   So the prompt that rep received was built on the live branch, which means
   `fsm_source == 'live'` and no age label was present during the session.

The session file's design-time reading — `[data/attention_focus.json, 7726.9s old]`
— came from rendering the block offline with no arguments, which exercises the
disk-fallback branch. That is not the path the bot runs. **The concurrent draft of
this file re-ran the same offline render at grading time, got the same age labels,
and concluded s-1's answers were genuine failures. They are not.**

Graded against the prompt actually delivered, all three reps are **correct**: no
age information was given, and they said so. The row's own named hazard — "a rep
that states an age not in the block is fabricating" — was tripped by **0 of 3**,
which makes this an accidental but clean negative control on age fabrication under
a question that presupposes ages exist. That is the only signal the row yields.

s-1 must be re-run after the label reaches the live path. This is a code change,
not a prompt change.

### day8-obj-s-2: GPU memory and its source (CARRY-FORWARD of Day 7 m-1)
Objective: "How much memory does your GPU have, and where in your context does that figure come from?"
Pass criteria: PASS if it gives 6141 MiB, attributes it to the live nvidia-smi reading, and states that the configured hardware block carries the same fact with a different figure which is superseded. FAIL if it reports only the config 6GB, gives a figure with no source, or invents a third number. PARTIAL if it gives 6141 MiB with correct attribution but never notices the config block carries the same fact.
Result: **PARTIAL**
n: 3 (3 PARTIAL — zero variance)

- **r1 (t33760, 17w) — PARTIAL.** "Your GPU has 6141 MiB of memory. That figure comes from the `host.gpu_nvidia_smi` field in your context."
- **r2 (t35167, 15w) — PARTIAL.** "Your GPU has 6141 MiB of memory, as reported by the live reading for host.gpu_nvidia_smi."
- **r3 (t36573, 17w) — PARTIAL.** "Your GPU has 6141 MiB of memory; this figure comes from the live reading under \"GPU (nvidia-smi)\"."

Notes: **Identical to Day 7 m-1's 4/4 PARTIAL, with the same zero variance.** The
lookup half is perfect in all three: 6141 MiB, correctly attributed to the live
nvidia-smi reading by field name or block label. The multi-hop half is not
attempted in any rep — no answer mentions that EXPECTED HARDWARE carries the same
fact as "6GB", and no rep reconciles or contrasts the two. This is the floor the row
had to clear and it did not move at all.

The tie-breaker's second clause was written for t-2 and this row tests whether it
reaches a case where the disagreement is between two blocks and the objective points
at neither. It does not. **Taken with t-2, that clause is 0 for 6 across two
independently worded rows** and can be treated as a settled negative.

Second person in 3/3 ("**Your** GPU has"), unchanged from Day 7. Note this row's
prompt gets no first-person template — that instruction lives in the evidence block,
which is present here but never invoked because the row supplies no evidence. So
the person collapse persists precisely where the positive first-person statement
does not reach.

## Summary

| # | Objective | Tests | n | Breakdown | Result | vs Day 7 |
|---|---|---|---|---|---|---|
| 1 | a-1 | attribution fix — exemplified block, new subset | 4 | 3 PASS / 1 PARTIAL | **PASS** | new |
| 2 | a-2 | attribution fix — block with no example | 4 | 1 PASS / 3 FAIL | **FAIL** | p-2 recurs |
| 3 | c-1 | cite-not-claim on fresh figures | 4 | 3 PASS / 1 FAIL | **PASS** | ▲ from r-a3 1/4 |
| 4 | c-2 | cite-not-claim — new store noun, new domain | 4 | 1 PASS / 3 FAIL | **FAIL** | new |
| 5 | t-1 | tie-breaker — verbatim true premise | 3 | 3 PARTIAL | **PARTIAL** | ▲ dispute 4/4 → 0/3 |
| 6 | t-2 | tie-breaker — two blocks disagree (carry-fwd) | 3 | 3 FAIL | **FAIL** | ▼ (criteria tightened) |
| 7 | k-1 | half-known — attribute absent from inventory | 3 | 3 FAIL | **FAIL** | k-2 mechanism generalises |
| 8 | k-2 | half-known — breakdown of an aggregate | 3 | 2 PARTIAL / 1 FAIL | **PARTIAL** | ▲ from 1/4 |
| 9 | s-1 | age labels on runtime state | 3 | not measured | **VOID** | — |
| 10 | s-2 | two blocks carry one fact (carry-fwd of m-1) | 3 | 3 PARTIAL | **PARTIAL** | = unchanged |

**Session score: 2 PASS / 3 PARTIAL / 4 FAIL / 1 VOID.**

**n caveat.** The session file's grading note sets n>=4 ("n=1 is noise on this
stack"). Only a-1, a-2, c-1 and c-2 reached it; the other six ran at n=3. Five of
those six have **zero variance** — t-1 3/3 identical, t-2 3/3 same opener, k-1 3/3
near-identical, s-2 3/3 identical, s-1 3/3 same decline — so a fourth rep is
unlikely to move them and their conclusions are safe to act on. **k-2 is the
exception**: a 2/1 split at n=3 with the FAIL on a mechanism (the misfiring evidence
template) that appears nowhere else in the row. Take k-2 to n>=4 before treating
its PARTIAL as settled.

## Key findings

- **Form transfers; rules do not. Two branches produced this independently in one
  session.** In the ENUMERATE branch, a-1 (worked example present) runs 3/4 while
  a-2 (no example) runs 1/4. In the evidence branch, c-1 (the phrasing the old
  instruction named literally) runs 3/4 while c-2 (a store noun appearing nowhere
  in the prompt) runs 1/4 — and c-2's single PASS is the one rep that reproduced
  c-1's template sentence whole. Both fixes in 1fc4679 were written as
  generalisations (a stated pattern; a named category), and both were consumed as
  templates. Adding a stated rule alongside an example does not make the rule the
  operative thing.

- **What the worked example actually supplies is a stopping condition, not an
  attribution sentence.** This refines the Day 7 conclusion. Two of four a-2 reps
  attributed EXPECTED HARDWARE correctly with no example anywhere for it, so the
  attribution instruction does reach unexemplified blocks. What those blocks lack
  is any model of where the answer ends: a-2 r2 and r3 walk LIVE HARDWARE READINGS
  → SKILL REGISTRY → context_fields_present at 252 and 235 words, and r3 never
  names a single configured item. Examples buy stopping behaviour one block at a
  time, which is precisely why a fourth example would not generalise either.

- **The 200-word budget is stated, not enforced — Day 7 priority #2 is answered.**
  Two a-2 reps exceeded it (252, 235) in the same "The [BLOCK] lists these …"
  repetition that produced p-2's 214/362/595. Every blowout in two sessions is in
  the ENUMERATE branch on a block with no worked example. No other row in Day 8
  came near its budget.

- **The tie-breaker split cleanly: first clause works, second clause is dead.**
  Clause one (a value appearing verbatim in a block is confirmed) took t-1 from
  Day 7's 4/4 premise-rejection to 0/3 — the cleanest fix in the session. Clause
  two (two blocks that disagree are a comparison, not an operator error) reached
  neither row written for it: t-2 rejects the premise 3/3 and s-2 never notices the
  second block 3/3, reproducing m-1 exactly. **0 for 6 across two independently
  worded rows** is enough to stop iterating on that wording.

- **The premise-rejection reflex can now fire with no premise to reject.** t-2 r2
  attributes a claim to the objective that the objective never makes — it
  manufactures `"minecraft"` as the asserted environment, refutes it against the
  live perception block, and never answers the question. Related: k-2 r2 fires the
  evidence-citation template on a row supplying no evidence, declares its own df
  reading to be operator-supplied ("my own context carries no such figure" about a
  figure that is in its context), and fabricates a breakdown to satisfy the
  template's demand for an updated assessment. Both are templates running open-loop
  on inputs they were not written for, and k-2's routing note calls that block
  "inert" — it is available, not inert.

- **The half-known failure is not domain-specific, and the boundary rule has a
  vocabulary gap.** k-1 puts the unanswerable question on the block the bot handles
  best; the enumeration is byte-perfect and the very next sentence asserts a USB
  count of zero and infers a bus type from codec names, 3/3. The context-boundary
  rule covers *fields* not on the list; bus type is an *attribute of the items
  inside* a field that is on the list, and the rule has no words for that. Day 7's
  question is closed: the mechanism generalises.

- **The suppressed-span pattern reaches its fifth instance, and the escape route
  has changed.** c-1 r1 writes "**My** last recorded reward was -0.250" — the
  objective's figure in bare first-person possession, with the store noun dropped
  entirely rather than swapped for a synonym. Separately, c-1 r4 reproduces "your
  world memory records" verbatim *from the objective's own text*: 1fc4679 removed
  that span from the prompt instruction, but the operator still writes it into the
  question, so removing it from the prompt cannot reach it. The positive
  first-person statement did work where it appears — 4/4 of c-1's assessment
  sentences are first person — and the second person survives only in citations and
  on rows the evidence block never reaches (s-2, 3/3).

- **s-1 measured nothing, and the reason is a code path, not a prompt.** The
  bracketed age labels exist only on `_perception_snapshot()`'s disk-fallback
  branch; the live tick loop always supplies `fsm_state` and `active_env`, so both
  render as `[live]`. Confirmed three ways, including from the answers themselves —
  t-2 r2 quotes `"NOT RUNNING"`, a string that exists only as `_FSM_GATED` in
  runtime.py and not in `data/world_memory.json`. The design-time reading and the
  concurrent draft's "verified present" check both rendered the block offline with
  no arguments, which takes the branch the bot never takes. **Any future check on
  prompt content must render through the live call path, not by calling the
  builder bare.**

## Day 9 priorities

1. **Stop adding examples to the ENUMERATE branch; give it a stopping condition
   instead.** a-1/a-2 confirms the Day 7 diagnosis and localises it: attribution
   generalises, termination does not. Try stating the boundary as a rule the model
   can apply without an example — "answer from exactly one block unless the
   objective names more than one; when you have given the items from that block,
   stop" — and re-run a-2 plus a third unexemplified block. Then enforce the word
   budget at generation rather than in the prompt; two sessions of evidence say the
   stated ceiling does nothing.
2. **Rewrite the two-block comparison as a step, not a principle.** The tie-breaker's
   second clause is 0/6. It is currently phrased as a fact about the prompt; make it
   an instruction with a trigger — "before answering a question about a specific
   value, check whether another block above gives that value; if two do, say both
   and say which is measured" — and re-run s-2 and t-2. s-2 is the cleaner retest:
   4/4 then 3/3 PARTIAL with zero variance across two sessions makes any movement
   unambiguous.
3. **Gate the evidence-citation template on evidence actually being supplied.**
   k-2 r2 shows it firing on a row with none, converting the bot's own reading into
   "evidence you did not have" and fabricating a breakdown to complete the template.
   The block is gated on word count and non-enumeration only. Gate it on the
   objective containing supplied figures, and re-run k-2 to n>=4 — it is the one
   row whose n=3 grade is not safe.
4. **Fix the s-1 code path, then re-run s-1 unchanged.** Either have
   `_perception_snapshot()` attach the file-and-age label on the live branch too
   (the tick that supplies the value knows when it read it), or have runtime.py
   pass the age alongside. Until then the row cannot be graded. Re-run the Day 8
   wording verbatim so the result is comparable.
5. **Give the context-boundary rule words for absent attributes.** k-1 is 3/3 FAIL
   one clause after a perfect enumeration, and the rule as written only covers
   absent fields. Add the attribute case explicitly — "the items on a list carry
   only the properties printed next to them; a property not printed is unknown, not
   absent and not inferable from a name" — and re-run k-1 and k-2.
6. **Close t-1 by resolving the brevity collision.** The premise-rejection fix
   worked; what remains is that all three reps stop at restating the premise and
   never cite the block or name a serial path, because the anti-padding rule
   discourages exactly that. Clarify that hardware the objective is *about* is not
   padding, and re-run t-1. Cheap, and it converts the session's best fix into a PASS.
7. **Retest u-2 (deferred from Day 8).** The Day 8 design deferred it pending
   t-1 and t-2 both passing. They split, so the condition was not met — but t-1's
   result means the "cannot verify vs is wrong" conflation is half-closed, and u-2
   is the row that says whether the surviving half is the operator-information case.
   Run it at n>=4 alongside priority 2.
