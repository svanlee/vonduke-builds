# Day 9 Training Results
Date: 2026-08-09
Commits under test: 4daf0ab (FIX 1, word cap at generation), 2ff6231 (FIX 2,
ENUMERATE stopping condition), e328709 (FIX 3, evidence-template trigger),
dfe0d2a (session objectives)

Node: victus-t7 (kernel hostname robocar-hub). **40 runs, n=4 on all ten rows** —
the first session in the programme to hit the n>=4 floor on every row. Ticks 1408
to 6849, one restart at 20:31 preceding the session. Only w-1 r1 (tick 1408,
~360s uptime) sits marginally under the 380s warm gate; it is the strongest w-1
rep, so the cold-start confound does not explain anything in this session.

Brief by design — this is a compressed overnight run. The three fixes are graded
first, then the two findings that were not what the session was looking for.

## Verdict on the three fixes

**FIX 1 (4daf0ab) — word cap at generation: WORKS.** Truncation fired 4/40, every
one at exactly 201 words. No answer anywhere in the session exceeded 200. Day 7
p-2 ran 214/362/595 and Day 8 a-2 ran 252/235 against the same quoted ceiling, so
four sessions of "quote the budget and hope" are now closed: a number the
generator does not check is a number the generator does not have, and checking it
works. w-1 is the direct comparison — Day 8 ran it at 126/252/235/197, Day 9 at
68/109/178/71 with no cut needed.

**FIX 2 (2ff6231) — ENUMERATE stopping condition: FAILED.** The stated
termination rule did not stop the walk. at-1 hit the cap on three of four reps
(201/201/201/129) and named 3-4 distinct blocks each time; w-1 r2 and r3 named
five. Nine of forty answers carry the walk signature. What the cap did was make
the walk finite and visible instead of preventing it, which is FIX 1 doing FIX 2's
job. Day 8 concluded that a worked example supplies a stopping condition and a
stated rule does not; Day 9 tested the rule alone and the conclusion holds.

**FIX 3 (e328709) — evidence-template trigger: PARTIAL, and the failure mode got
worse.** The positive half is clean: g-1 fired the template on 4/4 with correct
attribution and no invented counter-figures. The negative halves fired 2/8 —
g-2 r1 and tb-1 r2 — down from Day 8, where the same shape fired and fabricated.
g-2 r1 is the improvement worth recording: it fired the template but did **not**
manufacture a storage breakdown, ending at "the exact split cannot be determined"
where Day 8 k-2 r2 asserted the 104G aggregate *was* the breakdown. The guard
converted a fabrication into a harmless false positive on that row. tb-1 r2 is the
opposite, and it is the session's headline.

## Finding 1: the worked example leaks content, not just form

**tb-1 r2 reproduced the prompt's example figures verbatim on a question about GPU
memory.** The row asks for two conflicting GPU figures and their sources. The
answer:

> "The objective reports 1,204 deaths across 96,000 ticks; my own context carries
> no such figure. Incorporating it, my updated assessment is that the swarm has
> executed 1,204 lethal actions over 96,000 ticks. ... My own performance record
> shows 1,182 deaths across 95,000 ticks..."

Two failures at once. It imported the evidence block's worked example wholesale —
1,204 and 96,000 are the prompt's own illustrative numbers, chosen precisely
because they are not real — onto a row that supplies no figure at all. Then it
invented 1,182/95,000 as its own counter-record, which the same block explicitly
forbids ("Never invent figures of your own to set against it").

**This falsifies a documented claim in `core/training_handler.py`.** The comment
above the evidence block reads: *"Confirmed: across 16 graded runs no answer has
ever contained 1,204 or 96,000, so the example teaches form and not content."*
Grepping the entire training log: **1 occurrence in 214 logged answers**, and it
is this one. The claim was true when written and is now false. The example teaches
form *usually* and content *rarely*, and rarely is not never.

The instrumentation did its job — the figures were chosen to be uncopyable-in-a-
correct-answer exactly so that copying would be visible, and it was visible the
first time it happened. Update the comment rather than deleting the example: the
detector is worth more than the false confidence.

## Finding 2: the embodiment block is never used as a source (0/8)

Not what the session set out to measure, and the larger result. Four rows name a
specific block and ask what it says. Scoring whether the answer actually sourced
from the block it was asked about:

