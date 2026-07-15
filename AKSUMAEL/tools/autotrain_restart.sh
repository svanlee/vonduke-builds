#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL Autotrain Restart — detached stop/train/    ║
# ║  restart sequence, run outside aksumael's cgroup       ║
# ╚══════════════════════════════════════════════════════╝
#
# aksumael can't `systemctl --user stop aksumael` from its own thread and
# expect anything to run afterward — systemd kills the whole cgroup (every
# thread in the process) the instant the stop lands. behaviors/auto_trainer.py
# launches this script via `systemd-run --user --scope --slice=background.slice`
# so it lands in its own transient scope/cgroup outside aksumael.service's,
# survives that kill, and carries out the stop -> train -> restart sequence
# on its own.

set -uo pipefail

AKSUMAEL_DIR="$HOME/vonduke-builds/AKSUMAEL"
VENV_PYTHON="$AKSUMAEL_DIR/venv/bin/python3"
TRAIN_LOCK="/tmp/aksumael_training.lock"
MESH_LLM_HEALTH_TIMEOUT_SEC=60
MESH_LLM_HEALTH_POLL_SEC=2

cleanup() {
    rm -f "$TRAIN_LOCK"
}
trap cleanup EXIT

cd "$AKSUMAEL_DIR"

echo "[AUTOTRAIN-RESTART] stopping aksumael and mesh-llm to free VRAM for training..."
# aksumael is very likely already dead (it just launched us and is about to
# be killed by its own stop), but issue the stop anyway in case it's still
# limping along or this script is ever run by hand.
systemctl --user stop aksumael 2>/dev/null
systemctl --user stop mesh-llm 2>/dev/null
sleep 3  # let VRAM clear

echo "[AUTOTRAIN-RESTART] training..."
PYTORCH_MULTIPROCESSING_START_METHOD=spawn "$VENV_PYTHON" tools/yolo_finetune.py train
TRAIN_RC=$?
if [[ $TRAIN_RC -eq 0 ]]; then
    echo "[AUTOTRAIN-RESTART] training complete"
else
    echo "[AUTOTRAIN-RESTART] training failed (exit $TRAIN_RC) — restarting services anyway"
fi

echo "[AUTOTRAIN-RESTART] restarting mesh-llm..."
systemctl --user start mesh-llm

echo "[AUTOTRAIN-RESTART] waiting for mesh-llm to become healthy..."
"$VENV_PYTHON" - "$MESH_LLM_HEALTH_TIMEOUT_SEC" "$MESH_LLM_HEALTH_POLL_SEC" <<'EOF'
import json, sys, time, urllib.request

timeout, poll = float(sys.argv[1]), float(sys.argv[2])
url = 'http://localhost:9337/v1/models'
waited = 0.0
while waited < timeout:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        models = [m.get('id') for m in data.get('data', [])]
        # The endpoint answers 200 with an empty list while the API layer is
        # up but no model has finished loading yet — not "healthy" yet.
        if models:
            print(f'[AUTOTRAIN-RESTART] mesh-llm healthy after {waited:.0f}s — models loaded: {models}')
            sys.exit(0)
    except Exception as e:
        if waited == 0 or waited % 10 == 0:
            print(f'[AUTOTRAIN-RESTART] mesh-llm not healthy yet ({e}) — {waited:.0f}s/{timeout:.0f}s')
    time.sleep(poll)
    waited += poll
print(f'[AUTOTRAIN-RESTART] mesh-llm did not become healthy within {timeout:.0f}s — starting aksumael anyway')
sys.exit(1)
EOF

echo "[AUTOTRAIN-RESTART] restarting aksumael..."
systemctl --user start aksumael

echo "[AUTOTRAIN-RESTART] done."
