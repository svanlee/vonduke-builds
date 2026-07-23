#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL stop.sh — one-shot full stack shutdown      ║
# ║                                                        ║
# ║  Kills: AKSUMAEL, axon, mesh-llm, piper, espeak       ║
# ║  Usage: bash ~/vonduke-builds/AKSUMAEL/stop.sh        ║
# ╚══════════════════════════════════════════════════════╝
set -uo pipefail

AKSUMAEL_DIR="$HOME/vonduke-builds/AKSUMAEL"
# Must match run.sh's MAIN_PY_PATTERN exactly — the process is launched as
# "$AKSUMAEL_DIR/venv/bin/python3 -u main.py" (relative main.py, full venv path),
# so pkill must search for that string, not "$AKSUMAEL_DIR/main.py" which never
# appears in the cmdline.
MAIN_PY_PATTERN="$AKSUMAEL_DIR/venv/bin/python3 -u main.py"

log() { echo "[STOP] $*"; }

log "Stopping axon voice hub..."
systemctl --user stop axon 2>/dev/null && log "  axon stopped." || log "  axon was not running."

log "Killing AKSUMAEL processes..."
pkill -f "$MAIN_PY_PATTERN"       2>/dev/null && log "  main.py killed." || log "  main.py was not running."
pkill -f "aksumael_wrapper.sh"    2>/dev/null && log "  wrapper killed."  || log "  wrapper was not running."

log "Waiting for processes to exit..."
waited=0
while pgrep -f "$MAIN_PY_PATTERN" >/dev/null 2>&1 || pgrep -f "aksumael_wrapper.sh" >/dev/null 2>&1; do
    if (( waited >= 15 )); then
        log "Force-killing with -9..."
        pkill -9 -f "$MAIN_PY_PATTERN"    2>/dev/null
        pkill -9 -f "aksumael_wrapper.sh" 2>/dev/null
        sleep 1; break
    fi
    sleep 1; (( waited += 1 ))
done

log "Killing TTS processes..."
pkill -9 -f "piper"   2>/dev/null && log "  piper killed."  || log "  piper was not running."
pkill -9 -f "espeak"  2>/dev/null && log "  espeak killed." || log "  espeak was not running."
pkill -9 -f "tts.py"  2>/dev/null && log "  tts.py killed." || log "  tts.py was not running."

log "Stopping mesh-llm..."
systemctl --user stop mesh-llm 2>/dev/null && log "  mesh-llm stopped." || log "  mesh-llm was not running."

sleep 1

echo ""
log "===== Shutdown summary ====="
log "AKSUMAEL: $([[ -z "$(pgrep -f "$MAIN_PY_PATTERN" 2>/dev/null)" ]] && echo STOPPED || echo STILL_RUNNING)"
log "axon:     $(systemctl --user is-active axon 2>/dev/null || echo inactive)"
log "mesh-llm: $(systemctl --user is-active mesh-llm 2>/dev/null || echo inactive)"
log "============================"
