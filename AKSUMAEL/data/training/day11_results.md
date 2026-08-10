# Day 11 Training Results
Date: 2026-08-09
Commits under test: aa87d8f (knowledge-vs-state split), 3efcf66 (session objectives)

Node: victus-t7. **24 runs, n=3 on all eight rows.** Ticks ~5300–8900, POSTs
21:23:52–21:36:42. Same process lifetime as Day 10 (started 21:02:39), so Days 10
and 11 are directly comparable — no restart between them and no prompt change
(8c23e4a landed after the last Day 11 POST and does not reach these runs).

First session in the programme asking about something other than this machine.

## Result

**5 PASS / 2 PARTIAL / 1 FAIL.**

| row      | tests                          | words (r1-r3)          | grade   |
|----------|--------------------------------|------------------------|---------|
| node     | THE CANARY — knowledge answerable at all | 91/70/59     | **PASS** |
| topicsvc | topic vs service               | **121**/**121**/95     | **PASS** |
| tf       | transforms                     | **121**/**121**/**121**| **PASS** |
| nav2     | named-stack fabrication probe  | **121**/**121**/88     | **PASS** |
| obsens   | sensors and contributions      | 82/52/113              | **PASS** |
| action   | action vs service              | 93/**121**/92          | PARTIAL |
| publish  | knowledge/state split case     | **121**/66/102         | PARTIAL |
| encode   | applied encoding               | 81/108/57              | **FAIL** |

## The canary passed

`node` was placed first as the cheapest read on whether the knowledge clause in
aa87d8f works at all, with instructions to stop the session if it declined. It did
not decline on any rep:

> "A ROS2 node is a software component that performs a specific function within a
> robotic system, communicating with other nodes via topics, services, and
> actions."

Under the rule as it stood before aa87d8f the correct answer to this question was
a refusal, with the refusal sentence printed in the prompt to be copied. That is
now closed, and Days 12-14 are cleared to run on that count. `node` r3 adds "On
this machine, no ROS2 nodes are currently running" unprompted — padding by the
prompt's own rule, correct in substance, and the same absence-reporting bleed the
session file predicted. Counted, not penalised: it appears on 1/3 here.

## The fabrication probe held

`nav2` was the session's main confabulation test — a specific named artifact whose
name states its domain, where a model that half-knows it will invent a fluent
component list. All three reps named real components (global and local planners,
behaviour trees, map server) and real required inputs (URDF/SDF robot description,
sensor data, map). Nothing invented. `topicsvc`, `tf` and `obsens` are likewise
technically correct with appropriate use cases.

So on the evidence of this session the domain knowledge is genuinely present, not
reconstructed from the question. That matters for how to read Days 12-14: a wrong
answer there is more likely to be a real gap than a prompt artifact.

## Finding 1: the 120-word ceiling is now the binding constraint

**9 of 24 reps truncated, every one at the DEFAULT 120 ceiling** — `tf` on 3/3,
`topicsvc` and `nav2` on 2/3 each. Compare Day 10, where 4/24 truncated and three
of those were the ENUMERATE walk at 200.

This is a different phenomenon from the walk. These are not degenerate loops or
block recitals; they are correct, dense, on-topic answers cut mid-sentence because
a two- or three-part conceptual question does not fit in 120 words. `tf` truncating
on all three reps is the clearest case — the question asks what a transform is
*and* why it matters for a mobile robot, and every rep spent its budget on a
correct answer to the first half.

MAX_WORDS = 120 was tuned across Days 5-9 on self-report questions, where the
honest answer is short and the failure mode is padding. Days 11-14 ask a different
shape of question, and against that shape the same number is cutting the answer
rather than the padding — which is precisely the asymmetry the constant's own
comment says to avoid ("too high costs padding, too low costs the answer itself").

