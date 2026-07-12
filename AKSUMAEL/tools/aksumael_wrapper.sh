#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL Wrapper — process manager + control file   ║
# ║                                                      ║
# ║  Starts AKSUMAEL and watches .aksumael_ctl for:      ║
# ║    restart  — kill and relaunch                      ║
# ║    stop     — kill and exit wrapper                  ║
# ║                                                      ║
# ║  Cowork sandbox writes to .aksumael_ctl to control   ║
# ║  the process without needing PID namespace access.   ║
# ╚══════════════════════════════════════════════════════╝

AKSUMAEL_DIR="$HOME/vonduke-builds/AKSUMAEL"
VENV_PYTHON="$AKSUMAEL_DIR/venv/bin/python3"
LAUNCH_SCRIPT="$AKSUMAEL_DIR/tools/launch.py"
CTL_FILE="$AKSUMAEL_DIR/.aksumael_ctl"
LOG_FILE="/tmp/aksumael_live.log"

# Clear any stale control file on startup
rm -f "$CTL_FILE"

start_aksumael() {
    echo "[WRAPPER] Starting AKSUMAEL..."
    cd "$AKSUMAEL_DIR"
    "$VENV_PYTHON" "$LAUNCH_SCRIPT" >> "$LOG_FILE" 2>&1 &
    AKSUMAEL_PID=$!
    echo "[WRAPPER] AKSUMAEL PID: $AKSUMAEL_PID"
}

stop_aksumael() {
    if [[ -n "$AKSUMAEL_PID" ]] && kill -0 "$AKSUMAEL_PID" 2>/dev/null; then
        echo "[WRAPPER] Stopping AKSUMAEL (PID $AKSUMAEL_PID)..."
        kill "$AKSUMAEL_PID" 2>/dev/null
        wait "$AKSUMAEL_PID" 2>/dev/null
        AKSUMAEL_PID=""
    fi
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
        sleep 5
        start_aksumael
    fi
done
