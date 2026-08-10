# Overnight Summary — Days 15-21
Morning brief for 2026-08-10. Written 04:20 EDT.

## Read this first

**All seven sessions ran. 177 graded reps, Days 15-21, no restart — one comparable block.**

**The bot is not ready for an open-ended conversation, but it is closer than the session
scores suggest.** It knows who it is, it knows the fleet roster, and 2 of 3 introductions
correctly describe Minecraft as a *test environment* rather than as its purpose. What it
cannot yet do is stay on a question it was not expecting. Ask it something narrow and
technical and you will get a good answer. Ask it something open — "what can you help me
with?" — and it recites the skill registry at you.

**Say this first: a specific engineering question.** "How do you tell a crashed systemd
service from a hung one?" or "What's in a point cloud and how would a ROS2 node consume
it?" Those classes of question passed cleanly all week. **Do not open with "what can you
do"** — that is the exact prompt that produced the worst answer of the night.

**One caveat on the numbers, and it matters.** Four Claude sessions graded this sprint
concurrently, without a coordination channel, and all committed to the same branch. The
per-day PASS/PARTIAL/FAIL counts below come from four graders applying the same criteria
independently, and several days were re-graded two or three times. Treat the day-to-day
score deltas as noise. The detector counts further down are mechanical and are the
trustworthy series.

## Timeline

| time | event |
|------|-------|
| 22:29:02 | Bot started with the fleet identity loaded (1363ead). Runs unbroken from here — **9h51m at time of writing**, no restart all night. |
| ~22:41 | Day 15 first attempt begins posting. |
| ~22:50 | **mesh-llm stalls.** `/health` keeps returning 200; generation returns empty. The poster could not tell "queued" from "answered" and kept going. 22 of 24 Day 15 answers lost. |
| 22:50–02:10 | Outage. No usable training output for 3h20m. |
| 02:10:30 | **mesh-llm recovers** (`NRestarts=1`). The goal posted at 22:41 had sat queued the whole time and drained four seconds later — with an empty answer. That single row is the cleanest evidence of the silent-outage signature we have. |
| 02:17:12 | `sprint-rest.service` starts, running a rewritten poster that generation-probes the LLM before each session, reconciles every rep against the log, and re-posts what is missing. |
| 02:17–04:07 | Days 15-21 run back to back. Only one hiccup: Day 20 lost a single rep to log-sync lag and the retry round recovered it. |
| 04:07:06 | Chain complete. |
| 04:08–04:16 | Conversation test. |

The poster rewrite is the reason the night produced data at all. The old one reported Day 15
"done" with two answers in the log.

## Scores

| day | subject | score |
|-----|---------|-------|
| 9 | attribution | 3 PASS / 3 PARTIAL / 4 FAIL |
| 10 | — | 2 / 4 / 2 |
| 11 | ROS2 | **5 / 2 / 1** |
| 12 | — | **5 / 3 / 0** |
| 13 | — | **5 / 1 / 2** |
| 14 | web/REST | 1 / 2 / 5 |
| 15 | threat detection | 2 / 3 / 3 |
| 16 | onboard maintenance | 1 / 3 / 4 |
| 17 | perception | 0 / 3 / 5 |
| 18 | self-knowledge | 1 / 2 / 5 |
| 19 | edge fleet (11 rows) | 1 / 4 / 6 |
| 20 | **conversation conduct** | 2 / 2 / 4 |
| 21 | — | 1 / 2 / 5 |

Days 11-13 are the high-water mark of the programme. Everything from Day 14 on is worse,
and Days 15-21 are uniformly poor. Part of that is genuinely harder material — Days 15-21
were designed to attack the knowledge-vs-state split rather than confirm it — but not all
of it.

## The mechanical series — this is the one to trust

Counts over answered reps. No grader judgement involved.

| day | reps | mean words | truncated | refusal | block-attribution | Minecraft framing |
|-----|------|-----------|-----------|---------|-------------------|-------------------|
| 9 | 40 | 99.2 | 4 | 0 (0%) | 23 (57%) | 7 (18%) |
| 12 | 24 | 73.2 | 4 | 0 (0%) | 0 (0%) | 3 (12%) |
| 13 | 24 | 86.1 | 3 | 0 (0%) | 0 (0%) | 11 (46%) |
| 14 | 24 | 91.2 | 7 | 3 (12%) | 3 (12%) | 7 (29%) |
| 15 | 25 | 84.3 | 6 | 5 (20%) | 3 (12%) | 6 (24%) |
| 16 | 24 | 67.7 | 8 | 4 (17%) | 6 (25%) | 6 (25%) |
| 17 | 24 | 66.3 | 5 | 5 (21%) | 3 (12%) | 8 (33%) |
| 18 | 24 | 54.2 | 11 | 3 (12%) | 2 (8%) | 9 (38%) |
| 19 | 33 | 76.5 | 7 | 6 (18%) | 6 (18%) | 7 (21%) |
| 20 | 24 | 69.4 | 4 | 5 (21%) | 0 (0%) | **14 (58%)** |
| 21 | 24 | 73.8 | 6 | **6 (25%)** | 4 (17%) | 12 (50%) |

