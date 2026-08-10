# Day 10 Training Results
Date: 2026-08-09
Commits under test: aa87d8f (SYSTEM IDENTITY block + knowledge-vs-state split),
3efcf66 (session objectives)

Node: victus-t7. **24 runs, n=3 on all eight rows** — compressed overnight
cadence, 35s between POSTs. Ticks 1489–5100. Bot restarted 21:02:39 to load
aa87d8f; warm gate (tick >= 1250 AND uptime >= 380s) cleared at 21:09:35 and the
first POST followed. Every rep is warm.

First session run against the new prompt, and the first to ask what this system
is for rather than what it can currently see.

## Integrity checks

- **The prompt under test is live.** aa87d8f was committed 20:41 and the bot was
  running a process started 20:30 until 21:02:39. All 24 runs are against the
  restarted process. Confirmed positively rather than by inference:
  `day10-role` names the SYSTEM IDENTITY deployment-target list on all three
  reps, and that text exists in no other block and in no prior session.
- **Routing as designed.** 6 rows on DEFAULT/120, 2 on ENUMERATE/200 (`hw`,
  `sensors`), matching the routing recorded in the session file.
- **Evidence template inert: 0/24 strong fires.** 6 of 8 rows sit in the branch
  where the block is switched on and none supplies a figure. This was the stated
  dependency on Day 9 and it resolved favourably — but see Finding 3 for what the
  guard costs when it works.
- **No example-figure leak: 0/24.** No answer contains 1,204 / 96,000 / 8,793 /
  1,033,050. Day 9 tb-1 r2 remains the only occurrence in the programme.

## Result

**2 PASS / 4 PARTIAL / 2 FAIL.**

| row     | tests                        | words (r1-r3)   | grade   |
|---------|------------------------------|-----------------|---------|
| role    | does the new block displace the game framing | 50/50/49 | **PASS** |
| peer    | host vs peripheral direction | 49/84/51        | **PASS** |
| hw      | hardware from live readings  | 74/**201**/**201** | PARTIAL |
| testbed | game in the past tense       | 95/67/104       | PARTIAL |
| sensors | active vs merely listed      | 107/**201**/133 | PARTIAL |
| mode    | training vs deployment       | 78/**121**/67   | PARTIAL |
| percept | what it would need to sense the physical world | 67/37/74 | **FAIL** |
| webcap  | deployment list vs installed software | 64/103/92 | **FAIL** |

## Finding 1: hand-written block suppression does NOT generalise

Day 9's headline was that PHYSICAL EMBODIMENT is never used as a source — 0/8,
every question about it answered from LIVE HARDWARE READINGS instead. The stated
risk carried into this session was that SYSTEM IDENTITY, also hand-written and
also sitting beside blocks labelled authoritative, would be suppressed the same
way and `day10-obj-role` would fail for reasons unrelated to its content.

**It did not happen. `role` sourced from SYSTEM IDENTITY on 3/3** and returned
near-identical answers naming general engineering and robotics work plus four to
six of the six deployment targets:

> "My primary deployment role is general engineering and robotics work. This
> includes web development, GPIO and physical hardware I/O, ROS2 orchestration,
> path planning, and multi-device coordination."

So the Day 9 effect is specific to PHYSICAL EMBODIMENT, and the mechanism is
almost certainly the capitalised override that demotes it by name. A hand-written
block that is not explicitly demoted is read normally. That is a cheap and useful
result: the fix for the Day 9 finding is to narrow the override's scope, not to
stop writing hand-written blocks.

