# HANDOFF — 2026-08-12

## Current status

Bot running. Jarvis package live (committed fdb51a9).
Git locks stale — run `rm .git/HEAD.lock .git/index.lock` then commit below.

## Commit command (run from vonduke-builds/ root)

```bash
rm .git/HEAD.lock .git/index.lock 2>/dev/null
git add \
  AKSUMAEL/core/capture.py \
  AKSUMAEL/core/cognitive.py \
  AKSUMAEL/core/frame_server.py \
  AKSUMAEL/jarvis/tools.py \
  AKSUMAEL/jarvis/brain.py \
  AKSUMAEL/gesture/ \
  AKSUMAEL/HANDOFF.md \
  robocar/CLAUDE.md \
  robocar/specs/ \
  robocar/nodes/gesture_receiver.py \
  .claude/commands/
git commit -m "major: reid_bridge live, gesture layer, frugality gate, 14 Jarvis tools, CLAUDE.md"
```

## What changed (not yet committed)

### core/capture.py
GatedReIDBridge wired into YOLOThread._run(). Lazy-loads DINOv2 on first tracked
frame. Adds entity_id to each detection when track_mode is active.

### core/cognitive.py
Frugality gate: skips LLM call every other cycle when goal + visible entities +
reward all unchanged. Halves token burn in repetitive loops.

### core/frame_server.py
HUD now shows entity_id (green) when reid_bridge confirms identity, or track_id
(cyan) when tracked but entity not yet confirmed.

### jarvis/tools.py — 14 tools (was 7)
Added: get_system_telemetry, list_usb_devices, get_camera_status,
gpio_read_pins, read_display_info, send_keystrokes, capture_screen

### jarvis/brain.py
- System prompt updated (14 tools, DINOv2 ReID, KB2040 location)
- History persisted to data/jarvis_history.json across restarts

### gesture/ (new package)
- gesture/recognizer.py: MediaPipe hand gesture recognition (5 gestures)
- gesture/dispatcher.py: Routes AKSUMAEL goals + UDP to RoboCar
- gesture/run_gesture.py: Standalone runner
- gesture/install.sh: pip install mediapipe + apt xdotool scrot

### robocar/nodes/gesture_receiver.py (new)
UDP listener; publishes Twist on /robocar_01/cmd_vel in ROS 2 mode,
or prints standalone.

### robocar/CLAUDE.md + robocar/specs/imu-publisher.md (new)
Seed rules + IMU publisher spec (10 requirements, defs of done).

### .claude/commands/spec.md + build.md + review.md (new)
/spec /build /review development workflow commands.

## Open questions for Scott

1. llama.cpp vision backend rebuild — resolved or still blocked on CLIP SIGSEGV?
2. RDK X5 migration: started or still Pi 4 as queen node?
3. Relocation timing — any hardware-boxed window coming?

## To test gesture control

```bash
# On robocar-hub (Victus):
cd ~/vonduke-builds/AKSUMAEL
bash gesture/install.sh
python3 gesture/run_gesture.py --display --no-bot  # test without injecting goals
# Then with bot:
python3 gesture/run_gesture.py --display
```

## To test Jarvis end-to-end

Bot must be running. Press F9, say "what's the bot doing right now?".
Should hear spoken response via TTS.
