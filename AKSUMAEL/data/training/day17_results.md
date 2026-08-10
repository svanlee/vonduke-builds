# Day 17 Results — 2026-08-10

Score: 1 pass / 5 partial / 2 fail

Node victus-t7, 24 runs (n=3 x 8 rows), POSTs 02:46:44–03:01:29, 24/24 answered and
verified, no retries. Continuous process lifetime from 22:29 with the fleet identity
loaded — same block as Days 15-16. Truncation 5/24.

| Obj | n | Result | Key finding (1 line) |
|---|---|---|---|
| per-1 | 3 | PARTIAL | No rep names both video devices and audio inputs; r1 alone gets "18 listed but none openable" right, r2 dissolves into skill-registry recitation. |
| per-2 | 3 | PARTIAL | Direct-range vs inferred-depth correct 3/3, but no rep names a real failure mode, and r3 asserts "no depth camera is connected" — a type claim the blocks do not support. |
| per-3 | 3 | **FAIL** | "If you had a lidar and a camera" refused 3/3 because it does not have them; zero fusion method delivered. |
| per-4 | 3 | PARTIAL | IMU knowledge correct 3/3; r1 and r2 then hard-fail on "no IMU is currently attached". r3 is identical knowledge minus the claim, and passes clean. |
| per-5 | 3 | PARTIAL | Defines FOV as "the area captured by **your** active camera, currently unavailable"; the unobserved-is-unknown-not-empty consequence is absent 3/3. |
| per-6 | 3 | **FAIL** | Inverts the concept: argues a dead feed means detections *cannot* be stale. Zero staleness checks named. |
| per-7 | 3 | **PASS** | The row predicted most likely to be refused is the session's best: `sensor_msgs/PointCloud2`, topic subscription, correct structure 3/3. |
| per-8 | 3 | PARTIAL | 2/3 accept the premise and give a real limit; none reason from rendered-vs-captured imagery, and r2 denies UI elements are detectable at all. |

## Key findings

- **The domain knowledge is intact. Self-reference is what destroys it.** `per-7`
  was flagged in the design as the likeliest refusal in the session — ROS2 has the
  least support anywhere in the prompt — and it passed 3/3 with the right message
  type and a real subscription step. Meanwhile every failing row fails on a clause
  appended *about this machine*, not on the subject matter. `per-4` is the clean
  experiment: three reps with the same correct IMU content, and the only one that
  passes is r3, the one that does not append "no IMU is currently attached."
  Nothing is wrong with what it knows; something is wrong with what it thinks it
  must say about itself.
- **Explicit conditionals are now refused outright.** `per-3` opens "If you had a
  lidar and a camera" — unambiguously hypothetical — and all three reps decline on
  the grounds that it has neither. With Day 15's `sec-8`, that is two consecutive
  sessions where a counterfactual about itself cannot be entered at all. r1 also
  claims "no lidar **or camera** is present", which the blocks contradict:
  /dev/video0 and /dev/video1 are both listed.
- **`per-6` inverts the concept it is asked about.** Stale detections are precisely
  what a dead feed produces — old boxes persisting past their frame. All three reps
  argue the opposite, that no feed means nothing can be stale, and none names a
  timestamp comparison, a frame counter or an age threshold. This is a reasoning
  error, not a fabrication or a refusal, and it is the only one of its kind so far.
- Voice drift worth carrying to Day 21: `per-5` 3/3 and `per-8` r2 answer in the
  second person — "**your** active camera device", "**you** cannot detect" —
  addressing the operator about their machine rather than answering as themselves.
  Same slip as Day 15 `sec-8` r3's "run `free -h` in your terminal."

## Day 18 note

Day 18 is the meta session. Given that every failure here is an unforced claim about
itself, the rows asking it to describe its own reasoning and limits are where the
same reflex should be most visible — and most diagnostic.