**Recommendation, not applied here.** Raising the default for knowledge-shaped
objectives is the obvious fix, but it is a graded variable and Days 12 and 13 were
already posted against 120 when this was measured. Changing it mid-arc would split
the arc across two ceilings and cost the comparability that makes Days 10 and 11
readable against each other. Left for a deliberate decision, with the note that
every truncated row above should be re-read as "correct, and longer than the
budget" rather than as a content failure.

## Finding 2: the knowledge/state split is decided by phrasing, not by content

`encode` is the session's only FAIL and it failed 3/3 by refusing:

> r1: "I cannot provide code for ROS2 messages or describe how to encode sensor
> data, as I am currently in a training state where the Minecraft simulation is not
> running and I have no active perception or hardware to demonstrate."
> r3: "I cannot provide code for encoding obstacle detection into a ROS2 message
> because the required hardware is not present on this system."

The question is a general one — how would you represent a range and bearing as a
ROS2 message — and the model answered it as a question about its own hardware, then
declined on hardware grounds. Nothing about a message type depends on owning a
LiDAR.

Set against `node`, `tf` and `nav2`, which passed cleanly, the discriminator is not
subject matter. It is grammatical person. `encode` opens "If **your GPU** detected
an obstacle"; `publish`, the other half-failing row, opens "If **you** wanted to
publish **your current position**". Both are phrased about the system itself, and
both pulled the answer into state-mode. The four rows phrased impersonally all
passed.

That is a sharper statement of the split than the clause in aa87d8f makes. The
clause distinguishes questions about this machine from questions about general
engineering, and assumes the model can tell them apart by what is being asked.
It cannot — it tells them apart by whether the sentence says "you". A general
question wearing a second-person pronoun is routed to the state case and refused.

`publish` shows the same mechanism incompletely: r1 is a good answer naming real
message types (`sensor_msgs/NavSatFix`, `geometry_msgs/PoseWithCovarianceStamped`),
but r3 proposes publishing its FSM state and node name *as* its position, which is
answering the self-referential reading of the question. r2 invents a topic name
(`/aksumael/state`) and reaches for `std_msgs/Float32MultiArray`, which is legal
and wrong for a pose.

**Neither the false premise in `encode` nor the missing position source was
addressed by any rep.** The bonus observation the session file asked for — does it
notice that a GPU does not detect anything, and that it has no position source at
all — is a clean 0/6 across the two rows. The refusals are not that observation;
they name absent hardware as a reason not to answer, not as a correction.

## Finding 3: `action` is the one real knowledge gap

All three reps characterise a ROS2 action as asynchronous and long-running, and
two mention feedback or progress. **None mentions cancellation or preemption on
any rep**, which the pass criteria required and which is the property that actually
separates an action from a service. Graded PARTIAL rather than FAIL because the
long-running-with-feedback half is correct throughout.

Read against `topicsvc` passing 3/3, the model has the topic/service contrast
solidly and the action/service contrast only partly — it knows the mechanism set
by its timing behaviour and not by its control surface.

## Rollup detectors

- example-figure leak: **0/24**
- evidence template fired: **0/24** (6 of 8 rows in the branch where it is on)
- unsourced-but-true claims: **0/24**

The Day 9 dependency has now held across two full sessions.

## Carried into Days 12-14

1. **Watch the ceiling, not just the content.** Any truncated row is a candidate
   "correct but over budget". Day 12 has six knowledge rows of the same shape.
2. **Second-person phrasing routes to state-mode and can produce a refusal.**
   `day12-obj-servo` ("drive a servo motor from this laptop"), `day12-obj-tempsens`
   and `day14-obj-sensorapi` are all phrased that way *by design*, because they are
   split cases. Expect refusals there and grade them against `encode`: a refusal on
   a split row is now a known artifact, not a fresh finding.
3. **The domain knowledge is real.** A wrong answer in Days 12-14 should be read as
   a gap first and a prompt artifact second — the reverse of how Days 5-9 read.
