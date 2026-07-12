#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL Wrapper — process manager + control file   ║
# ║                                                      ║
# ║  Runs main.py directly (not via detaching launch.py) ║
# ║  so we can track the real PID and restart cleanly.  ║
# ║                                                      ║
# ║  Watches .aksumael_ctl for commands:                 ║
# ║    restart  — kill and relaunch                      ║
# ║    stop     — kill and exit wrapper                  ║
# ╚══════════════════════════════════════════════════════╝

AKSUMAEL_DIR="$HOME/vonduke-builds/AKSUMAEL"
VENV_PYTHON="$AKSUMAEL_DIR/venv/bin/python3"
KEY_FILE="$HOME/.config/anthropic/key"
CTL_FILE="$AKSUMAEL_DIR/.aksumael_ctl"
LOG_FILE="/tmp/aksumael_live.log"

AKSUMAEL_PID=""

# Clear stale control file on startup
rm -f "$CTL_FILE"

load_key() {
    if [[ -f "$KEY_FILE" ]]; then
        KEY=$(cat "$KEY_FILE" | tr -d '[:space:]')
        if [[ -n "$KEY" ]]; then
            export ANTHROPIC_API_KEY="$KEY"
            echo "[WRAPPER] API key loaded (${#KEY} chars)"
            return 0
        fi
    fi
    echo "[WRAPPER] WARNING: API key file empty or missing: $KEY_FILE"
    return 1
}

start_aksumael() {
    load_key
    echo "[WRAPPER] Starting AKSUMAEL..."
    cd "$AKSUMAEL_DIR"
    "$VENV_PYTHON" -u main.py >> "$LOG_FILE" 2>&1 &
    AKSUMAEL_PID=$!
    echo "[WRAPPER] AKSUMAEL PID: $AKSUMAEL_PID"
}

stop_aksumael() {
    if [[ -n "$AKSUMAEL_PID" ]] && kill -0 "$AKSUMAEL_PID" 2>/dev/null; then
        echo "[WRAPPER] Stopping AKSUMAEL (PID $AKSUMAEL_PID)..."
        kill "$AKSUMAEL_PID" 2>/dev/null
        wait "$AKSUMAEL_PID" 2>/dev/null
    fi
    AKSUMAEL_PID=""
}

start_aksumael

while true; do
    sleep 2

    # Check for control file commands
    if [[ -f "$CTL_FILE" ]]; then
        CMD=$(cat "$CTL_FILE" | tr -d '[:space:]')
        rm -f "$CTL_FILE"
        echo "[WRAPPER] Got command: $CMD"

        case "$CMD" in
            restart)
                stop_aksumael
                sleep 1
                start_aksumael
                ;;
            stop)
                stop_aksumael
                echo "[WRAPPER] Stopped by control file. Exiting."
                exit 0
                ;;
            *)
                echo "[WRAPPER] Unknown command: $CMD"
                ;;
        esac
    fi

    # Restart if crashed (and not intentionally stopped)
    if [[ -n "$AKSUMAEL_PID" ]] && ! kill -0 "$AKSUMAEL_PID" 2>/dev/null; then
        echo "[WRAPPER] AKSUMAEL crashed (PID $AKSUMAEL_PID). Restarting in 5s..."
        AKSUMAEL_PID=""
        sleep 5
        start_aksumael
    fi
done
