#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL Wrapper — process manager + control file   ║
# ║                                                      ║
# ║  Runs main.py directly (not via detaching launch.py) ║
# ║  so we can track the real PID and restart cleanly.  ║
# ║                                                      ║
# ║  Watches .aksumael_ctl for commands:                 ║
# ║    restart   — kill and relaunch                     ║
# ║    reinit_hw — alias for restart (sent by the        ║
# ║                hotplug watcher when video2/ttyUSB0   ║
# ║                appear)                               ║
# ║    stop      — kill and exit wrapper                 ║
# ╚══════════════════════════════════════════════════════╝

AKSUMAEL_DIR="$HOME/vonduke-builds/AKSUMAEL"
VENV_PYTHON="$AKSUMAEL_DIR/venv/bin/python3"
KEY_FILE="$HOME/.config/anthropic/key"
GEMINI_KEY_FILE="$HOME/.config/google/key"
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

load_gemini_key() {
    if [[ -f "$GEMINI_KEY_FILE" ]]; then
        GKEY=$(cat "$GEMINI_KEY_FILE" | tr -d '[:space:]')
        if [[ -n "$GKEY" ]]; then
            export GEMINI_API_KEY="$GKEY"
            echo "[WRAPPER] Gemini API key loaded (${#GKEY} chars)"
            return 0
        fi
    fi
    echo "[WRAPPER] Gemini API key file empty or missing: $GEMINI_KEY_FILE (optional)"
    return 1
}

start_aksumael() {
    load_key
    load_gemini_key
    echo "[WRAPPER] Starting AKSUMAEL..."
    cd "$AKSUMAEL_DIR"
    export QT_LOGGING_RULES="*.debug=false;qt.qpa.*=false"
    # Use the existing DISPLAY if set; otherwise try :0 (X.Org login screen).
    # This lets cv2.imshow / LabelingUI open a real window on the Victus screen.
    export DISPLAY="${DISPLAY:-:0}"
    # cv2's bundled Qt only ships the xcb platform plugin (no libqoffscreen.so
    # in this venv) — forcing offscreen here crashes main.py on startup with
    # "no Qt platform plugin could be initialized" whenever a real DISPLAY is
    # available. Only fall back to offscreen when DISPLAY turns out to be unusable.
    if ! xdpyinfo -display "$DISPLAY" &>/dev/null; then
        echo "[WRAPPER] WARNING: DISPLAY=$DISPLAY not reachable — falling back to offscreen"
        export QT_QPA_PLATFORM=offscreen
    fi
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
            restart|reinit_hw)
                stop_aksumael
                sleep 1
                wait_for_hardware
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

    # Auto-restart on crash is DISABLED — manual control only
    if [[ -n "$AKSUMAEL_PID" ]] && ! kill -0 "$AKSUMAEL_PID" 2>/dev/null; then
        echo "[WRAPPER] AKSUMAEL exited (PID $AKSUMAEL_PID). Auto-restart disabled — restart manually."
        AKSUMAEL_PID=""
        exit 0
    fi
done