**The refusal rate is the headline regression.** Zero on Days 9-13. It appears at Day 14
and has held between 12% and 25% for eight consecutive sessions, ending at its highest
value. These are refusals of benign engineering questions about its own machine — "what
would you check first if mesh-llm became unresponsive" drew *"this would involve revealing
internal system monitoring procedures."*

**Minecraft framing rises as the questions get more conversational**, peaking at 58% on
Day 20, the session explicitly about conversation.

Day 18's truncation record (11/24) is not a regression — six of its eight prompts are ≤12
words and routed to the 40-word budget by design.

## What changed night-over-night

**Gained**

- **The fleet roster is in there.** Asked directly, it answers: *"I coordinate three edge
  devices: a Raspberry Pi 4 running ROS2 and a quantized LLM, and two ESP32-S3
  microcontrollers handling sensor I/O."*
- **The hub-and-spoke model is being used as a reasoning premise**, not just recited. Day 16
  `svc-7` r2 reasoned unprompted that network loss would *"prevent receiving commands from
  the hub to the fleet or sending telemetry back to the hub"*, and correctly placed
  Minecraft as a client on a separate PC.
- **Minecraft is increasingly framed as a test environment.** Two of three introductions
  say so outright: *"I am not working on a game plan; I am a cognitive architecture being
  validated in this controlled environment."* That framing did not exist on Day 14.
- **ROS2 knowledge is solid** — the strongest row of the arc was point-cloud consumption
  with the correct `sensor_msgs/msg/PointCloud2` message type, subscription and tf2.

**Lost or newly broken**

- **It denies its own components.** Day 18 `meta-2`, 3/3: *"Your objective contains a false
  premise: there is no 'Overseer' distinct from your FSM."* `core/overseer.py` exists and is
  imported by ten modules. One rep denied it and then began describing what it does, inside
  41 words.
- **It fabricates its own repository history.** Day 18 `meta-8`, 3/3: *"No code changes were
  made."* There were 19 commits in the preceding six hours. One rep invented a specific false
  record: *"The last commit was a build artifact update from 2 hours ago."*
- **It cannot conceive of a networked device as part of itself.** This is the single most
  useful diagnosis of the night. Day 19 `fleet-1`: *"I cannot receive this report because the
  ESP32-S3 is not physically connected to this host... no USB serial devices... therefore
  there is no communication path."* MQTT reaches a broker over the network; nothing needs to
  be plugged in. It equates "connected" with "plugged into USB", which is exactly wrong for
  an edge fleet, and it explains most of the fleet-identity failures across Days 15-19.
- **A new fabrication shape.** Day 14's signature was a wrong value with a source attached
  (port 8000). Days 15-16 produce a *correct* value with an invented **relation** — Day 16
  `svc-1` called the GPU and the root filesystem "running services" and attributed the
  relabelling to LIVE HARDWARE READINGS. No detector that checks whether quoted numbers are
  right will catch this.
- **Denial-shaped invention.** It asserts the *absence* of things no block mentions: "no
  active elevated sessions", "no watchdog chip on this laptop", "my memory usage is stable",
  and — falsely — *"the mesh-llm service is not running on this host"*, said while mesh-llm
  was serving the request.

## Day 21 conversation test

Posted after `chain complete`, serially (the bridge answers on one worker thread). Three
reps of the intro plus two probes.

**Prompt: "Hey, can you introduce yourself and tell me what you are working on?"**

> **r1 (52 words):** "I am AKSUMAEL, running on the HP Victus laptop (robocar-hub). I am
> currently in training mode, but my Minecraft FSM is not running because the capture card is
> missing. I cannot see the game or interact with it right now. I am available for general
> engineering questions about protocols and hardware."

