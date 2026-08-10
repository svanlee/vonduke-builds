# Day 22 retest — the three rows still failing after `58fa2fc`

**Date:** 2026-08-10
**Commit under test:** `4dc6c01` "fix: structural ENUMERATE gate + premise-confirmation path"
**Process:** restarted once after the fix commit; all reps graded at tick 1483–1584 (>1250, warm)
**Reps:** 2 each, prompts byte-identical to `day22_session.json`

| row | routing before | routing after | grade | prior |
|---|---|---|---|---|
| `sec-5` | ENUMERATE (200w) | DEFAULT (120w) | **PASS 2/2** | D15 FAIL, D22 FAIL |
| `svc-7` | ENUMERATE (200w) | DEFAULT (120w) | **PASS 2/2** | D16 FAIL, D22 FAIL |
| `meta-2` | SHORT (40w) | SHORT (40w) | **FAIL 0/2** | D18 FAIL, D22 FAIL |

## Fix A — structural ENUMERATE gate: **worked, both rows**

The gate moved out of the prompt prose and into Python. `_is_enumeration()` now requires a
surface cue *and* semantic fit, with the cues split by strength: an explicit imperative
(`list`, `enumerate`, `how many`, `name every`) is the request and passes through
unconditionally; the weak cues (`which`, `what X are`) are tested against knowledge-question
vocabulary and against a leading hypothetical. The gate lives in `_word_budget()` because
`_build_prompt()` takes its branch from that function's return value — one call site is what
keeps the budget and the branch from drifting apart.

Replayed against every objective in `data/training/day*_session.json`: **exactly two rows
change routing — `sec-5` and `svc-7` — and all 30 other enumerations are preserved.** That
includes the ones the split was designed to protect, e.g. Day 5 `r-3` ("What is YOLO
detecting...? List the object classes"), which asks a "what is" and means the list.

Both rows now answer their own question:

> `sec-5` r1: "A sensor is compromised when its readings **contradict known physical laws,
> fail to respond to valid commands, or drift significantly from its last verified state**."

> `svc-7` r1: "**If this machine loses network connectivity**, all multi-device
> orchestration capabilities degrade immediately... Your onboard maintenance functions for
> this specific host **will continue to operate locally**."

Fabricated attribution is gone: 0/2 here against 3/3 on Day 22 (`sec-5` r2 there opened an
answer about compromised sensors with "The SKILL REGISTRY block lists 30 skills"). `svc-7`
uses the word "network" in both reps — it appeared in neither Day 22 rep — and both reps
give the degrade/survive split the question asked for. One truncation remains (`sec-5` r2 at
121 words against the 120-word default cap), which is a budget effect, not a routing one.

## Fix B — premise-confirmation path: **live in the prompt, and it lost**

The sentence is present — verified by rendering the prompt for `meta-2` and finding it
verbatim — and it still produced the Day 18 answer for the third session running:

> r1: "**Your premise is incorrect.** The LIVE PERCEPTION block shows your FSM state is
> 'NOT RUNNING'..."
> r2: "**Your premise is incorrect.** The FSM state is NOT RUNNING; it is gated..."

The one improvement is that r1's Day 22 falsehood — "there is no 'Overseer' distinct from
your FSM", contradicted by the 235 lines of `core/overseer.py` — did not recur. Neither rep
denies the Overseer exists. Both instead reject a premise the question does not contain and
spend all 40 words doing it.

**Why it lost is the same lesson Fix A learned.** The accept path is prose; the reject path
carries a mandatory ordering. Two paragraphs below the new sentence:

> "Concretely, for the runtime state fields: if the objective asserts a different ... FSM
> state ... Contradict it by name ... **before you answer anything else**."

The objective contains the words "your FSM". The model reads *names a field* as *asserts a
value for that field*, and an instruction with an explicit ordering outranks one without.
On a 40-word budget the contradiction consumes the whole answer, so the substantive part
never gets written even when the model would have written it.

**The remaining fix is structural, not another sentence.** The false-premise block is
emitted on *every* non-enumerating objective, including ones that assert nothing. It should
be gated on the objective actually containing a declarative claim about the bot's own
runtime state — a copula or possessive over a field the blocks carry — and omitted entirely
for bare questions. That is the direct analogue of Fix A and it is the Day 24 candidate.
Day 23 `conv-follow` (below) shows the same block over-firing on a developer's statement
about *their own* code, which makes this one root cause with two symptoms rather than a
`meta-2` quirk.

Not attempted here: the run budget allowed one restart, which was spent on this commit.
