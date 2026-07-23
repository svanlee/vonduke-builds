#!/bin/bash
# AKSUMAEL quick restart — delegates to run.sh which kills stale processes,
# health-gates mesh-llm, loads API keys, and starts the wrapper cleanly.
# Run this on Victus: bash ~/vonduke-builds/AKSUMAEL/RESTART_NOW.sh
set -uo pipefail
cd "$HOME/vonduke-builds/AKSUMAEL"
exec bash run.sh
