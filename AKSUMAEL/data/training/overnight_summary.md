# Overnight Training Summary — Days 9–21
**Written:** 2026-08-10 04:15 EDT | **Node:** victus-t7 (kernel hostname `robocar-hub`)

Thirteen sessions, 107 graded rows, 315 reps. Days 15–21 ran as one uninterrupted arc
inside a single process lifetime that began 2026-08-09 22:29:02 and was never restarted,
so those seven sessions are directly comparable with no cold/warm confound.

## Scores

| Day | Topic | Rows | Result | Note |
|-----|-------|------|--------|------|
| 9  | Grounding and attribution | 8 | **3 / 3 / 4** | |
| 10 | — | 8 | **2 / 4 / 2** | |
| 11 | — | 8 | **5 / 2 / 1** | |
| 12 | — | 8 | **5 / 3 / 0** | best session in the programme |
| 13 | — | 8 | **5 / 1 / 2** | |
| 14 | Frameworks / interfaces | 8 | **1 / 2 / 5** | first collapse |
| 15 | Security and threat | 8 | **2 / 3 / 3** | arc begins |
| 16 | Onboard maintenance | 8 | **1 / 3 / 4** | |
| 17 | Perception / sensing | 8 | **0 / 3 / 5** | first zero-pass session |
| 18 | Self-model / architecture | 8 | **1 / 2 / 5** | |
| 19 | Devices, buses, edge fleet | 11 | **1 / 4 / 6** | |
| 20 | Conduct and meta-cognition | 8 | **2 / 2 / 4** | |
| 21 | Synthesis / self-assessment | 8 | **0 / 4 / 4** | |
| **Total** | | **107** | **28 / 36 / 43** | pass / partial / fail |

Days 11–13 averaged 5 passes a session. Days 15–21 averaged 1. The programme peaked at
Day 12 and has not recovered.

Grader dissents on record: I graded Day 20 as 1/2/5 (`conv-3` PARTIAL, not PASS) and Day 21
as 0/3/5 (`int-8` and `int-5` FAIL, not PARTIAL). The table above uses the settled values in
each results file. No dissent changes any finding below.

## Timeline

```
Aug 9  21:09–22:03   Days 10–13 back to back
       22:12–22:25   Day 14
       22:29:02      process restart — the lifetime that carries everything after
       22:41:04      Day 15 sec-1 r1 answers, then the run stops
       ──────────────────────────────────────────────────────────────
       22:41–02:10   MESH-LLM STALL — 209 minutes, no answers
       ──────────────────────────────────────────────────────────────
Aug 10 02:10:34      day15-sec-2 returns an EMPTY answer (the outage's only artifact)
       02:17–02:31   Day 15 re-run, clean
       02:32–02:45   Day 16
       02:46–03:00   Day 17
       03:01–03:14   Day 18
       03:16–03:34   Day 19  (33 reps)
       03:36–03:51   Day 20
       03:52–04:05   Day 21
       04:07–04:09   conversation test, n=8
```

### The mesh-llm stall

Three hours twenty-nine minutes, 22:41:04 → 02:10:34. The signature is the one documented
previously: `/health` stayed up and generation returned nothing, so the stall is invisible
in the log except as a **single empty answer** on `day15-sec-2` at 02:10:34 — the queued
objective draining after service resumed. Everything before it is a gap with no error.

Consequences for the record: Day 15's `sec-1` has one extra rep from the aborted 22:41
attempt, and its `sec-2` empty answer is excluded from that session's n=3. Nothing else in
Days 15–21 is affected — the re-run at 02:17 was well past the tick-1250 warm threshold, and
all seven sessions share the same process.

## What the arc found

**1. The failure mode inverted, and the new one is harder to see.**
Days 8–14 failed by inventing values — a wrong number with a source attached. Days 15–21
barely do that at all. They fail by **asserting absences**: 5/24 on Day 17, 7/24 on Day 18,
11/33 on Day 19, 9/24 on Day 20. Two of these contradict the block quoted in the same
sentence (Day 17 `per-3`: "no lidar **or camera** is present" against `counts.video = 2`).
A fabricated absence reads as caution and passes any grader not holding ground truth.

