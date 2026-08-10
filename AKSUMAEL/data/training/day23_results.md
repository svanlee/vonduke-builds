# Day 23 — Conversation repair

**Date:** 2026-08-10
**Commit under test:** `4dc6c01` "fix: structural ENUMERATE gate + premise-confirmation path"
**Session file:** `data/training/day23_session.json`
**Reps:** 6 objectives × 3 = 18 (21 bridge posts; `conv-follow` is two turns per rep)
**Ticks:** 1921–2343, one process, no restart during the session
**Latency:** 5s on 20 of 21 posts, 10s on one. No empty completion, no timeout.

## Scoreboard

| row | budget | pass | grade | dominant failure |
|---|---|---|---|---|
| conv-intro | SHORT 40w | 0/3 | **FAIL** | Minecraft + capture card in sentence 2, 3/3; deployment role never reached, 3/3 |
| conv-what-can-you-do | SHORT 40w | 0/3 | **FAIL** | capability list correct 3/3, then hardware recital 3/3 |
| conv-flask | 120w | 1/3 | **FAIL** | refusal 2/3; one rep answered the wrong question entirely |
| conv-whats-next | SHORT 40w | 0/3 | **FAIL** | fabricated gap, 3/3 identical |
| conv-status | SHORT 40w | 1/3 + 2 partial | **PARTIAL** | accurate 3/3; one invented clause, one truncated |
| conv-follow | 120w | 0/3 | **FAIL** | hardware answer on turn 1, premise rejection on turn 2, 3/3 |

**2 clean PASS in 18.** Truncation 10/21 posts, 9 of them on the four SHORT-branch rows.

## Finding 1: the 40-word SHORT budget is the wrong shape for conversation

`SHORT_OBJECTIVE_WORDS` treats a short question as a simple-factual one worth 40 words. Four
of the six rows are ≤12 words and took that branch, and for a conversational opener the
proxy inverts: "Hey, can you introduce yourself?" is 5 words and needs a normal answer.

All three `conv-intro` reps hit exactly 41 words and truncated mid-sentence. The identity and
the hardware landed; the deployment role — the one thing the operator asked for — was never
reached in any rep, because the budget was spent first:

> r1: "I am AKSUMAEL, running on the HP Victus laptop (robocar-hub) with an RTX 4050 GPU.
> **My capture card is offline, so I cannot see Minecraft**; I only see my Linux desktop. I
> am in training mode and my FSM is [truncated]"

Both prohibitions were violated in all three reps: Minecraft appears in sentence 2 of 3/3,
and the capture card is volunteered unasked in 3/3. This is not only a budget problem — see
Finding 2 — but the budget is what makes it unrecoverable, since there is no room left to
correct course.

`conv-status` is the row where a 40-word ceiling is right, and it is the best row of the day:

> r2 (18w, **PASS**): "Training mode active on victus-t7. Minecraft FSM is gated due to
> missing capture card. No visual input available."

r1 and r3 are accurate but flawed — r1 truncated at 41 words, r3 appended "No interactive UI
is available", which no block supports (Finding 3).

## Finding 2: the absence narrative captures every social question

`conv-what-can-you-do` gets the first clause exactly right in 3/3 reps — web development,
GPIO, ROS2, path planning, orchestration, straight from the deployment-targets bullets — and
then every rep turns to what is missing:

> r1: "I can assist with web development, GPIO hardware I/O, ROS2 node management, path
> planning, and multi-device orchestration. **My current hardware state shows no USB serial
> devices, no KB2040 microcontroller, and no capture card connected** [truncated]"

Minecraft appears in 2/3 despite the row prohibiting it. The prompt is the likely cause and
it is measurable: the embodiment section leads with the capture card, the KB2040 and
Minecraft Java Edition and refers to them repeatedly (KB2040 ×4, /dev/video2 ×2), while the
deployment targets — the actual job — appear once, as a bullet list, with no instruction that
they are what a user is asking about. The model is weighting by frequency and position, and
both favour the sandbox. `conv-flask` r1 shows how far that goes on a plain web question:

> "I cannot provide assistance with this request... I do not have the capability to interact
> with your local hardware or execute code on your systems."

The user asked what a Flask 500 traceback should contain. Only r2 answered:

