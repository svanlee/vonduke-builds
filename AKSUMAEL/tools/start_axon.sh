#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════╗
# ║  Start Axon — voice hub launcher                      ║
# ║  Loads the Anthropic key (for free-form command       ║
# ║  parsing) and starts axon/hub.py in the venv.         ║
# ╚══════════════════════════════════════════════════════╝

AKSUMAEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$AKSUMAEL_DIR/venv/bin/python3"
KEY_FILE="$HOME/.config/anthropic/key"

cd "$AKSUMAEL_DIR" || exit 1

if [[ -f "$KEY_FILE" ]]; then
    KEY=$(tr -d '[:space:]' < "$KEY_FILE")
    if [[ -n "$KEY" ]]; then
        export ANTHROPIC_API_KEY="$KEY"
        echo "[AXON] API key loaded (${#KEY} chars)"
    fi
else
    echo "[AXON] no key file at $KEY_FILE — free-form commands will fall back to 'unknown'"
    echo "[AXON] rule-based commands (mine diamonds, come back to base, stop, status) still work"
fi

exec "$VENV_PYTHON" -u -m axon.hub
