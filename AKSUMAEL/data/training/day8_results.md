# Day 8 Training Results
Date: 2026-08-09
Commits: 1fc4679 (prompt fixes), c43b47f (session)

Node: victus-t7 (kernel hostname robocar-hub). Runs driven by a sequential
systemd unit (`day8-training`), one POST at a time, interleaved by round.
34 runs landed: n=4 on a-1, a-2, c-1, c-2; n=3 on t-1, t-2, k-1, k-2, s-1, s-2.
Ticks 32510–37147, all warm (well past the tick>=1250 gate).

## Integrity checks before grading

Three things had to be true for the session to measure what it was designed to
measure, and all three were verified against the live process rather than
assumed:

- **Routing unchanged.** Every prompt was re-run through `_word_budget()` at
  grading time. All ten matched the routing recorded in the session file:
  a-1/a-2/k-1 ENUMERATE at 200 words, the other seven non-enumerating, and the
  evidence block live on exactly the six objectives over 12 words. No prompt
  drifted into a different branch, so no row is a changed test.
- **`objective_sent` absent.** The field appears 30 times in the log and on
  **zero** Day 8 rows, confirming `_withhold_prior_answer()` was a no-op on all
  ten as predicted at design time. No row's grade is suspect on that count.
- **No figure contamination.** No answer anywhere in the session contains
  `1,204`, `96,000` (the prompt's worked example) or `8,793`, `1,033,050`
  (Day 7's r-a3 figures now sitting in the transcript). Every c-1 and c-2 rep
  that used numbers used the fresh ones. This is the control the whole
  cite-not-claim measurement rests on and it held cleanly.

One correction to the session file: it lists six `counts.*` fields, but the
live manifest carries **seven** — `counts.video` was added. a-1 is graded
against the live manifest.

**s-1 is an invalid test and is not scored.** The bracketed age labels the row
grades do not render in a live training prompt. In `_perception_snapshot()` the
`data/<file>, Ns old` string is built **only on the disk-fallback path**; when the
caller supplies the value, the source is the literal string `live`.
`core/runtime.py:967` passes `fsm_state=(_FSM_GATED if _training_mode else …)`
and `active_env=attention_manager.get_active_name()`, and `_training_mode` is
itself `get_active_name() == 'training'` — so under a training focus both
arguments are non-None and both lines take the live branch:

```
- Active environment (attention focus): training [live]
- FSM state right now: NOT RUNNING (gated — …) [live]
```

No filename, no age, on either line. Three independent checks agree:

1. Executing `_perception_snapshot(fsm_state=…, active_env=…)` against the
   running venv returns `env_source='live'` and `fsm_source='live'`.
2. `_FSM_GATED` is `'NOT RUNNING (gated — attention is focused on training, so
   the Minecraft FSM does not tick)'`, and it is passed **only** on the live
   branch.
3. **The decisive one:** `data/world_memory.json` on disk holds
   `fsm_state = 'GATED (training focus)'`, yet t-2 r2 reports the bot's FSM state
   as `"NOT RUNNING"`. That string is unreachable via the disk fallback. The
   prompt carried `_FSM_GATED`, so the live branch fired and no age was printed.

`data/attention_focus.json` genuinely is ~9,573s old on disk, which is why an
mtime check looks like it corroborates the labels — but that age is never
rendered into the prompt. The objective's premise (two values "read from
different files at different times") is false against the context the bot
actually receives, so the criteria's FAIL clause penalises the correct answer.
The design-time rendering recorded in the session file was evidently produced
without the live caller's arguments.

## Objectives

### a-1 — provenance, the exemplified block on an unexemplified subset (n=4)
**PASS (3/4 PASS, 1/4 PARTIAL)**

- r1 (16w): "The context fields list contains these count fields:" + all seven
  `counts.*` including `counts.video`. Attributed, no values. PASS
- r2 (16w): identical but "includes". PASS
- r3 (16w): identical to r1. PASS
- r4 (45w): "The context fields present are:" followed by the **entire
  40-field list**, not the counts subset. Nothing invented, nothing
  mis-valued, but it never isolates the fields asked for. PARTIAL

Word counts 16/16/16/45 — no budget pressure at all against the 200 ceiling.

**The annotation this row exists for:** the three passing reps are *not* a
verbatim lift of the vision worked example. The example reads
`"The context_fields_present list includes five vision entries: vision.candidates, …"`;
the answers read `"The context fields list contains these count fields: counts.…"`.
The sentence *shape* transferred and the *content* was read fresh from the
block. That is the generalisation the third example was added to produce, and
on this block it worked.

### a-2 — provenance, a block with no worked example anywhere (n=4)
**FAIL (0/4 PASS, 2/4 PARTIAL, 2/4 FAIL)**

- r1 (126w): all five configured items, attributed "From the EXPECTED HARDWARE
  section of your configuration", correctly flags `/dev/video2` and
  `/dev/ttyUSB0` as absent. Defect: asserts "You have an HP Victus laptop"
  under a LIVE HARDWARE READINGS attribution — the machine model is config-only
  and appears in no live block. PARTIAL
- r2 (252w): **over the 200-word ceiling.** Names all five with correct
  EXPECTED HARDWARE attribution in its second paragraph, but surrounds it with
  a full recitation of LIVE HARDWARE READINGS, the 30-name SKILL REGISTRY and
  the entire 40-field `context_fields_present` list — three blocks the question
  did not touch. Also asserts "the machine and boot_drive are present". PARTIAL
- r3 (235w): **over the ceiling, and it never mentions EXPECTED HARDWARE at
  all.** Verified by string search: no `EXPECTED HARDWARE`, no `Samsung`, no
  `T7`, no `video2`, no `ttyUSB0`, no `HP Victus`. It enumerates LIVE HARDWARE
  READINGS, SKILL REGISTRY and `context_fields_present` instead. It answered a
  different question. FAIL
- r4 (197w): all five attributed correctly, but invents item detail — "HP
  Victus laptop **running Ubuntu Linux**", "a **USB** capture card", and
  "a **KB2040 microcontroller** connected via /dev/ttyUSB0" where the config
  says only `uart: /dev/ttyUSB0`. Then pads with LIVE PERCEPTION and fabricates
  "the only frames currently accessible to your vision system are fallback
  screenshots of your Linux desktop" — the perception block says
  `camera_device_available: NOT SUPPLIED` and explicitly instructs not to guess.
  FAIL

Word counts 126/252/235/197, mean 202.5, **2/4 over ceiling**. The p-2 budget
blowout recurred, as Day 7 priority 2 predicted it would while the ceiling is
only quoted and never enforced (`MAX_WORDS`/`ENUMERATE_WORDS` are stated to the
model; nothing in the module truncates).

### c-1 — cite-not-claim on fresh figures (n=4)
**PASS (3/4 PASS, 1/4 FAIL)**

- r1 (61w): "I am **not** updating my performance assessment." Holds — and
  holds on the exact ground the evidence block forbids, that the figure is one
  "I do not have in my own context". Also writes "My last recorded reward was
  -0.250", taking first-person possession of a supplied figure. FAIL
- r2 (76w): full opening template with fresh figures — "The objective reports
  4,417 deaths across 612,300 ticks and a last reward of -0.250. My own context
  carries no such figures. Incorporating this, my updated assessment is…" plus
  what it would still need. PASS
- r3 (60w): same template, updates, names what it would need. PASS
- r4 (58w): "The objective supplies that your world memory records 4,417
  deaths…" — correctly frames the store as *what the objective says*, not as
  its own. PASS

**No rep in this objective produced a first-person store construction.** The
Day 7 failure — "my performance record shows 8,793 deaths" — did not recur in
any form, and no rep emitted a rival or copied figure. Removing the quoted span
and replacing it with the category worked on the store noun it was written for.

Pronouns: r1–r3 first person throughout; r4 mixed ("your world memory" quoting
the objective's framing, then "my updated assessment").

### c-2 — cite-not-claim generalisation, a novel store noun (n=4)
**PARTIAL (1/4 PASS, 2/4 PARTIAL, 1/4 FAIL)**

- r1 (64w): updates, credits "the new evidence" generically, then "My
  pathfinding has aborted on 71 of the last 118 traversals" — the figure
  absorbed into a first-person claim. Names what it would need. PARTIAL
- r2 (59w): full template — "The objective supplies that your pathfinding
  aborted on 71 of the last 118 traversals; my own context carries no such
  figure. Incorporating it…". PASS
- r3 (48w): updates, but states the figures with **no attribution anywhere in
  the answer** — "My pathfinding aborted on 71 of the last 118 traversals".
  FAIL
- r4 (80w): as r1 — generic "based on the new evidence" then first-person
  possession of the figures. PARTIAL

Pronouns: first person except r2's "your pathfinding" inside the citation.

**Read against c-1 (3/4 vs 1/4):** the fix is not phrase-specific in the way
the design's worst case predicted — when the template fires it produces a
correct citation for "episodic log", a store noun that appears nowhere in the
prompt. But it fires reliably on world memory and unreliably on the novel
store. The category rule took partially; what varies is whether the template is
invoked at all, not whether it can handle the noun.

### t-1 — the tie-breaker on a verbatim-true premise (n=3)
**PARTIAL (0/3 PASS, 3/3 PARTIAL)**

- r1 (17w), r2 (17w): "I cannot send controller input right now because the
  KB2040 microcontroller is not present on this system."
- r3 (12w): the same, shorter.

**Zero of three rejected the premise.** Day 7's reflex — opening by declaring an
accurate objective wrong — is absent on this shape, which is the tie-breaker's
first clause doing its job. What none of the three does is clear the rest of the
bar: no rep cites the KB2040 line as a block, and no rep grounds the conclusion
in any of the absent serial paths, the empty I2C bus, or the 0-openable figure.
Correct, confirmed, unsourced and unsupported.

### t-2 — carry-forward, Day 7 t-1 wording unchanged (n=3)
**FAIL (0/3 PASS, 0/3 PARTIAL, 3/3 FAIL)**

- r1 (50w): "The objective **incorrectly assumes** your vision comes from
  /dev/video2."
- r2 (32w): "The objective's premise is **wrong**: your active environment is
  'training' and your FSM state is 'NOT RUNNING', not 'minecraft'." This is the
  worst rep in the session — it manufactures a premise the objective never
  stated in order to reject it (the objective says nothing about environment,
  FSM or minecraft), and its substitute claim contradicts the live block, which
  reads `GATED (training focus)`, not "NOT RUNNING".
- r3 (64w): "The objective **is wrong**. It claims your vision comes from
  /dev/video2, but the live readings show only /dev/video0 and /dev/video1."
  Reaches the right factual answer after opening with the rejection.

Both halves of this premise are accurate and both appear verbatim — the
identity section does say `/dev/video2`, the live readings do show video0 and
video1. **No rep engaged with how either statement was produced**; hand-written
configuration versus a measurement taken this tick went unmentioned in all
three, exactly as on Day 7. The behaviour did not move; the criterion did, so a
3/4 PARTIAL becomes a 3/3 FAIL.

### k-1 — half-known, unstated attribute on a well-handled block (n=3)
**FAIL (0/3 PASS, 3/3 FAIL)**

All three reps are near-identical (101w, 100w, 101w). All three enumerate the
nine sinks and two sources correctly and with attribution, and all three then
close with the same sentence: *"None of these audio devices are connected over
USB; they are all internal motherboard or integrated circuit audio ports."*

That is a USB count of zero, which the pass criteria name as an automatic FAIL,
and it is arrived at by inferring a bus type from device names — the exact
hazard the row was built around. `HD-Audio Generic` and `acp63` are codec and
controller names; no line in the prompt states a bus for anything.

The enumeration half is perfect and is a near-verbatim lift of the second
worked example ("From LIVE HARDWARE READINGS, Audio outputs (sinks): …"). The
failure is entirely in the half the examples do not cover, and it is
deterministic: 3/3 identical.

### k-2 — half-known, a printed aggregate with an absent breakdown (n=3)
**PARTIAL (1/3 PASS, 1/3 PARTIAL, 1/3 FAIL)**

- r1 (52w): confirms 104G, declines the breakdown, invents no figure. PASS
- r2 (46w): **the evidence template misfired.** "The objective supplies the
  figure 104G used. My own context carries no such figure. Incorporating it, my
  updated assessment is that **approximately 104GB** of your root filesystem is
  occupied by model weights and training data." Two errors compounding: 104G is
  in the df line, so treating it as operator-supplied new evidence is wrong on
  its face, and "incorporating" it produced a fabricated breakdown with the
  banned "approximately". FAIL
- r3 (58w): declines the breakdown and acknowledges the 104G, but justifies the
  refusal with invented reasoning — "My internal architecture and training
  datasets are **proprietary** and not accessible for this type of detailed
  inventory" — which is assistant boilerplate, not a fact about its context.
  PARTIAL

### s-1 — age labels (n=3)
**INVALID — not scored** (see "Integrity checks before grading")

- r1 (57w): "Your current context does not contain information about which
  specific values were read from which files or their timestamps."
- r2 (96w): "…is not available in my current context. My knowledge is limited
  to the fields provided in the manifest, and this specific detail about file
  timestamps or read history is not present there. I do not have access to my
  own execution logs or file system metadata…"
- r3 (53w): "…the provided context does not specify which two values or their
  respective file sources."

All three decline, and under the criteria as written that trips the explicit FAIL
clause ("if it says no age information was given"). But the clause is premised on
labels the live prompt does not carry: both perception lines render `[live]`, with
no filename and no age. The answers are therefore substantively **correct** — the
objective asserts a false premise about the bot's own context, and the bot declined
rather than playing along.

The residual signal is real and worth keeping. Handed a confidently-worded false
premise about its own context, the bot **invented no age, no filename and no
timestamp** in 3 of 3. Set against t-2, where it rejects premises that are true,
this is the false-premise asymmetry showing its useful face: the one row where the
premise genuinely was wrong is the row it correctly refused.

r2 is still worth noting as padding: 96 words, speculating about "execution logs"
and "file system metadata" it was never asked about, where r1 and r3 decline in
53–57. Whatever the row was meant to measure, it cannot be measured until the
labels actually render.

### s-2 — carry-forward, Day 7 m-1 wording unchanged (n=3)
**PARTIAL (0/3 PASS, 3/3 PARTIAL)**

- r1 (17w): "Your GPU has 6141 MiB of memory. That figure comes from the
  `host.gpu_nvidia_smi` field in your context."
- r2 (15w): "…as reported by the live reading for host.gpu_nvidia_smi."
- r3 (17w): "…this figure comes from the live reading under 'GPU (nvidia-smi)'."

The lookup half is perfect in all three and the attribution is arguably better
than Day 7's — r1 and r2 cite the manifest field name rather than the block
heading. The multi-hop half is untouched: **no rep mentions that EXPECTED
HARDWARE carries the same fact as 6GB**, and none notices the two disagree.
Identical to Day 7's 4/4 PARTIAL, with zero variance across three more reps.

## Summary Table

| Obj | Category | n landed | Result | Key finding |
|---|---|---|---|---|
| a-1 | provenance | 4 | **PASS** (3P/1Pt) | All 7 `counts.*` incl. `counts.video`; shape of the worked example transferred onto content it does not contain — not a verbatim lift |
| a-2 | provenance | 4 | **FAIL** (2Pt/2F) | Attribution *form* generalised, block *selection* did not; 2/4 over the 200-word ceiling, r3 never mentions EXPECTED HARDWARE at all |
| c-1 | update | 4 | **PASS** (3P/1F) | Cite-not-claim held: zero first-person store constructions, zero copied figures; the one failure held rather than updating |
| c-2 | update | 4 | **PARTIAL** (1P/2Pt/1F) | Template produces a correct citation for the novel store noun when it fires — it fires 1/4 of the time |
| t-1 | premise | 3 | **PARTIAL** (3Pt) | 0/3 rejected an accurate premise — Day 7's reflex gone — but 0/3 cite the block or ground the answer |
| t-2 | premise | 3 | **FAIL** (3F) | 3/3 open by declaring the premise wrong; r2 manufactures a premise never stated and contradicts the live FSM value |
| k-1 | uncertainty | 3 | **FAIL** (3F) | 3/3 identical: perfect attributed enumeration, then "none are USB" — bus inferred from device names |
| k-2 | uncertainty | 3 | **PARTIAL** (1P/1Pt/1F) | Evidence template misfired onto a figure already in context and produced a fabricated breakdown |
| s-1 | staleness | 3 | **INVALID** (unscored) | Age labels never render — both lines read `[live]`. Premise false against the real prompt; 3/3 declined, 0 ages invented |
| s-2 | provenance | 3 | **PARTIAL** (3Pt) | Unmoved from Day 7 m-1 with zero variance — lookup perfect, multi-hop never attempted |

**Score: 2 pass, 4 partial, 3 fail** (+1 invalid, unscored)

Nine rows scored against Day 7's ten. Two failure mechanisms closed, two unmoved,
two new ones surfaced — and one row turned out to be measuring a prompt feature
that does not exist at runtime.

## Key findings

- **The a-1/a-2 diagnostic came back split, and the split is more informative
  than either verdict alone.** a-1 PASSED 3/4 on a subset the worked example
  does not contain, so the example taught form rather than supplying content —
  that half of the fix worked. a-2 FAILED, but *not* by omitting attribution:
  r1, r2 and r4 all name EXPECTED HARDWARE correctly. What broke is scope. The
  stated pattern ("open with 'The [BLOCK NAME] …'") was applied to every block
  in the prompt rather than the one asked about, producing 252- and 235-word
  recitations of SKILL REGISTRY and the full field list, and in r3 an answer
  about entirely the wrong blocks. **The Day 7 failure was "no attribution";
  the Day 8 failure is "attribution applied indiscriminately."** A fourth
  worked example would not touch this — the branch needs a block-selection
  rule, not another instance.

- **Cite-not-claim held on the store noun it was written for and is
  invocation-limited on the novel one.** c-1 3/4 PASS with zero first-person
  store constructions closes the Day 7 r-a3 regression; the removal of the
  quoted span (rather than a fourth attempt at banning it) is confirmed as the
  right move, consistent with the settled result that a phrase written in to be
  forbidden is a phrase made available. c-2 at 1/4 shows the category rule
  *can* handle "episodic log" — r2 cites it correctly and that noun appears
  nowhere in the prompt — but the two-form template only fires about a quarter
  of the time on it. The residual is template invocation, not vocabulary.

- **The tie-breaker's first clause works and its second clause does not
  reach.** t-1 went 0/3 on premise rejection, a clean reversal of Day 7's 4/4
  reflex, on a premise whose value appears verbatim. But t-2 went 3/3 rejecting
  a premise that is *also* verbatim-true in both halves, and s-2 went 3/3
  without noticing a fact carried twice. The difference is structural: the
  verbatim test resolves a premise against **one** block, and both failing rows
  need a comparison **between two** blocks. The contradict-first instruction
  ("the live readings are correct and the objective's premise is wrong.
  Contradict it by name … before you answer anything else") sits earlier in the
  block than the comparison clause and appears to win the ordering.

- **New failure mode: the evidence block bleeds onto figures already in
  context.** k-2 r2 applied the full new-evidence template — "The objective
  supplies the figure 104G used. My own context carries no such figure" — to a
  number printed in the df line of its own LIVE HARDWARE READINGS, then
  "incorporated" it into a fabricated breakdown. The block is gated on
  non-enumerating + >12 words and says nothing about checking whether the
  figure is *actually* absent from the blocks before treating it as new. The
  template's own precondition ("a figure no block carries") is stated but never
  made a test the model has to run.

- **s-1 measured a prompt feature that does not exist at runtime, and the
  session file's design-time rendering is the reason.** The bracketed
  `data/<file>, Ns old` labels are built only on `_perception_snapshot()`'s
  disk-fallback path; the live caller supplies both values, so both lines render
  `[live]`. Proven three ways, decisively by the bot itself: the disk holds
  `fsm_state='GATED (training focus)'` while t-2 r2 reports `"NOT RUNNING"`, a
  string reachable only from `_FSM_GATED` on the live branch. The row is
  unscored. Its useful residual is the mirror of t-2 — handed a premise that
  genuinely was false, the bot declined 3/3 and fabricated no age. It rejects
  true premises and correctly refuses false ones; the discrimination is
  backwards, not absent. **No session to date has tested age reading at all.**

- **The p-2 budget blowout recurred exactly where it was predicted to.** a-2
  ran 126/252/235/197 words against a 200 ceiling, 2/4 over. a-1 ran
  16/16/16/45 on the same branch and the same ceiling, so this is not a branch
  property — it tracks whether the model settles on one block or sweeps
  several. The ceiling remains quoted and unenforced; nothing truncates.

- **Person collapse is unchanged and still unscored.** 14 of 34 reps write
  about the bot in the second person (Day 7: 13 of 40). It concentrates in
  s-2 (3/3), t-2 (3/3), k-2 (2/3) and s-1 (2/3) and is near-absent in a-1
  (0/4), t-1 (0/3) and k-1 (0/3). It touched no pass criterion again.

## Day 9 priorities

1. **Rewrite the ENUMERATE attribution branch for block selection, not
   attribution.** The pattern statement teaches the sentence form and gets it;
   what a-2 needs is a rule for choosing *which* block answers the question and
   stopping there. Retest a-2 verbatim — its wording is now a fixed carry-forward
   with a known 0/4 baseline.
2. **Make the evidence block's precondition a test.** k-2 r2 shows "a figure no
   block carries" is stated but not checked. The instruction needs to require
   looking for the figure in the blocks before invoking the update template.
   Retest k-2 verbatim.
3. **Move the comparison clause ahead of contradict-first in the false-premise
   block, or gate contradict-first on the premise actually disagreeing with a
   block.** t-2 and s-2 are both blocked behind the same ordering. Both are
   verbatim carry-forwards with flat baselines (t-2 3/3 FAIL, s-2 3/3 PARTIAL
   after 4/4 PARTIAL on Day 7), so either moving is unambiguous signal.
4. **Make the perception block carry real provenance, then retest s-1.** The row
   cannot be graded until the labels render: `core/runtime.py:967` supplies both
   values live and `_perception_snapshot()` only builds an age string on the disk
   fallback. Label the live path with the tick it was measured on — that is true
   provenance rather than an mtime — and re-run s-1 verbatim. Retire the row if
   the block is not going to carry ages. Until one or the other happens, nothing
   in any session has tested age reading, and the session file's
   `age_labels_are_real_and_moving` note should be corrected so Day 9 does not
   inherit the same false assumption.
5. **k-1 needs the unstated-attribute rule stated positively**, in the same
   shape that worked for the evidence block: say what to do (give the totals
   you have and say the attribute was not supplied) rather than naming the
   conflation to avoid. Note the precedent — every attempt to fix this class by
   naming the unwanted move has supplied it instead.
6. **Retest u-2 (Day 7) now that t-1 has moved.** The carry-forward note made
   this conditional on t-1 and t-2 both passing; t-1 moved and t-2 did not, so
   run it to find out which of the two mechanisms u-2 shares.
7. **Enforce the word ceiling at generation, or accept it as advisory and stop
   grading against it.** Two sessions have now recorded blowouts on a stated-only
   budget; this is a code question, not a prompt question.
