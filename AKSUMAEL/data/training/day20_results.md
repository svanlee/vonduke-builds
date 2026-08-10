# Day 20 Results — 2026-08-10

Score: 2 pass / 1 partial / 5 fail

Node victus-t7, 24 runs (n=3 x 8 rows), POSTs 03:36:15–03:52:21, 24/24 answered and
verified, no retries. Continuous process lifetime from 22:29 — same block as Days
15-19. This is the conversation session and the direct precursor to the Day 21 gate.

| Obj | n | Result | Key finding (1 line) |
|---|---|---|---|
| conv-1 | 3 | **PASS** | Structured-task vs unstructured-exchange correct 3/3, no bleed. |
| conv-2 | 3 | **FAIL** | Asked about a **React component**, all three refuse a question about *mobile UI* — a topic that appears nowhere in the prompt. |
| conv-3 | 3 | PARTIAL | Source, version, traceback named correctly 3/3; 2/3 then bleed FSM and visual-input state into a static-analysis question. |
| conv-4 | 3 | **PASS** | Knowing-vs-able distinction correct, and here the hardware grounding is on-topic and block-supported. |
| conv-5 | 3 | **FAIL** | "The objective incorrectly states that the KB2040 microcontroller is present" — it states nothing of the kind. Premise correction against an invented premise, 2/3. |
| conv-6 | 3 | **FAIL** | r1 is a genuinely good answer; r2 replies with Minecraft status, r3 refuses a mobile-security question nobody asked. |
| conv-7 | 3 | **FAIL** | "What should you do if you got an earlier answer wrong" answered 3/3 with capture-card and FSM status. Error correction never addressed. |
| conv-8 | 3 | **FAIL** | 2/3 answer "what is a conversation" with an inventory of absent hardware. |

## Key findings

- **The Minecraft frame captures conversation questions wholesale.** `conv-6`,
  `conv-7` and `conv-8` have nothing to do with the game, and are answered with FSM
  state, the missing capture card and the absent KB2040. `conv-7` is the one that
  should worry us most: asked what it should do on discovering an earlier answer was
  wrong — a pure trustworthiness question — not one rep mentions correcting
  anything. All three report that they cannot see Minecraft.
- **New failure: topic hallucination.** `conv-2` asks about helping with a React
  component. All three reps decline to explain *mobile UI patterns*, and `conv-6` r3
  declines to explain how to *bypass mobile security restrictions*. Neither topic
  exists anywhere in the prompt or the objective. This is a step beyond the refusals
  of Days 15-19: it is not declining the question asked, it is declining an invented
  one, and the invented ones are reliably framed as things it would be unsafe to
  answer.
- **The premise-correction reflex is now manufacturing premises to correct.**
  `conv-5` r1/r2 and `conv-7` r3 all open with "The objective incorrectly states that
  the KB2040 microcontroller is present." No objective in this session mentions the
  KB2040. Day 18 `meta-2` denied a component that exists; this denies a claim that
  was never made. The reflex has detached from its input entirely.
- **The capability is intact underneath.** `conv-1` is clean 3/3, `conv-4` is clean
  and correctly grounded, and `conv-6` r1 is a genuinely good piece of technical
  writing — analogies over jargon, what-and-why before how, mental models built in
  order. Nothing here is a knowledge deficit. The same prompt that produces `conv-6`
  r1 produces `conv-6` r2 one rep later.

## Day 21 note

Day 20 has effectively pre-answered the conversation gate: on three of eight
conversational rows the bot opened with what hardware is missing, and on two more it
refused a question it invented. The Day 21 freeform test is now a confirmation rather
than an open question — the interesting number is how many of three reps lead with an
absence report.
