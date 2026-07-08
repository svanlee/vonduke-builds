# AKSUMAEL — Gemini/LLM Throttling Investigation

Date: 2026-07-08

## Symptom reported

- `grep -n 'ask_vision' main.py` returns nothing.
- `grep -n 'LLM_EVERY_N_TICKS' main.py` returns nothing.
- Live console output showed `Gemini error 429` repeatedly, suggesting Gemini
  was being called on every tick instead of every `LLM_EVERY_N_TICKS` (5) ticks.

## 1. Where the vision/LLM call actually happens

Not in `main.py`. `main.py` is a 2-line launcher:

```python
from core.runtime import run

if __name__ == '__main__':
    run()
```

The real decision loop — including the Gemini call — lives in `core/runtime.py`,
inside `run()`:

```python
# core/runtime.py
from core.vision_brain import ask_vision
...
else:
    # Try a learned skill first
    skill, match = skills.find_best(objects)
    if skill and match >= skills.MIN_MATCH_SCORE:
        ...
    # Fall back to Gemini
    elif tick % config.LLM_EVERY_N_TICKS == 0:
        ...
        action_dict = ask_vision(frame, history, objects)
```

`ask_vision()` itself lives in `core/vision_brain.py`, which dispatches to
`_ask_gemini()` or `_ask_claude()` based on `config.VISION_PROVIDER`.

This fully explains the grep results: `ask_vision` and `LLM_EVERY_N_TICKS`
are never referenced in `main.py` by name — they're only used two files
away, in `core/runtime.py`. Grepping `main.py` was grepping the wrong file.

## 2. Why LLM_EVERY_N_TICKS appeared not to be respected

This is the surprising part: **as currently written on disk, it is respected.**

`core/runtime.py` gates the Gemini call with:

```python
elif tick % config.LLM_EVERY_N_TICKS == 0:
```

This only fires on ticks 5, 10, 15, ... (after first checking for a matching
learned skill). I could not find any code path that bypasses this:

- `ask_vision`/`_ask_gemini` has exactly one call site in the whole tree
  (confirmed via `grep -rn "ask_vision\|_ask_gemini\|generativelanguage" .`
  across all `.py` files, excluding `__pycache__`).
- No retry loop inside `vision_brain.py` — a single `urllib.request.urlopen`
  call per invocation, with error handling that returns a JSON error dict,
  not a retry.
- `ui/labeling.py` (which runs every tick, ungated) does not call vision/Gemini
  independently — checked its imports and `update()` method.
- No stale `.pyc` bytecode: pycache timestamps are newer than their
  corresponding `.py` source files everywhere, so Python would have
  recompiled fresh rather than run outdated bytecode.
- `skills.find_best()` / `MIN_MATCH_SCORE` (0.5, in `skills/skill_system.py`)
  don't raise or otherwise short-circuit the modulo check.

Given the on-disk code is already correctly gated, the most plausible
explanation for the observed every-tick Gemini calls is that **the live
process was started before this gating logic existed in memory and was
still running stale imported code** — Python does not hot-reload edited
modules in a running process, so editing `config.py`/`core/runtime.py`
after the process starts has no effect until it's restarted. No process
was running at investigation time (`ps aux` / `ps -ef` came back empty),
so this could not be confirmed directly against a live process.

## 3. Is main.py the real entrypoint?

Yes — but there are **two separate, unsynced copies of this codebase on disk**,
which is the more important discovery:

| Path | Status | Notes |
|---|---|---|
| `/home/pi/vonduke-builds/AKSUMAEL` | git repo | Last commit 2026-07-05 ("full codebase — reactive GoalStack, 18-class train-6 weights, cognitive smoke test") |
| `/home/pi/AKSUMAEL_v1_0_0` | untracked directory | Last edited mid-June 2026 (`core/runtime.py` June 11, `config.py` June 15). Has its own `data/`, `runs/` (YOLO training output), `cutouts/`, `yolov8n.pt` |
| `/home/pi/AKSUMAEL_v1_0_0.zip` | untracked snapshot | Same-named zip backup of the above, dated 2026-07-04 |

