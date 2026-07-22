#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL stop.sh — one-shot full stack shutdown      ║
# ║                                                        ║
# ║  Kills: AKSUMAEL, axon, mesh-llm, piper, espeak       ║
# ║  Usage: bash ~/vonduke-builds/AKSUMAEL/stop.sh        ║
# ╚══════════════════════════════════════════════════════╝
set -uo pipefail

AKSUMAEL_DIR="$HOME/vonduke-builds/AKSUMAEL"

log() { echo "[STOP] $*"; }

log "Stopping axon voice hub..."
systemctl --user stop axon 2>/dev/null && log "  axon stopped." || log "  axon was not running."

log "Killing AKSUMAEL processes..."
pkill -f "$AKSUMAEL_DIR/main.py"      2>/dev/null && log "  main.py killed." || log "  main.py was not running."
pkill -f "aksumael_wrapper.sh"        2>/dev/null && log "  wrapper killed."  || log "  wrapper was not running."

log "Killing TTS processes..."
pkill -9 -f "piper"   2>/dev/null && log "  piper killed."  || log "  piper was not running."
pkill -9 -f "espeak"  2>/dev/null && log "  espeak killed." || log "  espeak was not running."
pkill -9 -f "tts.py"  2>/dev/null && log "  tts.py killed." || log "  tts.py was not running."

sleep 1

log "Stopping mesh-llm..."
systemctl --user stop mesh-llm 2>/dev/null && log "  mesh-llm stopped." || log "  mesh-llm was not running."

sleep 1

# Verify nothing is left
remaining=$(pgrep -f "$AKSUMAEL_DIR/main.py" 2>/dev/null | tr '\n' ' ')
if [[ -n "$remaining" ]]; then
    log "WARNING: main.py still running (PID $remaining) — force killing..."
    pkill -9 -f "$AKSUMAEL_DIR/main.py" 2>/dev/null
fi

echo ""
log "===== Shutdown summary ====="
log "AKSUMAEL: $([[ -z "$(pgrep -f "$AKSUMAEL_DIR/main.py" 2>/dev/null)" ]] && echo STOPPED || echo STILL_RUNNING)"
log "axon:     $(systemctl --user is-active axon 2>/dev/null || echo inactive)"
log "mesh-llm: $(systemctl --user is-active mesh-llm 2>/dev/null || echo inactive)"
log "============================"