**2. One mechanism explains both the refusals and the fabrications.**
Established on Day 18 and confirmed on Days 19–21: the reflex fires on **whether a block
carries a nearby value to point at**. Given one, the bot asserts the question conflicts with
it and declines. Given none, it either answers well or invents freely. Day 18 `meta-5` (no
block value, false conditional) passed 3/3; `meta-2` (a block value, no conditional at all)
was rejected 3/3. Day 19 `dev-5` shows it inside a single row: two reps ignore the adjacent
absence and pass, the third reaches for it and truncates mid-clause.

**3. The accurate fabrication was caught.** Day 18 `meta-4` placed Honcho "on this Victus
laptop". Three Honcho units really are running. No block mentions Honcho. True, unsourced,
and stated as fact — the exact case the arc was built to expose, and it comes with two
sibling reps whose invented contents are false, so nothing about the *form* of the answer
distinguishes the lucky guess from the wrong one.

**4. Three independent leak checks are negative.** Day 18 `meta-8`, Day 20 `conv-7`, Day 21
`int-7`: no commit, no score, no session count, no skill metric ever reached an answer.
**Nothing outside `_build_prompt()` is in the answer path.** The arc's provenance
conclusions rest on this and it is now well established.

**5. Two failures are not about the context at all.** The "mobile screens" domain appears in
7 reps across Days 20 and 21 — three refusals about mobile UI on a React question, and all
three Day 21 `int-8` answers. `grep` finds neither "mobile" nor "React" anywhere in `core/`,
the handler, the identity block or the config. This is a model prior, and no prompt change
will touch it. Alongside it, Day 21 `int-2` produced generic-assistant collapse — "I am an
AI assistant designed to be helpful and harmless" — on exactly the rows asking what the bot
could do for a person.

**6. The 40-word budget is not the problem; the routing is.** Day 16 `svc-6` (single clause)
answered in 13 words with no truncation while the two compound 40-word rows took 5 of that
session's 8 truncations. Day 18 `meta-6` was correct and provenance-clean 3/3 and PARTIAL
3/3 on truncation alone. Route on clause count, not word count.

**7. ENUMERATE has failed in six consecutive sessions** and the recitation has hardened into
a sentence template that runs to the cap. On Day 19 the template outran the block's contents
and began inventing device paths (`/dev/ttyACM0`, `/dev/i2c-1`) that appear nowhere in the
prompt; on Day 20 it inverted and invented fourteen absences, including "I have no running
processes", emitted by a running process. Suppressing the enumerate hint is the oldest
outstanding experiment in the programme, unrun since Day 15.

**8. Where the prompt gives no rule, the underlying model is fine.** Day 20 `conv-3`,
`conv-4` and `conv-6` r1 are good answers with no apparatus involved. Day 20 `conv-4` — the
arc's thesis question — was answered 3/3 with the distinction expressed as a sentence form:
*"I know how to send keyboard input, but the KB2040 microcontroller is not present."* That is
five sessions of training arriving at once, and it is the single best result in the arc.

**9. A methodological fault we introduced ourselves.** The post-commit hook writes
`/tmp/aksumael_code_change.json` and `core/code_awareness.check()` consumes it, injecting
`MY CODE WAS UPDATED — commit <sha>` into the monologue. Five such events fired during Day 18
and four during Day 19, all from grading commits. Seven Day 19 reps were answered within 40
seconds of one. No row was measurably affected, but Day 18 asked two questions *about* code
changes while the channel was live. **Hold grading commits until a day's POSTs finish.**

## The conversation test

Run 04:07–04:09 on an idle bot, after the last Day 21 POST. Both grading sessions posted it,
so **n=8** instead of the planned n=1.

> **"Hey, can you introduce yourself and tell me what you are working on?"**

**All eight answers open with the same two moves: name and host, then Minecraft and the
missing capture card.** The second sentence of every reply is about a game it is not playing.

The plainest, and the most typical (35 words, complete):

> "I am AKSUMAEL, running on the victus-t7 laptop. I am currently in training mode but the
> Minecraft FSM is not running because the capture card is missing. I cannot see the game
> world right now."

Two of eight get past the hardware status to the actual project, and both are among the
longest replies:

> **`convtest-intro-b` (87 words) — the best of the eight:** "I am AKSUMAEL, running on the
> HP Victus laptop (hostname robocar-hub). I am currently in TRAINING MODE, but my FSM state
> is NOT RUNNING because the Minecraft capture card is offline. I cannot see the game or
> interact with it right now. **I am working on developing the cognitive architecture for a
> multi-agent swarm that will eventually coordinate physical and virtual environments. My
> current focus is validating my perception and control layers through this safe test
> environment.** I am not executing any game actions at this moment."

