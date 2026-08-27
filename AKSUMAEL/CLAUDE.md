# CLAUDE.md — AKSUMAEL Agent Rules

Rules accumulate from real failures. If you make a new mistake, add a rule here.
Prune weekly; past ~50 lines rules get ignored.

---

## Connection

- Confirm the MCP terminal connection to the Victus is live BEFORE any file
  operation. Verify by writing and reading back a scratch file. A dropped
  connection previously caused 3-5 days of changes that were never written
  to disk.

---

## Perception

- Use `model.track(persist=True, tracker="bytetrack.yaml")`. Never
  `model.predict()` — predict() does not produce stable track IDs.
- Track IDs are the join key between the Vision Pipeline and Belief State
  (`entity_id` ↔ `track_id`). Do not renumber or reassign them downstream.
- Trajectory history is a `defaultdict` keyed by `track_id`. Preserve that shape.

---

## Cognition

- Belief State, Goal Stack, Episodic Memory, and Inner Monologue live in
  `core/cognitive.py`. Do not split them across files without an explicit
  instruction.
- The learning loop produces `(belief_state, chosen_action, outcome)` tuples.
  Log these to a structured store before choosing any learning algorithm.

---

## Action layer

- KB2040 HID protocol is newline-terminated ASCII, edge-only transmission,
  chunked mouse deltas. Read `pad_bridge.py` and `code.py` before touching either.
- The 250 ms watchdog that releases held inputs is a safety mechanism.
  Never disable or lengthen it to "fix" a latency problem.
- Planner/pad arbitration is single-ownership. Two writers to the HID bridge
  is a bug, not a feature.

---

## Services

- Always use `systemctl --user restart aksumael.service` — never `bash run.sh`.
- After any bot restart, /dev/video2 may drop. Fix: `sudo modprobe -r uvcvideo && sudo modprobe uvcvideo`.
- `goals.save()` runs in the finally block on SIGTERM. To safely clear goals:
  stop service → wait for exit → clear file → start service.

---

## LLM — local only

- **NO external API calls. Ever.** All inference routes through mesh-llm
  (localhost:9337). If intelligence is lacking, train/fine-tune — don't add
  an API key fallback.
- `LLM_TEMPERATURE` stays at 0.2 in `core/llm_router.py`.

---

## Security

- API keys live at `~/.config/anthropic/key`, `~/.config/google/key`,
  `~/.config/roboflow/key`. Never print values. Never commit.
- `.notes/` is gitignored. Never commit files from it.

---

## Jarvis base mode

- Jarvis IS the default runtime. There is no `JARVIS_MODE` flag. Do not
  re-add one.
- Game behaviors gate on `_game_env = ACTIVE_ENV in GAME_ENVS`. Add new
  envs to `GAME_ENVS` in `config.py`; never pollute base mode with
  game-specific imports.
- If Jarvis gives wrong/looping voice responses: clear poisoned history with
  `echo '[]' > data/jarvis_history.json` and reset `data/state.json` goal
  to `"idle"`. These files are gitignored runtime state.

---

## Voice / Jarvis

- Voice mode: `data/voice_mode.txt` takes priority over `data/axon_mode.txt`.
  Write "ptt" to `data/voice_mode.txt` to enable push-to-talk.
- Game audio bleeds into the laptop mic at ~0.22 RMS; Scott's voice is ~0.099.
  Always-on VAD does not work; PTT (F9) is the correct mode.
- Jarvis brain is the primary voice handler. All transcripts not caught by
  cheap deterministic fast-paths route to `jarvis/brain.py`.

---

## ROS 2 / Hive

- Every ROS 2 topic goes under `/robocar_01/` namespace. No exceptions.
- Do not fork, vendor, or copy Honcho server source (AGPL).

---

## Protected files — do not modify without explicit instruction

- `config.py`
- `data/skills/*.json`
- `day5_results.md`
- `mine_*_ore.json`
- `overnight_monitor.sh`

---

## Commit discipline

- Commit small and often. Prefer scoped commits with real messages over dumps.
- If you leave a task incomplete, write `HANDOFF.md` — where you stopped, what
  you tried, what you'd do next.
- Before reporting a task complete: verify files exist on disk, topics are
  namespaced, no hardcoded bus indices, new deps audited.