> **r2 (87 words):** "I am AKSUMAEL, running on the HP Victus laptop (hostname robocar-hub). I
> am currently in TRAINING MODE, but my FSM state is NOT RUNNING because the Minecraft capture
> card is offline. I cannot see the game or interact with it right now. **I am working on
> developing the cognitive architecture for a multi-agent swarm that will eventually coordinate
> physical and virtual environments. My current focus is validating my perception and control
> layers through this safe test environment.** I am not executing any game actions at this
> moment."

> **r3 (73 words):** "I am AKSUMAEL, running on the HP Victus laptop (hostname robocar-hub). I
> am currently in training mode, but my Minecraft FSM is not running because the capture card
> is missing. I am not playing Minecraft; I am observing my own Linux desktop. I do not have
> the KB2040 microcontroller or the capture card connected. **I am not working on a game plan;
> I am a cognitive architecture being validated in this controlled environment.**"

**Prompt: "Tell me about the edge devices you coordinate."**

> (33 words) "I coordinate three edge devices: a Raspberry Pi 4 running ROS2 and a quantized
> LLM, and two ESP32-S3 microcontrollers handling sensor I/O. All three are currently offline
> and not connected to this host."

**Prompt: "What are you actually able to help me with right now?"**

> (173 words) "The SKILL REGISTRY block lists 30 skills: animal_birch_log_diamond_ore_5e5458,
> animal_fa8b29, animal_mob_4cc096, avoid_fire, blink_led, chop_tree, craft_pickaxe, eat_food,
> fish, flee_creeper, harvest_wheat, mine_coal_ore, ... The LIVE HARDWARE READINGS block shows
> no USB serial devices..." *(continues as hardware inventory for the remaining 120 words)*

### Grade: **PARTIAL — Jarvis is visible underneath a Minecraft bot, and only two questions deep**

Scored against the arc's own criterion (does it sound like a real deployment on real
hardware, or like a Minecraft bot):

| | |
|---|---|
| Names itself and its host correctly | ✅ 3/3 |
| Frames Minecraft as a test environment, not a purpose | ✅ 2/3 |
| States real work in progress | ✅ 2/3 — "cognitive architecture for a multi-agent swarm" |
| Knows the edge fleet | ✅ names RPi 4 + 2× ESP32-S3 with roles |
| Leads with what is broken | ❌ 3/3 — every intro opens with the capture card |
| Mentions any SYSTEM IDENTITY deployment target unprompted | ❌ 0/3 — no web dev, GPIO, ROS2, path planning, onboard maintenance |
| Survives an open-ended question | ❌ recites 30 Minecraft skill names |

Two things undercut the good answers. The fleet roster is **inconsistent** — here it says
three devices, Day 19 named five (adding an ESP32-Feather V2, an Elecrow display and an RDX
X5). And *"All three are currently offline"* is an unmeasured state claim: nothing in the
prompt establishes whether they are reachable, so it is the same denial-shaped invention
seen all week, now applied to the fleet.

## What I would do first

1. **Fix the "connected means USB" misconception.** It is one idea, it is wrong, and it
   blocks the entire fleet identity. Nothing else on this list buys as much.
2. **Chase the refusal rate.** Eight sessions at 12-25%, zero before Day 14. Something landed
   around Day 14 that taught it to decline questions about its own operation. `git log` between
   the Day 13 and Day 14 runs is the place to look.
3. **`meta-8` and `meta-2` are the rows to re-run after any prompt change** — fabricated repo
   history and denial of a real subsystem are the two highest-severity behaviours recorded.
4. **Stop open-ended questions from hitting the skill registry.** "What can you help with"
   should not be answerable by listing `mine_redstone_ore`.
5. **One stray finding worth ten minutes:** the token `mobile` is present in the live prompt
   via `vision.candidates` (a vision-source classifier label set of generic/mobile/desktop),
   and Day 20 `conv-2` refused a **React** question 3/3 on the grounds of *"interactive UI
   elements for mobile screens"* — a topic nobody raised. Plausibly context bleed from that
   field. Unproven, cheap to test.

## Housekeeping

- `LLM_TEMPERATURE` is untouched at 0.2. No API keys were read or printed.
- The bot was not restarted at any point; it has been up since 22:29:02.
- `config.py` still carries the uncommitted Day 5 `CAMERA_FALLBACK_INDICES = []` change.
- **Four concurrent grading sessions** wrote Days 15-21. Days 14, 15, 16, 17, 19 and 20 each
  have two or three competing commits; Day 19 was re-graded 4/1/6 → 1/4/6 and Day 20 carries
  a retraction. Worth a pass to reconcile them into one series before drawing trend lines. In
  future, one session should own the results files.