`.bash_history` shows `cd ~/AKSUMAEL_v1_0_0` repeatedly, along with
`python3 main.py` invocations issued from that directory (right after
i2c/joystick hardware tests) — strongly suggesting **`~/AKSUMAEL_v1_0_0` is
the copy that has actually been run**, not the git repo.

Both copies' `main.py` correctly forward to `core.runtime.run()`, and both
already contain the `tick % LLM_EVERY_N_TICKS` gate (it's present even in
the older, June 11 version in `AKSUMAEL_v1_0_0`). So this isn't a
"recently fixed" situation either — the gate has been there all along in
both trees.

Other things ruled out while confirming the entrypoint:
- No systemd service, cron job, rc.local entry, or autostart file references
  AKSUMAEL anywhere on the system.
- No tmux/screen sessions (neither binary is even installed).
- `PYTHONPATH` is unset; no shell alias/function wraps `main.py`.
- `config.py.bak` (present as an untracked file in the git repo) is
  byte-identical to `config.py` — just a backup, not a divergent version.

## 4. Fix

No code change was made to the throttling logic itself, because the gating
in `core/runtime.py` (`elif tick % config.LLM_EVERY_N_TICKS == 0:`) is
already correct in both copies of the codebase — there was no bug to patch
there.

What actually needs to happen operationally:

1. **Restart the AKSUMAEL process** whenever `config.py` or `core/runtime.py`
   is edited. A running process keeps using the module state it imported at
   startup; editing files on disk has no effect on it until it's killed and
   relaunched. This is the most likely reason Gemini appeared to fire on
   every tick despite the config value being 5.
2. **Decide which directory is canonical** and stop running the stale one.
   `~/AKSUMAEL_v1_0_0` (and its `.zip`) is a June snapshot drifting further
   from the git repo (`~/vonduke-builds/AKSUMAEL`) every time it's edited
   independently. Recommend reconciling/merging into the git repo and
   removing (or archiving) the untracked duplicate so there's a single
   source of truth and future "is this actually running the code I'm
   editing" confusion doesn't recur.
3. Next time the process is live, confirm the real entrypoint directly with:
   ```
   ps -ef | grep main.py
   readlink /proc/<pid>/cwd
   ```
   rather than inferring it from `main.py` alone.

## 5. Reconciliation (follow-up, 2026-07-08)

Per request, the two directories were reconciled and the stale one removed.
A full comparison (code diff, data diff, md5 of model weights) showed the
`.bash_history`-based guess in section 3 was backwards: **the git repo
(`~/vonduke-builds/AKSUMAEL`) was actually the more advanced copy**, not
`~/AKSUMAEL_v1_0_0`:

- Code: `core/cognitive.py`, `test_cognitive.py`, and `.gitignore` existed
  only in the git repo; `core/runtime.py`, `core/vision_brain.py`, and
  `config.py` all differed, with the git repo's versions being current.
  `AKSUMAEL_v1_0_0` had no code the git repo lacked — its
  `rp2040/boot.py.bak` was just a cruder, superseded draft of the same file.
- Data: `data/models/aksumael_mc.pt` in the git repo was byte-identical
  (md5) to `AKSUMAEL_v1_0_0/runs/detect/train-6/weights/best.pt` — the
  final trained model was already safely committed. `data/world_model.json`
  in the git repo was strictly ahead (session 5 vs. session 4). Skills data
  and `yolo_labels.json` were identical between the two.
- The only content unique to `AKSUMAEL_v1_0_0` was: 4 raw frame archives
  (`new_frames.zip`, `new_frames2/3/4.zip`, ~40MB) not yet present in the
  git repo's dataset, one cutout image (`cutouts/creeper.png`), the 86MB
  `runs/` training-run history (checkpoints/plots for every run through
  train-6 — byproduct of producing the already-committed `aksumael_mc.pt`),
  and a stock pretrained `yolov8n.pt` (auto-downloadable via `ultralytics`).

Actions taken:
1. Copied the 4 raw frame zips into
   `data/yolo_dataset/images/train/` in the git repo.
2. Copied `cutouts/creeper.png` into a new `cutouts/` dir in the git repo.
3. Discarded `runs/` (per user decision — regenerable training byproduct,
   final weights already committed) and the stock `yolov8n.pt`.