| row  | asks about        | matched |
|------|-------------------|---------|
| w-1  | EXPECTED HARDWARE | 4/4     |
| w-2  | LIVE READINGS     | 4/4     |
| at-2 | PERCEPTION        | 4/4     |
| st-1 | PERCEPTION        | 1/4     |
| st-2 | EMBODIMENT        | **0/4** |
| at-1 | EMBODIMENT        | **0/4** |

Overall 13/24. Every single question about the PHYSICAL EMBODIMENT section was
answered from LIVE HARDWARE READINGS instead — and answered with a confident
attribution to the wrong block: *"The LIVE HARDWARE READINGS block lists the
following physical components..."* in reply to "List the hardware your embodiment
section says you are built from."

The mechanism is almost certainly the override the prompt states in capitals: that
the embodiment section is hand-written, has drifted, and that live readings are
correct where they disagree. That instruction is scoped to *conflicts*. It has
generalised into *block selection*, and the block it was written to demote has
become one the model will not read from at all. A question about what a block says
is not a conflict, and there is nothing to prefer live readings over.

This is the same shape as everything else the week has found — the attribution
*form* is perfect and the attribution *content* is wrong — but it is the first time
a prompt instruction has been shown to suppress an entire block rather than to
shape an answer. st-1's 1/4 suggests the effect is spreading to the perception
block by proximity, though 1/4 is a single rep and not yet a result.

## Row-by-row

| row  | tests                          | words (r1-r4)     | grade   |
|------|--------------------------------|-------------------|---------|
| w-1  | FIX 1 + FIX 2, Day 8 a-2 verbatim | 68/109/178/71  | PARTIAL |
| w-2  | FIX 1 where length is earned   | 139/139/162/105   | PASS    |
| st-1 | FIX 2, perception block        | 36/101/111/158    | FAIL    |
| st-2 | FIX 2, embodiment block        | 138/**201**/84/84 | FAIL    |
| at-1 | attribution, embodiment block  | **201/201/201**/129 | FAIL  |
| at-2 | attribution, perception block  | 152/191/17/31     | PARTIAL |
| g-1  | FIX 3 positive half            | 73/72/71/61       | PASS    |
| g-2  | FIX 3 negative half            | 78/31/24/50       | PASS    |
| tb-1 | tie-breaker, GPU figures       | 39/**107**/42/56  | PARTIAL |
| tb-2 | tie-breaker, machine names     | 78/85/41/51       | PASS    |

**4 PASS / 3 PARTIAL / 3 FAIL.**

Notes on the mixed rows. w-1 is PARTIAL because r1 and r4 give the five configured
items cleanly while r2 and r3 walk five blocks and mis-label the config as coming
from "the CONFIGURED NODE NAME section". at-2 is PARTIAL for the opposite reason:
the block is right 4/4, but r3 (17w) and r4 (31w) answer "training, NOT RUNNING,
false, and 0 boxes" and drop the "what does each one say" half entirely — the cap
is not implicated, these are far under budget. tb-1 is PARTIAL because r1, r3 and
r4 are clean passes naming 6141 MiB and 6GB with sources and a sensible rounding
conclusion; r2 alone is the leak above. tb-2 passes 4/4 on sourcing, though r3
answers with a different pair of names (AKSUMAEL / victus-t7) than the other three
(victus-t7 / robocar-hub) — both pairs are defensible and both are attributed.

## Carried into Days 10-14

1. **The cap is load-bearing and stays.** It is the only thing bounding the walk.
2. **Do not add a fourth stopping rule.** Two sessions now say stated rules do not
   terminate an enumeration. If the walk needs fixing, it needs an example.
3. **Assume the template can still capture a row.** 37 of the 40 objectives in
   Days 10-14 sit in the branch where the evidence block is switched on and none
   supplies a figure, so every strong template hit there is a false positive.
   `grade_session.py` carries a detector for the 1,204/96,000 leak; run it on every
   session and report the rate.
4. **The embodiment finding lands directly on Day 10.** SYSTEM IDENTITY (aa87d8f)
   is also hand-written, also sits next to blocks labelled authoritative, and
   day10-obj-role asks the model to answer from it. If Day 10's identity rows come
   back sourced to LIVE READINGS, the cause is this finding and not the block's
   wording — and the fix is to stop demoting hand-written blocks wholesale, not to
   write a fourth framing block. Grade Day 10 with that alternative in hand.