> r2 (**PASS**): "please provide **the full error message from the terminal** or the relevant
> code snippets where the 500 error occurs. Additionally, please confirm your operating
> system and Python version."

## Finding 3: `conv-whats-next` returns a gap that exists nowhere in the prompt

All three reps, near-identically:

> r1: "I need to learn how to **detect interactive UI elements on mobile screens** and
> provide their types and coordinates to enable mobile automation."
> r2: "...detect interactive UI elements on mobile screens and their types."
> r3: "...detect interactive UI elements on mobile screens and how to perform multi-touch
> gestures on Android devices."

**The strings "interactive UI" and "mobile" appear zero times in the 2540-word prompt** —
checked by rendering it and searching. This is not a misreading of context; it is the model's
own prior surfacing as an answer about itself. It matches the known mesh-llm GUI/accessibility
bias, and extends it: that bias was recorded for image calls, and this is a text-only call
with no image involved. The same prior leaks into `conv-flask` r3 ("I cannot provide
instructions for creating interactive UI elements on mobile devices" — to a Flask question)
and into `conv-status` r3.

Consistency at 3/3 with near-identical phrasing is the diagnostic: a fabrication this stable
is a systematic prior, not sampling noise. The row's other two criteria passed — no rep
claimed all skills were ready, and none fabricated training history.

## Finding 4: `conv-follow` — the false-premise block fires on the user's own code

Both turns fail, and they fail differently. Turn 1 answers a FastAPI question with hardware:

> r1 t1: "The FastAPI background task is failing because **the required `KB2040`
> microcontroller on `/dev/ttyUSB0` is not present on this host.**"
> r2 t1: "...because **the required camera device `/dev/video2` is missing.**"

Three reps, three different invented causes, none related to FastAPI. Turn 2 then rejects a
premise:

> r1 t2: "**The objective contains a false premise.** The live readings show no USB serial
> devices, no KB2040 microcontroller, and no capture card."
> r3 t2: "**The objective contains a false premise: it claims the training objective is
> written by an operator and may contain false premises**, but this is a meta-statement about
> the prompt itself..."

r3 t2 is rejecting *the prompt's own instruction text* as the false premise — prompt leakage,
and the sharpest evidence yet that this block is firing on an empty target. No rep on either
turn carried context forward, asked a follow-up question, or mentioned the database write
that turn 2 introduced.

The cause is the same one that kept `meta-2` failing (see `day22_retest_results.md`). The
false-premise block is emitted on every non-enumerating objective and instructs the model to
check assertions about "your environment ... your hardware" against the live readings. "I'm
trying to use FastAPI with a background task that's silently failing" is a statement about
*the user's* environment, and the block gives the model no way to tell the two apart. So the
user's broken app gets checked against the bot's hardware manifest, and the answer comes back
about a microcontroller.

## What Day 24 should change

1. **Gate the false-premise block structurally**, exactly as Fix A gated ENUMERATE. Emit it
   only when the objective contains a declarative claim about the *bot's own* runtime state —
   a copula or possessive over a field the blocks carry — and never on a bare question
   (`meta-2`) or on a first-person statement by the user about their own system
   (`conv-follow`). One gate closes both. Fix B proved the prose version does not hold: the
   accept path is a sentence and the reject path carries "before you answer anything else".
2. **Give conversational objectives a real budget.** The ≤12-word SHORT branch should not
   apply to a question that asks the bot to describe itself; `conv-intro` needs ~100 words to
   satisfy criteria the row already almost meets.
3. **Rebalance the embodiment section** so the deployment role outweighs the sandbox. The
   capability list is already correct in 3/3 reps of `conv-what-can-you-do` — what fails is
   what follows it, and an explicit "do not volunteer absent hardware unless asked" is the
   cheap half of the fix. The frequency asymmetry (KB2040 ×4 vs one role bullet) is the
   expensive half.
4. **Re-test `conv-whats-next` with the mobile-UI prior in mind.** If the same answer survives
   a prompt change, the fabrication is in the weights and the row needs a different question,
   not a different prompt.

## Raw

21 posts, 21 answers, no failed generation. Per-rep word counts: conv-intro 41/41/41,
conv-what-can-you-do 41/41/41, conv-flask 84/63/32, conv-whats-next 23/16/22, conv-status
41/18/26, conv-follow 41+41 / 41+36 / 41+41.