4. Deleted `/home/pi/AKSUMAEL_v1_0_0` and `/home/pi/AKSUMAEL_v1_0_0.zip`
   entirely (per user decision on the zip backup).

`/home/pi/vonduke-builds/AKSUMAEL` is now the single canonical copy of
this project. The reconciliation (including `cutouts/` and the 4
`new_frames*.zip` archives) was committed as `59ac632` and pushed to
`origin/main`. The stray, always-identical `config.py.bak` was deleted
separately (it was never tracked by git).

## 6. Actual root cause found and fixed (follow-up, 2026-07-08)

Section 2 concluded the on-disk gating logic looked correct and speculated
the discrepancy was a stale in-memory process. That speculation was wrong.
Restarting AKSUMAEL fresh (`venv/bin/python3 main.py`, real hardware: HDMI
capture card, KB2040, YOLO model, Gemini key all live) reproduced the
"every tick looks like an LLM call" symptom immediately, which made the
real bug visible:

```python
# core/runtime.py, decision block
action_dict = _idle()
used_skill  = None
src_tag     = 'LLM'          # <-- default set unconditionally, before the gate

if replayer.is_active():
    ...
else:
    skill, match = skills.find_best(objects)
    if skill and match >= skills.MIN_MATCH_SCORE:
        ...                   # overwrites src_tag with 'SK:...'
    elif tick % config.LLM_EVERY_N_TICKS == 0:
        ...
        src_tag = 'LLM'       # overwrites src_tag with 'LLM' again (redundant here)
```

`src_tag` defaults to `'LLM'` before the skill/tick-gate check ever runs.
On any tick where no skill matches **and** `tick % LLM_EVERY_N_TICKS != 0`,
neither branch executes, so `src_tag` is never reassigned — it just stays
at its default value of `'LLM'`. The console log then prints `LLM` on
every line, whether or not Gemini was actually called that tick. The
*actual* Gemini network call frequency was correct the whole time (one
call every 5 ticks, as configured); only the log line was mislabeled,
making it look like every tick called Gemini.

**Fix**: changed the default at `core/runtime.py:142` from
`src_tag = 'LLM'` to `src_tag = 'idle'`.

**Verification** — live run before fix — every tick tagged `LLM` regardless of whether
`ask_vision` ran (only tick 5's timing/observation showed an actual call):

```
[0001] 8.2s  | LLM  | conf:0.00 | ...
[0002] 1.28s | LLM  | conf:0.00 | ...
[0003] 1.25s | LLM  | conf:0.00 | ...
[0004] 1.38s | LLM  | conf:0.00 | ...
[0005] 3.74s | LLM  | conf:0.00 | { ...        <- actual Gemini call
[0006] 1.21s | LLM  | conf:0.00 | ...
```

Live run after fix — `idle` on non-throttled ticks, `LLM` only on ticks
5 and 10:

```
[0001] 6.51s | idle | conf:0.00 | ...
[0002] 1.2s  | idle | conf:0.00 | ...
[0003] 1.17s | idle | conf:0.00 | ...
[0004] 1.19s | idle | conf:0.00 | ...
[0005] 1.44s | LLM  | conf:0.00 | Gemini error 429: ...
[0006] 1.11s | idle | conf:0.00 | ...
[0007] 1.22s | idle | conf:0.00 | ...
[0008] 1.1s  | idle | conf:0.00 | ...
[0009] 1.16s | idle | conf:0.00 | ...
[0010] 1.43s | LLM  | conf:0.00 | Gemini error 429: ...
[0011] 1.1s  | idle | conf:0.00 | ...
```

Confirms `LLM_EVERY_N_TICKS` throttling was, and now visibly is, working
as configured.

**Separate, still-open issue**: the calls that do go out are hitting
Gemini `429` (rate limit) on both observed occurrences (ticks 5 and 10).
This is a quota/rate-limit problem with the configured Gemini free tier,
unrelated to the throttling bug above — worth a follow-up look at
`LOOP_INTERVAL_SEC` / `LLM_EVERY_N_TICKS` against Gemini's actual current
free-tier RPM/RPD limits, or adding backoff/retry in `_ask_gemini`.
