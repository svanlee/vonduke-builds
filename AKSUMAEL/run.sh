#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL run.sh — one-shot full stack startup        ║
# ║                                                        ║
# ║  Order: kill stale procs -> mesh-llm -> AKSUMAEL      ║
# ║         -> axon voice hub -> summary                  ║
# ║                                                        ║
# ║  Health-gated: waits for mesh-llm's /v1/models to     ║
# ║  report models before continuing. Idempotent — safe   ║
# ║  to re-run while things are already up. Does NOT      ║
# ║  auto-restart anything after it exits.                ║
# ╚══════════════════════════════════════════════════════╝
set -uo pipefail

AKSUMAEL_DIR="$HOME/vonduke-builds/AKSUMAEL"
LOG_FILE="/tmp/aksumael_live.log"
MESH_LLM_URL="http://localhost:9337/v1/models"
ANTHROPIC_KEY_FILE="$HOME/.config/anthropic/key"
GOOGLE_KEY_FILE="$HOME/.config/google/key"

log() { echo "[RUN] $*"; }

# ── Step 1: kill stale processes ─────────────────────────
log "Killing any stale processes (piper, espeak, old main.py, old wrapper)..."
pkill -f "piper"                    2>/dev/null
pkill -f "espeak"                   2>/dev/null
pkill -f "$AKSUMAEL_DIR/main.py"    2>/dev/null
pkill -f "aksumael_wrapper.sh"      2>/dev/null
sleep 1
log "Stale process cleanup done."

# ── Step 2: start mesh-llm, health-gate on /v1/models ────
log "Starting mesh-llm..."
systemctl --user start mesh-llm

log "Waiting for mesh-llm to report a non-empty models list (up to 90s)..."
mesh_llm_ready=0
elapsed=0
while (( elapsed < 90 )); do
    models_json="$(curl -s --max-time 2 "$MESH_LLM_URL" 2>/dev/null)"
    if [[ -n "$models_json" ]] && echo "$models_json" | grep -q '"id"'; then
        mesh_llm_ready=1
        break
    fi
    sleep 3
    (( elapsed += 3 ))
    log "  ...still waiting on mesh-llm (${elapsed}s / 90s)"
done

if (( mesh_llm_ready == 1 )); then
    log "mesh-llm is up and serving models (${elapsed}s)."
else
    log "WARNING: mesh-llm did not report models within 90s. Proceeding anyway."
fi

# ── Step 3: start AKSUMAEL wrapper ───────────────────────
log "Loading API keys..."
if [[ -f "$ANTHROPIC_KEY_FILE" ]]; then
    export ANTHROPIC_API_KEY="$(cat "$ANTHROPIC_KEY_FILE")"
    log "  ANTHROPIC_API_KEY loaded."
else
    log "  WARNING: $ANTHROPIC_KEY_FILE not found — ANTHROPIC_API_KEY not set."
fi
if [[ -f "$GOOGLE_KEY_FILE" ]]; then
    export GEMINI_API_KEY="$(cat "$GOOGLE_KEY_FILE")"
    log "  GEMINI_API_KEY loaded."
else
    log "  WARNING: $GOOGLE_KEY_FILE not found — GEMINI_API_KEY not set."
fi

if pgrep -f "$AKSUMAEL_DIR/main.py" >/dev/null 2>&1; then
    log "AKSUMAEL main.py already running (PID $(pgrep -f "$AKSUMAEL_DIR/main.py" | tr '\n' ' ')) — skipping start."
else
    log "Starting AKSUMAEL via wrapper..."
    nohup bash "$AKSUMAEL_DIR/tools/aksumael_wrapper.sh" >> "$LOG_FILE" 2>&1 &
    disown
    sleep 5
    if pgrep -f "$AKSUMAEL_DIR/main.py" >/dev/null 2>&1; then
        log "AKSUMAEL main.py is running (PID $(pgrep -f "$AKSUMAEL_DIR/main.py" | tr '\n' ' '))."
    else
        log "WARNING: main.py not detected after 5s. Check $LOG_FILE for errors."
    fi
fi

# ── Step 4: start axon voice hub ─────────────────────────
log "Starting axon voice hub..."
systemctl --user start axon
sleep 2
if systemctl --user is-active --quiet axon; then
    log "axon is active."
else
    log "WARNING: axon is not active. Check 'systemctl --user status axon'."
fi

# ── Step 5: summary ──────────────────────────────────────
echo ""
log "===== Startup summary ====="
log "mesh-llm:  $(systemctl --user is-active mesh-llm 2>/dev/null)  (health check: $([[ $mesh_llm_ready -eq 1 ]] && echo OK || echo TIMED_OUT))"
mesh_llm_pid="$(systemctl --user show -p MainPID --value mesh-llm 2>/dev/null)"
log "  MainPID: ${mesh_llm_pid:-unknown}"

aksumael_pids="$(pgrep -f "$AKSUMAEL_DIR/main.py" | tr '\n' ' ')"
log "AKSUMAEL:  $([[ -n "$aksumael_pids" ]] && echo running || echo NOT_RUNNING)"
log "  PID(s): ${aksumael_pids:-none}"

log "axon:      $(systemctl --user is-active axon 2>/dev/null)"
axon_pid="$(systemctl --user show -p MainPID --value axon 2>/dev/null)"
log "  MainPID: ${axon_pid:-unknown}"

log "Log file:  $LOG_FILE"
log "============================"
