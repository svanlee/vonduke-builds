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

# ── SIGTERM handler ────────────────────────────────────────────────────────────
# systemd sends SIGTERM to this bash process on `systemctl stop aksumael`.
# Without a trap, bash exits immediately and the Python child (AKSUMAEL_PID)
# never receives SIGTERM, so its finally-block cleanup (goals.save, world.save,
# executor.close, etc.) is skipped and systemd eventually SIGKILLs the orphan.
#
# With this trap, SIGTERM is forwarded to the Python process, which converts it
# to KeyboardInterrupt (core/runtime.py line ~252), runs the finally block
# (≤10s including TTS + saves), and exits cleanly before bash returns.
_on_sigterm() {
    echo "[WRAPPER] SIGTERM received — forwarding to AKSUMAEL (PID $AKSUMAEL_PID)..."
    if [[ -n "$AKSUMAEL_PID" ]] && kill -0 "$AKSUMAEL_PID" 2>/dev/null; then
        kill -TERM "$AKSUMAEL_PID"
        wait "$AKSUMAEL_PID" 2>/dev/null
        echo "[WRAPPER] AKSUMAEL exited cleanly."
    fi
    exit 0
}
trap '_on_sigterm' TERM

# Any of these being present counts as "a camera is here" — main.py probes
# config.CAMERA_INDEX then config.CAMERA_FALLBACK_INDICES and uses whichever
# works, so pinning the wrapper to the capture card alone made it wait out
# the full timeout on every start once that card disappeared (2026-08-08).
# CAPTURE_DEVICE (single path) still overrides, for pinning to one device.
CAPTURE_DEVICE="${CAPTURE_DEVICE:-}"
# The point of this wait is the boot-time race where USB devices enumerate a
# few seconds after the service starts — 30s covers that generously. It is
# NOT a way to wait out genuinely absent hardware: main.py now starts fine
# without a camera (vision-less mode) and re-probes in the background, so
# stalling longer just delays the bot for no benefit.
STARTUP_WAIT_SEC=30
CRASH_RESTART_SEC=30   # seconds to wait after a crash before restarting

# Clear stale control file on startup
rm -f "$CTL_FILE"

# Camera device nodes main.py is willing to use, in preference order.
camera_candidates() {
    if [[ -n "$CAPTURE_DEVICE" ]]; then
        echo "$CAPTURE_DEVICE"
        return
    fi
    python3 -c "
import sys; sys.path.insert(0, '$AKSUMAEL_DIR')
import config
seen = []
for i in [config.CAMERA_INDEX] + list(getattr(config, 'CAMERA_FALLBACK_INDICES', []) or []):
    if i is not None and i >= 0 and i not in seen:
        seen.append(i)
print(' '.join(f'/dev/video{i}' for i in seen))
" 2>/dev/null || echo "/dev/video2"
}

# First candidate that exists, or empty.
present_camera() {
    local dev
    for dev in $(camera_candidates); do
        [[ -e "$dev" ]] && { echo "$dev"; return; }
    done
}

wait_for_hardware() {
    local uart_port; uart_port=$(python3 -c "import sys; sys.path.insert(0,'$AKSUMAEL_DIR'); import config; print(config.UART_PORT)" 2>/dev/null || echo "/dev/ttyUSB0")
    local cams; cams=$(camera_candidates)
    echo "[WRAPPER] Waiting for a camera (any of: $cams) and serial port ($uart_port)..."
    local waited=0
    while true; do
        local missing=()
        local cam; cam=$(present_camera)
        [[ -z "$cam" ]]          && missing+=("camera[$cams]")
        [[ ! -e "$uart_port" ]]  && missing+=("$uart_port")
        if [[ ${#missing[@]} -eq 0 ]]; then
            echo "[WRAPPER] Hardware ready — camera $cam and serial port detected."
            return 0
        fi
        if (( waited >= STARTUP_WAIT_SEC )); then
            # Not fatal for either device: main.py runs vision-less without a
            # camera and falls back to print-mode actions without the UART,
            # and both are re-probed once it's up.
            echo "[WRAPPER] timed out waiting for: ${missing[*]} — starting anyway (AKSUMAEL degrades gracefully)."
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
    # Make CUDA errors synchronous so they become catchable Python exceptions
    # instead of C++ std::terminate() that kills the whole process.
    export CUDA_LAUNCH_BLOCKING=1
    export PYTORCH_CUDA_ALLOC_CONF=garbage_collection_threshold:0.6
    # VAD energy threshold override: config.py has 0.08 (tuned for the Rybozen
    # line-in, which is loud). The laptop's built-in DMIC is much quieter;
    # 0.020 catches normal speech without false-triggering on ambient noise.
    # Raise this if the mic keeps triggering on background sounds.
    # Mic at 66%. Quiet room ambient ~0.065; threshold below speech (~0.08+).
    export VOICE_VAD_ENERGY_THRESHOLD=0.070
    # PIPEWIRE_NODE was previously set to 'USB3.0 Video Analog Stereo' (Rybozen
    # capture card) but that device is not always connected — PortAudio crashes
    # with PaUnixThread_Terminate when the node is missing. Leave unset so
    # PipeWire/PortAudio use whatever audio source is available (laptop DMIC etc).
    # Re-enable and point to the correct node name if/when game audio capture
    # from an external card is needed again.
    # export PIPEWIRE_NODE='USB3.0 Video Analog Stereo'
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