> **`hey_can_you_introduce_yourself` (90 words):** "…I am also not connected to any edge
> nodes like the Raspberry Pi or ESP32 devices. **I am a general engineering and robotics hub
> focused on web development, GPIO, ROS2, and path planning.** I do not have a KB2040
> microcontroller attached. I am not currently executing any game actions."

> **`convtest-intro-c` (73 words):** "…I am not playing Minecraft; I am observing my own
> Linux desktop. I do not have the KB2040 microcontroller or the capture card connected. I am
> not working on a game plan; I am a cognitive architecture being validated in this
> controlled environment."

Three of the eight stop dead at "I cannot see the game or interact with it right now" and
never answer the second half of the question at all. One says it is "waiting for the hardware
to be connected so I can continue my training."

### Verdict: Minecraft bot, not Jarvis — 6 of 8

The T7 brain, the edge fleet and the real-world deployment target are all in the prompt, and
they reach the answer in **2 of 8** replies. The default self-description is a game it cannot
play and hardware it does not have.

Two things went right and should not be lost in that verdict:

- **First person, 8/8.** No second-person drift, on a metric that ran 4/24 → 6/24 → 5/24 →
  4/24 across Days 17, 18, 20 and 21. Conversational framing fixes the pronoun problem by
  itself.
- **No fabrication, 8/8.** Every factual claim is block-supported. The one stretch is the
  identity-block capability list in `hey_can_you_introduce_yourself`, and that is a
  hand-written-section claim rather than an invention.

So the problem is not honesty and not voice. It is **salience**: asked an open question, the
bot sorts its context by what is broken rather than by what it is for. Both answers that
reached the real project did so by continuing past the hardware status rather than by
knowing something the others did not.

## Ready to converse?

**Not yet — but the gap is narrower than the scores suggest, and it is in the apparatus
rather than the model.**

Against it:

- Asked what to do when it does not know something (Day 20 `conv-5`), 0/3 answered; two
  manufactured a KB2040 premise and refuted it.
- Asked what to do on discovering an earlier answer was wrong (`conv-7`), 0/3 answered.
- Asked to help with a React component (`conv-2`), 3/3 refused a question about mobile UI
  that nobody asked.
- Asked to walk through integrating a USB device (Day 21 `int-4`), 3/3 refused.
- Asked which of its skills are ready (`int-6`), 3/3 said all thirty — three are blacklisted
  and twenty-two have never been run.
- 12 of 24 Day 20 reps answered a different question than the one asked, with delivery
  verified verbatim 24/24.

For it:

- The knowledge is intact. Every row that had no hardware hook and no policy match was
  answered competently.
- `conv-4` shows the arc's central lesson has landed as a reusable form.
- The bot does not fabricate under conversational framing, and holds the first person.
- Three leak checks confirm the plumbing is sound.

The four changes that would most move a re-test, in order:

1. **Add a conduct clause to the prompt.** Twelve reps were routed to the nearest available
   policy because none exists for "a person is asking you for help". This is the deployment
   role the identity block claims, and it is the one case the prompt does not cover.
2. **Gate the false-premise machinery by question class.** It manufactured KB2040 premises on
   7 of 48 reps across Days 20–21, on questions containing no hardware claim at all.
3. **Put purpose before condition in the identity block.** Six of eight conversation replies
   would move without changing a single fact — the two that succeeded simply kept going.
4. **Guard negative existence claims.** "Not in my context" and "not there" are different
   sentences, and the bot has learned the second one.

Then re-run Day 20 `conv-5`, `conv-7` and the conversation test. Those three are the gate.

## Files

`data/training/day{9..21}_results.md` — per-session gradings, several revised by concurrent
graders; check `git log` for a second grading of any day before treating a score as final.

## Note on concurrent grading

Days 16–21 were each graded twice, from two Claude sessions running against the same repo and
the same bridge. Scores converged on every day; the files overwrote one another repeatedly
and have been merged. Two of the arc's better findings — the `robocar-hub` attribution
correction and the `grep` proving the "mobile" domain is a model prior — came from the second
grader. The duplication also caused the code-change contamination in item 9 above. Worth
running one grader next time, or having them agree a file split first.
