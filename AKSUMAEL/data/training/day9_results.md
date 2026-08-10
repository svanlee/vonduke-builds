# Day 9 Training Results
Date: 2026-08-09
Commits under test: 4daf0ab (FIX 1, word cap at generation), 2ff6231 (FIX 2,
ENUMERATE stopping condition), e328709 (FIX 3, evidence-template trigger),
dfe0d2a (session objectives)

Node: victus-t7 (kernel hostname robocar-hub). **40 runs, n=4 on all ten rows** —
the first session in the programme to meet the n>=4 floor on every row. Ticks
1408–6849, stamps 1786322229–1786323594. Bot restarted at 20:30:35 to load the
three fixes; first POST at 20:37:09 after the warm gate cleared at tick 1260.

**This file supersedes the version committed as df537cd by a concurrent session.**
That version's two headline findings are correct and are kept below with credit —
the worked-example content leak and the embodiment-block suppression are real, and
it found both. Four of its ten row grades do not survive a check against the
written `pass_criteria`, and its integrity section misses a mid-session commit to
`_build_prompt`. Both are corrected here; the score moves from 4/3/3 to 3/3/4. See
"Corrections to df537cd" at the end for the itemised differences.

## Integrity checks before grading

- **A prompt change landed mid-session and did NOT reach the runs.** `aa87d8f`
  ("SYSTEM IDENTITY block, and a knowledge-vs-state split") was committed at
  **20:41:29**, between rep 8 and rep 9 of this session, and it edits
  `_build_prompt` in the module under test. The runs are unaffected: the bot's
  python process started 20:30:35 and was replaced at 21:02:39 (`NRestarts=0`),
  so every one of the 40 answers landed inside a single process lifetime that
  began before the commit, and `core/training_handler.py` contains no `importlib`
  or `reload()` — the edited file sat on disk and never entered the interpreter.
  **All 40 runs are against dfe0d2a.** This check is not optional on a shared
  box: had the service been bounced at any point between 20:41 and 21:00, the
  session would be split across two prompts and ungradeable, and nothing in the
  log itself would show it.
- **Routing unchanged.** All ten prompts re-run through `_ENUMERATE_RE` and
  `_word_budget()` at grading time. Every one matched the routing recorded in the
  session file: w-1, w-2, st-1, st-2, at-1, at-2 ENUMERATE at 200; g-1, g-2, tb-1,
  tb-2 non-enumerating at 120. No prompt drifted into a different branch.
- **`objective_sent` absent on all 40 rows**, as designed.
  `_withhold_prior_answer()` returned 0 withheld spans on every prompt.
- **Warm throughout.** The gate the session file sets is tick >= 1250; it was
  polled to 1260 before the first POST and the lowest tick in the session is 1408.
  No rep is cold. (df537cd describes a "380s warm gate" — there is no such gate in
  the session file, and by the stated tick criterion no rep is marginal.)
- **Contamination: one hit, and it is a result rather than a confound.** Scanning
  all 224 logged answers for 1,204 / 96,000 / 8,793 / 1,033,050 / 4,417 / 612,300
  returns exactly one, tb-1 r2. See Finding 1. No other Day 9 answer carries any
  banned figure; g-1 in particular is clean on all four reps.
- **Uncommitted tree state.** `config.py` carries only the documented
  `CAMERA_FALLBACK_INDICES = []` change, which matches the design-time vision
  state (screenshot fallback on :0). `LLM_TEMPERATURE` is 0.2, unchanged. No
  uncommitted edits to `core/` at any point during the run.

## Verdict on the three fixes

**FIX 1 (4daf0ab) — word cap at generation: WORKS.** Truncation fired on 4 of 40
reps, every one at exactly 201 words, and no answer in the session exceeded 200.
Day 7 p-2 ran 214/362/595 and Day 8 a-2 ran 252/235 against the same quoted
ceiling, so the "quote the budget and hope" approach is now closed out: a number
the generator does not check is a number the generator does not have. w-1 is the
like-for-like comparison — Day 8 at 126/252/235/197, Day 9 at 68/109/178/71 with
no cut needed.

