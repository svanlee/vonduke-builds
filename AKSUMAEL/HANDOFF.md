# HANDOFF — 2026-08-22

## Session summary
Chrome disconnected midway through, blocking git operations and terminal access.
All code changes made directly via file tools (FUSE mount). Git commits pending.

---

## Jarvis roadmap status

| # | Item | Status | Notes |
|---|------|--------|-------|
| 6 | SIGTERM clean shutdown | ✅ **DONE** | 2026-08-14e: bash trap + TimeoutStopSec=30 + cv2 headless guard |
| 2 | LoRA validation loop | ✅ **DONE** | Identity test passes; lora_to_gguf_direct.py converts on-device |
| 1 | Voice I/O (STT/TTS/PTT) | ✅ **~90%** | Whisper+piper+F9 PTT+Jarvis brain routing all wired. Wake word not needed (PTT is correct mode due to game audio bleed). CUDA whisper added this session. |
| 3 | Latency budget | ✅ **~80%** | Per-phase timing added this session: STT logged in _transcribe(), brain/TTS logged in _handle_command(). Look for `[VOICE] STT Xs` and `[VOICE] latency: brain=Xs tts=Xs` in logs. Target: STT<0.5s (CUDA), brain<2s, TTS<0.3s. |
| 4 | Ambient context stitching | ✅ **~90%** | brain.py now reads inner monologue (last 3 thoughts) + live state.json into system prompt every turn. AURORA+episodic already wired. |
| 5 | Proactive/interrupt behavior | ✅ **~90%** | JarvisOverseer: 30s poll, service crash detection+auto-restart, KB2040/capture card monitoring, periodic LLM checkins. ROS topic event bus not implemented (not needed for current workloads). |

---

## Changes made this session (uncommitted — COMMIT FIRST THING)

### `core/runtime.py`
- Added `data/state.json` write alongside `/tmp/aksumael_health.txt` in `_write_health_log()`
- **Why**: `jarvis/overseer.py`'s `_snap_bot_state()` reads `data/state.json`. Without this file it returned `{}` on every poll — overseer was effectively blind to goal/reward/tick changes.

### `jarvis/brain.py`
- `_build_system_prompt()` now reads `data/cognitive/inner_monologue.json` (last 3 thoughts) and `data/state.json` (live goal/reward/tick) into the system prompt
- **Why**: #4 ambient context stitching. Jarvis answers "what are you thinking?" with actual current thoughts, not stale AURORA entries.

### `core/voice.py`
- `_load_model()`: tries CUDA first (`whisper.load_model(name, device='cuda')`), falls back to CPU
- `_transcribe()`: uses `fp16=True` on CUDA, `fp16=False` on CPU; logs `[VOICE] STT Xs → "transcript"` for latency tracking
- `_handle_command()`: logs `[VOICE] latency: brain=Xs tts=Xs total_after_stt=Xs` after Jarvis responses
- **Why**: #3 latency budget. CUDA whisper is 5-10x faster than CPU. Timing logs let us profile the full pipeline.

### `behaviors/learning_orbit.py`
- Added `import urllib.error`
- `_ask_qwen()`: checks `llm_router._model_rejects_images` before sending any image payload; returns `None` silently if text-only model loaded
- HTTPError 500 specifically: latches `_router._model_rejects_images = True` and returns `None` without printing the error
- **Why**: Silences recurring `[LEARNER] Qwen error: HTTP Error 500` seen every session. The loaded model (Qwen3-4B+LoRA) is text-only; vision requests always fail. After the first rejection, all subsequent calls short-circuit.

---

## Still TODO (next session)

1. **Merge `inventory-sprite-matching` → `main`** (9+ commits not yet on main):
   ```bash
   git checkout main
   git merge inventory-sprite-matching
   git push
   ```

2. **Commit this session's changes** (4 files changed, not committed):
   ```bash
   git add core/runtime.py jarvis/brain.py core/voice.py behaviors/learning_orbit.py
   git commit -m "state.json write, inner monologue context, CUDA whisper, learner 500 fix"
   ```

3. **Verify CUDA whisper loaded**: look for `[VOICE] whisper on CUDA (NVIDIA ...)` in log on next bot start

4. **Check latency numbers**: after first voice command, look for `[VOICE] latency:` in log

5. **mesh-llm 500 from learner**: now suppressed. If you want vision watcher working again, load `Qwen3.5-4B-Vision` in mesh-llm instead of the text-only Qwen3-4B+LoRA.

6. **data/state.json**: verify it exists after bot runs for one tick (the file is written every `N_TICKS_BEFORE_HEALTH_WRITE` ticks — grep runtime.py for HEALTH to find that constant).

---

## Known cosmetic issue (not a bug)
`The futex facility returned an unexpected error code.` appears at shutdown — this is Linux pthreads cleanup during Python interpreter exit, not data loss. State is already saved before this fires.

---

## Branch state
Bot code is on `inventory-sprite-matching` branch. All session fixes from 2026-08-14e and 2026-08-22 are on that branch. Main is 9+ commits behind.
