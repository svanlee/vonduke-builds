#!/bin/bash
set -e
cd ~/vonduke-builds

echo "=== Step 1: Reset the bad commit (keep files on disk) ==="
git reset --soft HEAD~1

echo "=== Step 2: Fix root .gitignore ==="
grep -qx 'aksumael-assets/' .gitignore 2>/dev/null || echo 'aksumael-assets/' >> .gitignore
git rm --cached aksumael-assets 2>/dev/null || true

echo "=== Step 3: Fix AKSUMAEL/.gitignore ==="
cat >> AKSUMAEL/.gitignore << 'EOF'

# Large data — never commit
data/lerobot/
data/yolo_dataset/
data/models/*.pt
data/models/*.pth
data/skill_evaluator.db
data/memory.db

# Runtime captures
debug_snapshot_latest.jpg
digging_now.jpg
frame_live.jpg
latest.jpg
live_frame_latest.jpg
burst_*.jpg
current_view.jpg
*.tmp
EOF

echo "=== Step 4: Remove large paths from index ==="
git rm --cached AKSUMAEL/data/lerobot/ -r --quiet 2>/dev/null || true
git rm --cached AKSUMAEL/data/models/sam2.1_hiera_tiny.pt --quiet 2>/dev/null || true
git rm --cached AKSUMAEL/data/yolo_dataset/ -r --quiet 2>/dev/null || true
git rm --cached AKSUMAEL/data/skill_evaluator.db --quiet 2>/dev/null || true
git rm --cached AKSUMAEL/data/memory.db --quiet 2>/dev/null || true

echo "=== Step 5: Remove runtime JPGs/tmp from index ==="
git diff --cached --name-only | grep -E '\.(jpg|tmp)$' | xargs git rm --cached --quiet 2>/dev/null || true

echo "=== Step 6: Add gitignores ==="
git add .gitignore AKSUMAEL/.gitignore

echo "=== Step 7: Add the real code files ==="
git add \
  AKSUMAEL/audio/device_probe.py \
  AKSUMAEL/axon/speaker.py \
  AKSUMAEL/core/code_skill_generator.py \
  AKSUMAEL/core/human_assist.py \
  AKSUMAEL/core/self_editor.py \
  AKSUMAEL/data/axon_mode.txt \
  AKSUMAEL/data/debug_snapshot.jpg \
  AKSUMAEL/data/goals.json \
  AKSUMAEL/data/last_train.json \
  AKSUMAEL/data/progression.json \
  AKSUMAEL/data/handoff_2026_07_21.md \
  AKSUMAEL/data/q_table.json \
  AKSUMAEL/data/world_memory.json \
  AKSUMAEL/data/world_model.json \
  AKSUMAEL/data/models/configs/ \
  AKSUMAEL/memory/hud_reader.py \
  AKSUMAEL/memory/world_memory.py \
  AKSUMAEL/run.sh \
  AKSUMAEL/skills/skill_system.py \
  AKSUMAEL/stop.sh \
  AKSUMAEL/tools/ \
  AKSUMAEL/uart/kb2040_packer.py \
  AKSUMAEL/vision/f3_reader.py \
  DEVLOG.md 2>/dev/null || true

# Add skill files (but not .tmp)
git add AKSUMAEL/data/skills/ 2>/dev/null || true
git rm --cached $(git diff --cached --name-only | grep '\.tmp$') --quiet 2>/dev/null || true

# Add piper voice metadata (json only, not .onnx binaries)
git add AKSUMAEL/data/piper_voices/*.json 2>/dev/null || true
git rm --cached $(git diff --cached --name-only | grep '\.onnx$') --quiet 2>/dev/null || true

# Add episode memory jsonl files
git add AKSUMAEL/data/memory/ 2>/dev/null || true

echo "=== Step 8: Verify no huge files staged ==="
echo "Largest staged files:"
git diff --cached --name-only | xargs -I{} bash -c '[ -f "{}" ] && stat -c "%s {}" "{}" 2>/dev/null' | sort -rn | head -10

echo "=== Step 9: File count check ==="
echo "Staged file count: $(git diff --cached --name-only | wc -l)"

echo "=== READY — review above then run: git commit + git push ==="