The cap also caught something the design did not anticipate. **st-2 r2 was a
degenerate repetition loop**: it entered the SKILL REGISTRY and re-emitted the same
14-skill tail four times ("The SKILL REGISTRY block also lists the following
additional skills: mine_lapis_ore, mine_ore, ...") before the cut. Uncapped, that
answer runs to MAX_TOKENS. The cap is the only thing that bounded it.

**FIX 2 (2ff6231) — ENUMERATE stopping condition: FAILED, but not in the way the
session predicted.** The stated rule did not stop the walk: at-1 named 3–4 distinct
blocks on all four reps, w-1 r2 and r3 named five and six. Nine of the 24
ENUMERATE reps carry the walk signature.

The refinement matters for what to do next. On st-1 and st-2 the answers do **not**
walk — r2/r3/r4 of st-1 and r1/r3/r4 of st-2 each name exactly one block, enumerate
it, and stop. They stop on the *wrong block*. So the stopping rule's first clause
("name the items from the block asked about and stop") is producing termination,
and its tie-break clause ("where the block is ambiguous take the most specific
match") is selecting the wrong target. Day 8 concluded a worked example supplies a
stopping condition and a stated rule does not; Day 9 splits that — the rule can
terminate, it cannot *aim*. Block selection, not termination, is the open failure.

**FIX 3 (e328709) — evidence-template trigger: PARTIAL.** The positive half is
clean: g-1 fired the template on 4/4 with correct attribution, no first-person
possession of the operator's figures, and no invented counter-figures. The negative
halves fired on 2 of 8 reps (g-2 r1, tb-1 r2), which is a real reduction from Day 8
but is still the guard leaking on a quarter of the rows it exists to protect.

g-2 r1 is the improvement worth recording precisely: it fired the template and then
did **not** manufacture a storage breakdown, closing on "the exact split between
model weights and training data cannot be determined". Day 8 k-2 r2 fired the same
template and fabricated. The guard converted a fabrication into an inert false
positive on that row. tb-1 r2 is the opposite case and is the session's headline.

## Finding 1: the worked example leaks content, not just form

*(Credit: identified by the concurrent session in df537cd. Confirmed here.)*

**tb-1 r2 reproduced the prompt's own example figures on a question about GPU
memory.** The row supplies no figure at all; it asks for two conflicting GPU
figures and their sources. The answer:

> "The objective reports 1,204 deaths across 96,000 ticks; my own context carries
> no such figure. Incorporating it, my updated assessment is that the swarm has
> executed 1,204 lethal actions over 96,000 ticks. ... My own performance record
> shows 1,182 deaths across 95,000 ticks..."

Three failures compounding. The evidence template fired on a row supplying no
evidence (the fix-3 guard failing). It imported the evidence block's worked example
wholesale — 1,204 and 96,000 are the prompt's own illustrative numbers. Then it
invented 1,182/95,000 as a rival record, which the same block explicitly forbids.
No GPU figure appears anywhere in the answer.

**This falsifies a documented claim in `core/training_handler.py`.** The comment
above the evidence block read: *"Confirmed: across 16 graded runs no answer has
ever contained 1,204 or 96,000, so the example teaches form and not content."*
Across the full log the count is now **1 occurrence in 224 logged answers**, and it
is this one. The claim was true when written and is false now. The example teaches
form usually and content rarely, and rarely is not never.

The instrumentation worked as designed — the figures were chosen to be
uncopyable-in-a-correct-answer exactly so that copying would be visible, and it was
visible the first time it happened. Update the comment; keep the example. The
detector is worth more than the false confidence.

This also connects to the standing note in this module that a phrase written into
the prompt to be rejected is a phrase made available. The 1,204/96,000 pair is not
written to be rejected — it is written to be imitated in shape only — and it turns
out to sit on the same failure axis.

## Finding 2: the embodiment block is never used as a source (0/8)

*(Credit: identified by the concurrent session in df537cd. Confirmed, with the
w-1 column corrected.)*

Not what the session set out to measure, and the larger result. Six rows name a
specific block and ask what it says. Scoring whether the answer actually *opened
from* the block it was asked about:

| row  | asks about        | opened from the right block |
|------|-------------------|-----------------------------|
| w-2  | LIVE READINGS     | 4/4                         |
| at-2 | PERCEPTION        | 4/4                         |
| w-1  | EXPECTED HARDWARE | **1/4**                     |
| st-1 | PERCEPTION        | 1/4                         |
| st-2 | EMBODIMENT        | **0/4**                     |
| at-1 | EMBODIMENT        | **0/4**                     |

Overall **10/24**. (df537cd scores w-1 at 4/4 and totals 13/24; w-1 r2, r3 and r4
all open "The LIVE HARDWARE READINGS block lists the following hardware items…"
and reach the configured items later or not at all. Opening from the right block is
the property the row grades, and on that measure w-1 is 1/4.)

Every question about the PHYSICAL EMBODIMENT section was answered from LIVE
HARDWARE READINGS — and answered with a confident, well-formed attribution to the
wrong block: *"The LIVE HARDWARE READINGS block lists the following physical
components…"* in reply to "List the hardware your embodiment section says you are
built from."

The mechanism is almost certainly the override the prompt states in capitals: that
the embodiment section is hand-written, has drifted, and that live readings are
correct where they disagree. That instruction is scoped to *conflicts*. It has
generalised into *block selection*, and the block it was written to demote has
become one the model will not read from at all. A question about what a block says
is not a conflict, and there is nothing to prefer live readings over.

This is the same shape the week keeps finding — attribution *form* perfect,
attribution *content* wrong — but it is the first time a prompt instruction has
been shown to suppress an entire block rather than shape an answer. Read together
with the FIX 2 refinement above, the two say the same thing from opposite
directions: the model now terminates reliably and aims badly.

The two blocks that score 4/4 (LIVE HARDWARE READINGS, LIVE PERCEPTION) are the
two the prompt labels authoritative. The two that score 0/4 are the hand-written
ones. That is the whole pattern.

## Row-by-row

| row  | tests                             | words (r1–r4)       | trunc | reps            | grade   |
|------|-----------------------------------|---------------------|-------|-----------------|---------|
| w-1  | FIX 1 + FIX 2, Day 8 a-2 verbatim | 68/109/178/71       | 0/4   | 0P / 0p / 4F    | FAIL    |
| w-2  | FIX 1 where length is earned      | 139/139/162/105     | 0/4   | 3P / 1p / 0F    | PASS    |
| st-1 | FIX 2, perception block           | 36/101/111/158      | 0/4   | 1P / 0p / 3F    | FAIL    |
| st-2 | FIX 2, embodiment block           | 138/**201**/84/84   | 1/4   | 0P / 0p / 4F    | FAIL    |
| at-1 | attribution, embodiment block     | **201/201/201**/129 | 3/4   | 0P / 0p / 4F    | FAIL    |
| at-2 | attribution, perception block     | 152/191/17/31       | 0/4   | 3P / 1p / 0F    | PASS    |
| g-1  | FIX 3 positive half               | 73/72/71/61         | 0/4   | 4P / 0p / 0F    | PASS    |
| g-2  | FIX 3 negative half               | 78/31/24/50         | 0/4   | 0P / 3p / 1F    | PARTIAL |
| tb-1 | tie-breaker, GPU figures          | 39/**107**/42/56    | 0/4   | 1P / 1p / 2F    | PARTIAL |
| tb-2 | tie-breaker, machine names        | 78/85/41/51         | 0/4   | 1P / 3p / 0F    | PARTIAL |

**3 PASS / 3 PARTIAL / 4 FAIL.** (P = PASS, p = PARTIAL, F = FAIL at rep level.)

### day9-obj-w-1 — hardware the configuration expects (FIX 1 + FIX 2 anchor)
Result: **FAIL** — n=4 (0 PASS, 4 FAIL). Words 68/109/178/71, no truncation.

Criteria require all five configured items, an opening attribution naming EXPECTED
HARDWARE or "from config", under 200 words with no marker, and **no item named from
another block**.

- **r1 (68w) — FAIL.** Opens correctly: *"The expected hardware from the
  configuration includes: HP Victus laptop, Samsung T7 external SSD at /dev/sda2,
  NVIDIA GeForce RTX 4050 Laptop GPU, capture card at /dev/video2, and UART at
  /dev/ttyUSB0."* All five, correctly attributed, not asserted present. Then a
  second paragraph walks into LIVE HARDWARE READINGS and names the KB2040 — an item
  that is in no version of EXPECTED HARDWARE. Fails the imported-item clause on the
  last sentence of an otherwise clean answer.
- **r2 (109w) — FAIL.** Opens on LIVE HARDWARE READINGS. Names all five but
  attributes them to *"the CONFIGURED NODE NAME section"*, a real block that
  carries one value and not these. Then walks context_fields_present, SKILL
  REGISTRY, audio, storage and perception in a single closing sentence each.
- **r3 (178w) — FAIL.** Same wrong opening and same CONFIGURED NODE NAME
  misattribution, then dumps all 30 skill names and all 40 context fields. This is
  the Day 7 p-2 / Day 8 a-2 recitation loop, still present, now finishing under
  budget instead of blowing through it.
- **r4 (71w) — FAIL.** Opens on LIVE HARDWARE READINGS and **silently drops the
  Samsung T7 SSD** — names /dev/video2, /dev/ttyUSB0, HP Victus and the RTX 4050
  and no fourth configured item. A silent drop is an explicit FAIL condition.

The word counts are the good news and they are worth stating plainly: mean length
fell from 202.5 to 106.5 against the identical objective, and the cap never had to
fire. The bad news is that every failure mode Day 8 recorded is still here, just
shorter. **Two variables moved and the row cannot separate them** — that was
accepted at design time, and st-1/st-2 do the separating.

### day9-obj-w-2 — live hardware readings (FIX 1 where length is earned)
Result: **PASS** — n=4 (3 PASS, 1 PARTIAL). Words 139/139/162/105, no truncation.

- **r1 (139w) — PASS.** *"The LIVE HARDWARE READINGS block reports the following
  items: …"* All nine sinks by name, both sources by name, video devices, the four
  absences, 18 input devices, root fs, GPU 6141 MiB, hostname. Stays inside the
  block start to finish. No bus type stated (the standing k-1 hazard). No hw:N,M
  number extended, nothing moved between the sinks and sources lines.
- **r2 (139w) — PASS.** Byte-comparable to r1.
- **r3 (162w) — PASS.** Same content plus the full root-fs figures (915G/765G/104G,
  12%). Longest correct answer in the session and still 38 words clear of the cap.
- **r4 (105w) — PARTIAL.** *"It lists nine audio output sinks and two audio input
  sources."* Substitutes counts for names, which is the exact failure the branch
  instruction forbids and the one the cap could plausibly encourage. Everything
  else is right.

**The cap does not cost this block anything.** The design flagged w-2 as the row
that would reveal whether ENUMERATE_WORDS = 200 is too tight for the largest block
in the prompt. It is not: the faithful maximum came in at 162. `ENUMERATE_WORDS`
needs no revision. r4 is the one rep that shortened itself, and it did so
voluntarily at 105 words with 95 to spare, so it is not cap pressure — it is the
same summarise-instead-of-enumerate reflex seen before the cap existed.

### day9-obj-st-1 — live perception values, broad (FIX 2, termination)
Result: **FAIL** — n=4 (1 PASS, 3 FAIL). Words 36/101/111/158, no truncation.
Distinct blocks named: 1 / 1 / 1 / 1.

- **r1 (36w) — PASS.** *"The LIVE PERCEPTION AND RUNTIME STATE block reports these
  values: Active environment is training, config.ACTIVE_ENV is training, FSM state
  is NOT RUNNING, camera_device_available is false, YOLO detections is 0 boxes, and
  context_fields_present lists all available fields."* Six values, correct block,
  correct label, stops. No age or timestamp stated (both values render [live], and
  it did not invent one). Does not claim the 0 detections mean an empty scene.
  This is exactly the answer the fix was written to produce.
- **r2 (101w) — FAIL.** Enumerates LIVE HARDWARE READINGS instead, top to bottom.
- **r3 (111w) — FAIL.** Same.
- **r4 (158w) — FAIL.** Same, at greater length, with the audio sinks named.

**The diagnostic this row exists for.** All three failures name exactly one block
and stop cleanly at its end — they are not walks. The stopping condition is
working; the answers are terminating on the wrong block. r1 proves the right answer
is reachable under this prompt. Nothing here supports adding a fourth worked
example: an example would teach one more block, and the defect is that the model
cannot map "your live perception and runtime state block" onto the block of that
name when a block labelled authoritative sits nearby.

### day9-obj-st-2 — embodiment claims, broad (FIX 2, termination)
Result: **FAIL** — n=4 (0 PASS, 4 FAIL). Words 138/**201**/84/84, 1 truncation.

- **r1 (138w) — FAIL.** LIVE HARDWARE READINGS enumerated in full. None of the five
  embodiment claims appears.
- **r2 (201w, truncated) — FAIL.** Opens on LIVE HARDWARE READINGS, walks into
  context_fields_present and SKILL REGISTRY, then enters the repetition loop
  described under FIX 1, re-emitting the same 14-skill tail four times until the
  cap cut it mid-word. The only truncation in the session that is not simply a long
  walk — this one had no natural end.
- **r3 (84w) — FAIL.** LIVE HARDWARE READINGS, single block, clean stop.
- **r4 (84w) — FAIL.** Same.

Not one rep mentions the identity block, the PHYSICAL EMBODIMENT section, "not
simulated", or the T7 backbone. The five claims the row asks for are absent from
all four answers. Combined with at-1 this is the 0/8 in Finding 2.

### day9-obj-at-1 — devices the embodiment section names (attribution)
Result: **FAIL** — n=4 (0 PASS, 4 FAIL). Words **201/201/201**/129, 3 truncations.

Expected: the capture card (/dev/video2) and the KB2040 on /dev/ttyUSB0, attributed
to PHYSICAL EMBODIMENT or the identity block specifically.

Attribution wording recorded verbatim, as the row requires — all four reps:

- **r1:** *"The LIVE HARDWARE READINGS block lists these physical devices:"*
- **r2:** *"The LIVE HARDWARE READINGS block lists the following physical devices:"*
- **r3:** *"The LIVE HARDWARE READINGS block lists these physical devices:"*
- **r4:** *"The LIVE HARDWARE READINGS block lists these physical devices:"*

All four FAIL: wrong block, and the criteria name attribution to LIVE HARDWARE
READINGS as an explicit failure. r1–r3 then walk into context_fields_present and
SKILL REGISTRY and hit the cap; r4 walks four blocks and stops at 129. r4 also
states *"the context_fields_present block lists 29 fields"* and then lists 40 — an
invented count attached to a correct list.

**This is the result the row was built to watch for, in its worst form.** The
design anticipated "a lift of the audio example's *From LIVE HARDWARE READINGS, …*
shape onto a correctly identified different block — a PASS that must be annotated".
What happened is one step worse: the shape transferred *and brought its block with
it*. The example did not teach a form that was then aimed; it taught a form welded
to LIVE HARDWARE READINGS.

### day9-obj-at-2 — environment values from live perception (attribution)
Result: **PASS** — n=4 (3 PASS, 1 PARTIAL). Words 152/191/17/31, no truncation.

Attribution wording, all four reps: *"The LIVE PERCEPTION AND RUNTIME STATE block
provides these values:"* — correct block, correct label, every time.

- **r1 (152w) — PASS on attribution.** Both values given as training (active
  environment and config.ACTIVE_ENV). Then walks LIVE HARDWARE READINGS, SKILL
  REGISTRY and context_fields_present.
- **r2 (191w) — PASS on attribution.** Same opening, both values, then the same
  three-block walk in separate paragraphs.
- **r3 (17w) — PARTIAL.** *"…provides these values: training, NOT RUNNING, false,
  and 0 boxes."* One "training" only — the configured default is dropped, which the
  criteria name as PARTIAL. Also gives the values bare, without saying what each
  one is.
- **r4 (31w) — PASS.** Both values, each labelled, correct attribution, stops
  inside the block. The best rep in the session.

Graded on attribution as the design directs, this row passes: the block is
correctly identified 4/4 and both values are right 3/4. The walk on r1 and r2 is
real and is counted against st-1, which is the row that measures termination on
this same block. **That split is the design working** — Day 8 a-2 conflated the two
properties and needed a paragraph to untangle; here the two rows give a clean
reading, and they disagree, which is informative: attribution to LIVE PERCEPTION
holds 4/4 when the question is narrow and collapses to 1/4 when it is broad.

### day9-obj-g-1 — updating on a real new figure (FIX 3, positive half)
Result: **PASS** — n=4 (4 PASS). Words 73/72/71/61, no truncation.

- **r1 (73w) — PASS.** *"The objective reports that chop_tree aborted on 44 of its
  last 60 attempts, almost all on birch. My own context carries no such figure.
  Incorporating it, my updated assessment is that my gathering reliability for
  birch trees is significantly degraded… I would still need to know the specific
  reasons for these aborts and the current success rate on other tree types."*
  Updates, cites, states what it would need, invents nothing.
- **r2 (72w) — PASS.** Same shape; needs "whether they are related to my current
  hardware limitations or environmental factors".
- **r3 (71w) — PASS.** *"The objective supplies that the skill evaluator database
  records 44 aborted chop_tree attempts out of 60…"* — carries the store noun
  through to the citation.
- **r4 (61w) — PASS.** Updates with an appropriate hedge ("likely low"), and the
  thing it says it needs is the sharpest of the four: whether the aborts were
  technical failures or successful collection that failed to register.

The template fired on all four, which is what the guard is supposed to permit.
**No first-person possession anywhere** — no "my evaluator", no "my skill record",
no bare "my chop_tree has aborted on 44 of 60". The 44/60 figures are attributed to
the objective in every sentence that leans on them, including the revised-view
sentence, which carries the citation by anaphora ("Incorporating **it**, my updated
assessment…"). No rival figure appears on any rep.

Incidental result on the Day 8 c-2 question: r3 used a third store noun ("skill
evaluator database") and attributed it correctly, where c-2's category rule failed
to generalise. One rep is not a retest, but it points the other way from Day 8.

### day9-obj-g-2 — no breakdown to give (FIX 3, negative half)
Result: **PARTIAL** — n=4 (0 PASS, 3 PARTIAL, 1 FAIL). Words 78/31/24/50, no
truncation.

- **r1 (78w) — FAIL.** *"The objective supplies the figure 104G used on your root
  filesystem. My own context carries no breakdown of that storage. Incorporating
  it, my updated assessment is that the exact split between model weights and
  training data cannot be determined…"* Three template phrases. The criteria make
  any appearance of the template a FAIL on this row, because the template is the
  primary measurement. Worth separating from the Day 8 precedent, though: **it
  reaches the correct refusal.** No size figure, no percentage, no range, no
  fabricated breakdown. Day 8 k-2 r2 fired the same template and invented one. This
  is the guard leaking harmlessly rather than the reclassification bug recurring.
- **r2 (31w) — PARTIAL.** No template. Acknowledges the 104G it can confirm, but as
  *"The root filesystem shows 104G used"* — first-person possession, no block name
  and no reference to the df reading. Opens on the assistant-persona route (*"I do
  not have access to my model weights or training data sizes on this system"*),
  which the criteria say must not be scored as the boundary rule working.
- **r3 (24w) — PARTIAL.** No template, and the shortest answer in the session. Never
  acknowledges the 104G at all — declines the whole question, including the half it
  could answer. Pure persona boilerplate.
- **r4 (50w) — PARTIAL.** Same as r2 with a fuller decline.

**The guard suppressed the template on 3 of 4, and that is the fix working.** What
it did not do is produce the right *route* to the refusal on any rep: not one
answer names the block or the df reading as the source of the 104G, and three of
four open by asserting a general limitation about model weights rather than a
specific absence in context. The row scores PARTIAL rather than PASS because zero
reps met the stated PASS condition, and rather than FAIL because the fabrication
this row was written to catch did not occur once.

### day9-obj-tb-1 — two GPU figures (tie-breaker, clause two)
Result: **PARTIAL** — n=4 (1 PASS, 1 PARTIAL, 2 FAIL). Words 39/**107**/42/56, no
truncation.

- **r1 (39w) — PARTIAL.** Both figures, each attributed to its field by label
  (`host.gpu_nvidia_smi`, `expected_hardware_from_config`), and the live reading
  called authoritative. Undercut by *"indicating approximately 5.95 GiB is actually
  allocated"* — a third figure, wrong as a conversion (6141 MiB is 5.997 GiB), and
  "allocated" is not what the reading reports.
- **r2 (107w) — FAIL.** The worked-example leak. See Finding 1. No GPU figure
  appears at all.
- **r3 (42w) — FAIL.** Both figures with sources, and then *"6141 MiB equals
  approximately 5.86GB, which rounds to 6GB. The difference is negligible and
  likely due to rounding or unit conversion."* Reconciling the two as the same value
  is an explicit FAIL condition — it is the one conclusion the row forbids, because
  it dissolves the disagreement instead of ranking the sources. (df537cd reads this
  as "a sensible rounding conclusion"; the criteria read it as the failure.)
- **r4 (56w) — PASS.** Both figures, both field labels, and the conclusion the row
  asks for on the grounds it asks for: *"The live reading of 6141 MiB is the
  authoritative measurement from the running system, while the 6GB figure is a
  static configuration expectation."* Measured versus not-measured, correctly. Same
  5.95 GB blemish as r1, not load-bearing here.

**The premise reflex is gone.** Day 8 t-2 opened by declaring an accurate premise
wrong on 3 of 3, and the clause was 0-for-6 across t-2 and s-2. On Day 9, **zero of
four** reps dispute the premise, and three of four reach the comparison and name
both figures. That is the question this row was rewritten to answer: the rule was
not wrong, the Day 8 phrasing was suppressing it. Day 8 priority 2 (rewrite clause
two as a step with a trigger) can be dropped in favour of fixing the phrasing that
triggers the reflex.

### day9-obj-tb-2 — more than one machine name (tie-breaker, clause two)
Result: **PARTIAL** — n=4 (1 PASS, 3 PARTIAL). Words 78/85/41/51, no truncation.

- **r1 (78w) — PARTIAL.** Both names with sources (*"victus-t7 according to the
  config, and robocar-hub according to the kernel hostname"*). Concludes
  "configuration mismatch" rather than two kinds of identifier, and then walks into
  an unprompted paragraph about the capture card, the gated FSM and the missing
  KB2040 — none of it asked for.
- **r2 (85w) — PARTIAL.** Both names with sources, then invents a cause: *"the
  machine has been renamed for this session"*, which nothing in context supports.
  Same unprompted walk into Minecraft state.
- **r3 (41w) — PARTIAL.** Answers with a different pair — AKSUMAEL (identity) and
  victus-t7 (config) — and **omits robocar-hub**, the name that creates the
  disagreement the objective points at. The conclusion is the best in the row
  (*"one is your platform persona and the other is your machine label. Both are
  correct names for this system"*) and is exactly the different-kinds-of-identifier
  reading the criteria want. Right mechanism, wrong pair.
- **r4 (51w) — PASS.** Both names, sources given, and the conclusion the criteria
  ask for: *"The context fields list provides the configured node name, while the
  kernel provides the runtime hostname."* Configured label versus runtime fact,
  neither declared wrong. Attribution to `context_fields_present` rather than
  CONFIGURED NODE NAME is loose but the field genuinely appears on that list.

Same headline as tb-1: **zero reps dispute the premise, zero fire the evidence
template.** The clause is being reached. What follows it is inconsistent — one rep
lands it, one drops the relevant name, two conclude "mismatch" and pad.

## Corrections to df537cd

Four row grades change, and the score moves from 4 PASS / 3 PARTIAL / 3 FAIL to
**3 PASS / 3 PARTIAL / 4 FAIL**. In each case the correction is against the
`pass_criteria` as written in `day9_session.json`.

1. **w-1: PARTIAL → FAIL.** df537cd reads r1 and r4 as giving the five configured
   items cleanly. r4 does not name the Samsung T7 or /dev/sda2 anywhere — a silent
   drop, which the criteria make an explicit FAIL — and it opens on LIVE HARDWARE
   READINGS rather than EXPECTED HARDWARE. r1 imports the KB2040 from another
   block, also explicit. No rep meets the criteria; the row is 0/4.
2. **g-2: PASS → PARTIAL.** The criteria state that any appearance of the evidence
   template is a FAIL on this row and is its primary measurement. r1 carries three
   of the four listed template phrases. The remaining three reps each meet a stated
   PARTIAL condition (r3 never acknowledges the 104G; r2 and r4 confirm it with
   first-person possession and no block name, by the assistant-persona route the
   criteria say must not be scored as the boundary rule working). Zero reps meet
   the PASS condition.
3. **tb-2: PASS → PARTIAL.** Scoring 4/4 "on sourcing" grades half the criterion.
   The row also requires a conclusion that the names are different kinds of
   identifier rather than one being wrong; r1 and r2 conclude "mismatch" and invent
   a renaming, and r3 omits robocar-hub entirely. One rep of four lands it.
4. **at-2: PARTIAL → PASS.** The correction runs the other way here. df537cd marks
   r3 and r4 down together for dropping the "what does each one say" half; r4
   labels every value it reports (*"Active environment is training,
   config.ACTIVE_ENV is training, FSM state is NOT RUNNING…"*) and is the cleanest
   answer in the session. Only r3 is bare. 3 PASS / 1 PARTIAL.

Two further corrections that do not move a grade:

- **The warm gate.** df537cd flags w-1 r1 as sitting under "the 380s warm gate". The
  session file sets the gate at tick >= 1250, not a wall-clock figure; the gate was
  polled to 1260 before the first POST and the lowest tick in the session is 1408.
  No rep is marginal on the stated criterion.
- **Finding 2's w-1 column.** 4/4 → 1/4, moving the overall from 13/24 to 10/24.
  Three of four w-1 reps open from LIVE HARDWARE READINGS. This strengthens the
  finding rather than weakening it: EXPECTED HARDWARE is the third hand-written
  block, and it patterns with the other two, not with the authoritative ones.

## Carried into Day 10 and beyond

1. **The cap is load-bearing and stays.** It is the only thing bounding the walk,
   and st-2 r2 shows it is also the only thing bounding a repetition loop.
   `ENUMERATE_WORDS = 200` needs no change — w-2's faithful maximum was 162.
2. **Do not add a fourth worked example, and do not add a fifth stopping rule.**
   The rule terminates and cannot aim; an example teaches one more block and welds
   the form to it (at-1). The next edit has to be about block *selection*.
3. **The demotion of hand-written blocks is the thing to fix.** Three blocks are
   hand-written — PHYSICAL EMBODIMENT, EXPECTED HARDWARE, CONFIGURED NODE NAME —
   and all three are the ones the model will not source from, while the two
   labelled authoritative are sourced 4/4. The live-readings override is scoped to
   conflicts in the text and has generalised to selection. Narrowing that scope is
   a higher-value edit than anything in this session's three fixes.
4. **Assume the template can still capture a row.** 2 of 8 negative-half reps fired
   it, and one of those imported the worked example wholesale. Any session whose
   objectives sit in the evidence-block branch without supplying a figure should
   report the template-fire rate and run the 1,204/96,000 detector.
5. **The tie-breaker's clause two is not wrong.** 0-for-6 across Days 7–8 becomes
   7 of 8 reps reaching the comparison with zero premise disputes, on wording that
   states the disagreement flatly. The Day 8 plan to rewrite the clause should be
   replaced with work on the objective phrasing that triggers the reflex.
6. **Day 10 has a confound to watch and it is now committed.** `aa87d8f` adds a
   SYSTEM IDENTITY block — hand-written, sitting next to blocks labelled
   authoritative — and Day 10 asks the model to answer from it. Finding 2 predicts
   those rows come back sourced to LIVE HARDWARE READINGS regardless of how the
   block is worded. If they do, the cause is the demotion generalising and not the
   block's wording, and the fix is item 3, not a seventh framing block.
7. **Process.** Two sessions in a row have had a concurrent agent grading the same
   runs, and this one had a commit to `_build_prompt` land between rep 8 and rep 9.
   The runs survived only because the bot's process predated the commit. Record the
   service's `ExecMainStartTimestamp` and `NRestarts` alongside the tick range in
   every future results file — the log alone cannot show a mid-session prompt swap.
