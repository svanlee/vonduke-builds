#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL Overnight Training Supervisor               ║
# ║  Cycles goal injections to build training data for   ║
# ║  trees, animals, and HUD bars over ~7 hours.         ║
# ║                                                        ║
# ║  Usage: bash tools/overnight_supervisor.sh &          ║
# ║  Logs:  tail -f /tmp/aksumael_overnight.log           ║
# ╚══════════════════════════════════════════════════════╝

AKSUMAEL_DIR="$HOME/vonduke-builds/AKSUMAEL"
GOALS_FILE="$AKSUMAEL_DIR/data/injected_goals.json"
LOG="/tmp/aksumael_overnight.log"
MAIN_PY_PATTERN="$AKSUMAEL_DIR/venv/bin/python3 -u main.py"

log() { echo "[OVERNIGHT $(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

inject() {
    local goal="$1"
    local authority="${2:-3}"
    echo "{\"queue\": [{\"goal\": \"$goal\", \"authority\": $authority}]}" > "$GOALS_FILE"
    log "Injected goal: $goal (authority $authority)"
}

log "Overnight supervisor started. Goals will cycle to build YOLO training data."
log "Bot must already be running. Check: pgrep -f 'main.py'"

# ── Cycle plan ─────────────────────────────────────────────────
# Each phase runs for PHASE_MINUTES before cycling.
# The bot naturally generates tree frames via tree_auto_labeler.py
# and animal frames via YOLO while hunting. Survey behavior fires
# when YOLO confidence is low (trees, fish, vines).

PHASE_MINUTES=20    # switch goals every 20 minutes

GOALS=(
    "find_and_chop_tree"   # birch/oak frames via tree_auto_labeler
    "find_and_chop_tree"   # double time on trees — biggest data gap
    "find_food"            # hunt cows/pigs/sheep — animal YOLO data
    "find_and_chop_tree"   # more tree frames
    "fish_for_food"        # fish + water frames
    "find_and_chop_tree"   # trees again
    "find_food"            # animals again
    "explore"              # general coverage — vines, leaves, terrain
    "find_and_chop_tree"   # trees
    "find_food"            # animals
    "fish_for_food"        # fish
    "find_and_chop_tree"   # trees to finish strong
)

phase=0
total_phases=${#GOALS[@]}

while true; do
    # Check if bot is still alive
    if ! pgrep -f "$MAIN_PY_PATTERN" >/dev/null 2>&1; then
        log "WARNING: AKSUMAEL main.py is NOT running. Waiting 60s..."
        sleep 60
        continue
    fi

    goal="${GOALS[$((phase % total_phases))]}"
    inject "$goal"

    # Show training frame count every cycle
    birch_count=$(ls "$AKSUMAEL_DIR/data/yolo_dataset/images/train/" 2>/dev/null | grep -c "tree_autolabel_birch" || echo 0)
    log_count=$(ls "$AKSUMAEL_DIR/data/yolo_dataset/images/train/" 2>/dev/null | grep -c "tree_autolabel_log" || echo 0)
    leaves_count=$(ls "$AKSUMAEL_DIR/data/yolo_dataset/images/train/" 2>/dev/null | grep -c "tree_autolabel_leaves" || echo 0)
    total=$(ls "$AKSUMAEL_DIR/data/yolo_dataset/images/train/" 2>/dev/null | wc -l || echo 0)
    log "Training frames — birch:$birch_count log:$log_count leaves:$leaves_count | dataset total:$total"

    # Report any recent retrain
    last_train=$(python3 -c "import json; d=json.load(open('$AKSUMAEL_DIR/data/last_train.json')); import datetime; print(datetime.datetime.fromtimestamp(d['last_train']).strftime('%H:%M:%S'))" 2>/dev/null || echo "unknown")
    log "Last retrain: $last_train"

    phase=$((phase + 1))
    log "Next goal in ${PHASE_MINUTES}m: ${GOALS[$((phase % total_phases))]}"
    sleep $((PHASE_MINUTES * 60))
done
