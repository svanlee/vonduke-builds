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
CAPTURE_DEVICE="${CAPTURE_DEVICE:-/dev/video2}"
STARTUP_WAIT_SEC=120   # max seconds to wait for hardware before giving up
CRASH_RESTART_SEC=30   # seconds to wait after a crash before restarting

# Clear stale control file on startup
rm -f "$CTL_FILE"

wait_for_hardware() {
    echo "[WRAPPER] Waiting for capture card ($CAPTURE_DEVICE) and serial port (/dev/ttyUSB0)..."
    local waited=0
    while true; do
        local missing=()
        [[ ! -e "$CAPTURE_DEVICE" ]] && missing+=("$CAPTURE_DEVICE")
        [[ ! -e "/dev/ttyUSB0" ]]    && missing+=("/dev/ttyUSB0")
        if [[ ${#missing[@]} -eq 0 ]]; then
            echo "[WRAPPER] Hardware ready — capture card and serial port detected."
            return 0
        fi
        if (( waited >= STARTUP_WAIT_SEC )); then
            echo "[WRAPPER] WARNING: timed out waiting for: ${missing[*]}. Starting anyway."
            return 1
        fi
        echo "[WRAPPER] Still waiting for: ${missing[*]}  (${waited}s / ${STARTUP_WAIT_SEC}s)"
        sleep 5
        (( waited += 5 ))
    done
}

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

wait_for_hardware
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
            win_shutdown)
                echo "[WRAPPER] Sending Windows shutdown via KB2040..."
                "$VENV_PYTHON" "$AKSUMAEL_DIR/tools/win_shutdown.py"
                echo "[WRAPPER] win_shutdown done."
                ;;
            *)
                echo "[WRAPPER] Unknown command: $CMD"
                ;;
        esac
    fi

    # Restart if crashed (and not intentionally stopped)
    if [[ -n "$AKSUMAEL_PID" ]] && ! kill -0 "$AKSUMAEL_PID" 2>/dev/null; then
        echo "[WRAPPER] AKSUMAEL crashed (PID $AKSUMAEL_PID). Waiting ${CRASH_RESTART_SEC}s then checking hardware..."
        AKSUMAEL_PID=""
        sleep "$CRASH_RESTART_SEC"
        wait_for_hardware
        start_aksumael
    fi
done