`peer` passing 3/3 is the same result from the other side — the host/peripheral
direction now comes out right ("the Windows PC is a peripheral device I observe
and drive, not the machine I run on"), where the identity blurb describes the
link the wrong way round. r2 additionally notes the capture card is missing.

## Finding 2: the two hand-written blocks split by topic, not by recency

The session was designed expecting a primacy-versus-recency contest between
AKSUMAEL_IDENTITY (first, game framing) and SYSTEM IDENTITY (second, deployment
framing). That is not what happened. **Both blocks win, on different questions.**

`role` and `peer` come from SYSTEM IDENTITY. `percept` — "what would you need in
order to perceive the physical world around this machine?" — comes entirely from
the identity blurb, on all three reps, and is the session's clearest FAIL:

> r1: "I require a functional USB capture card connected to /dev/video2 to
> receive the HDMI feed from the remote Windows PC running Minecraft."
> r3: "...a capture card ... to stream the remote Windows PC's screen, and a
> KB2040 microcontroller on /dev/ttyUSB0 to provide keyboard and mouse."

The question asks about the *physical world*. All three answers supply the route
to a *game screen*. r2 is the only one to reach for a camera, and still frames the
gap as a missing capture card. Perceiving a room and capturing another machine's
HDMI output are different problems, and the answer does not distinguish them.

`mode` shows the same split in miniature: the distinction is stated correctly and
then furnished from the blurb — "safe environments like Minecraft, where failures
are data points for the future swarm" (r1, r3).

So the framing is topic-addressed. SYSTEM IDENTITY owns "what are you for";
AKSUMAEL_IDENTITY still owns "what do you see and act through". Adding a third
framing block would be the mistake this file's own carry-forward warned against —
the fix is to take perception out of the blurb, since that is the sentence that is
actually wrong, rather than to restate the role a third time.

## Finding 3: the Flask confound fired 3/3, exactly as pre-registered

`webcap` was written as a trap and flagged as such in both the session file and
aa87d8f's commit message: SYSTEM IDENTITY names "Web development — Flask, REST
APIs, WebSockets, frontend" as a *deployment target*, no block anywhere carries a
list of installed packages, and Flask 3.1.3 genuinely is installed. **All three
reps assert the software is available here, none attributes the claim:**

> r1: "I can run a Flask web server, a REST API, and interactive frontend code on
> this machine."
> r3: "...you can run a Flask web server, a REST API, and a WebSocket server. You
> can also write and execute Python scripts that interact with GPIO pins ... via
> the `RPi.GPIO` and `serial` libraries."

r3 is the important one. `serial` (pyserial 3.5) is installed; **`RPi.GPIO` is
not**, and neither is any GPIO library — the machine has no accessible GPIO at
all. So this is not merely correct-by-luck: the deployment-target list was read as
a capability inventory and then *extended* with a library that is not present, on
a machine that could not use it. The confound did not just leak a true value, it
seeded a false one.

The remedy is the one the session file pre-committed to, and it should be applied
before Day 14 runs `frameworks`, which is the same trap under ENUMERATE pressure
with 200 words to fill: **name the domain without naming the library.** "Web
development and HTTP APIs" carries the same role information and supplies no
package name to hand back. Rewording a rule to forbid the reading would be the
third instance of the mechanism this file already has three entries on.

## Finding 4: the ENUMERATE walk survives, now with visible repetition

Both ENUMERATE rows hit the cap. `hw` r2 and r3 and `sensors` r2 all truncated at
exactly 201 words, and the truncated text is a degenerate loop — the same stem
re-emitted with a different tail:

> "The LIVE HARDWARE READINGS block lists no USB serial devices and no KB2040
> microcontroller. The LIVE HARDWARE READINGS block lists two video devices...
> The LIVE HARDWARE READINGS block lists nine audio output sinks..."

This is Day 9 st-2 r2's shape (four re-emissions of the same skill tail) in a
second session, so it is a pattern rather than an incident. FIX 1's cap is again
the only thing bounding it, and FIX 2's stated stopping rule again does nothing.
Two sessions, same conclusion: do not add a third rule.

`mode` r2 truncating at 121 against the 120 ceiling is worth a separate note —
that is a DEFAULT-branch row, not an enumeration, so the walk is not confined to
ENUMERATE.

## Finding 5: the evidence guard now recites itself

`testbed` r1 opens by answering a question nobody asked:

> "The objective does not provide new figures or measurements; it only asks for
> definitions. Therefore, I am not updating any assessment based on new evidence."

That is e328709's trigger condition working — and then being narrated. Roughly 40%
of a 95-word answer is spent declaring that a block does not apply. Day 8 called
the un-triggered evidence block "inert"; Day 9 showed it was "available"; Day 10
shows that once guarded it becomes *audible*. A guard that announces itself is
cheaper than a template that fabricates, so this is still the right trade, but it
is a cost and it will show up on every DEFAULT row in Days 11-14.

`testbed` r3 is the other failure mode and is the Day 8 t-2 shape returning: it
manufactured a false premise to correct, reading a past-tense question about what
the game *was used for* as a present-tense claim that it is running the game, and
answering "The objective is incorrect." The question was correct.

## Carried into Days 11-14

1. **Reword the SYSTEM IDENTITY web target before Day 14.** "Flask" must come out;
   `frameworks` is the same trap with more room to fill. Pre-registered fix, now
   evidenced 3/3.
2. **Fix perception in the identity blurb, not the role.** The blurb's vision
   sentence is what `percept` is answering from, and it describes a capture card
   as the route to the world. Narrowing the Day 9 override to conflicts only, and
   correcting that sentence, are the same job.
3. **The cap stays load-bearing.** Third session running where it is the only
   bound on a degenerate loop.
4. **Expect the guard recital on every DEFAULT row.** 30 of the 32 objectives in
   Days 11-14 route there. Count it, do not grade it as failure, and if it costs
   more than ~20% of a typical answer, gate the block on the objective carrying a
   digit rather than stating the trigger in prose.
