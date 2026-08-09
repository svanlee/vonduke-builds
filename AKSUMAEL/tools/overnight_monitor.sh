#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════╗
# ║  AKSUMAEL Overnight Monitor                                          ║
# ║                                                                      ║
# ║  Watches aksumael / mesh-llm / honcho-api every 5 minutes, tracks    ║
# ║  tick throughput, false deaths and voice utterances, and restarts    ║
# ║  ONLY aksumael if it goes down.                                      ║
# ║                                                                      ║
# ║  Start:  nohup bash tools/overnight_monitor.sh >> /tmp/overnight_monitor.log 2>&1 &
# ║  Watch:  tail -f /tmp/overnight_monitor.log                          ║
# ║  Stop:   pkill -f overnight_monitor.sh                               ║
# ║                                                                      ║
# ║  HARD RULE: mesh-llm and honcho-api are NEVER restarted here — an    ║
# ║  outage is logged and left for a human. mesh-llm owns the GPU/KV     ║
# ║  cache and a blind restart can leave VRAM wedged.                    ║
# ╚══════════════════════════════════════════════════════════════════════╝

set -uo pipefail

# ── Config ────────────────────────────────────────────────────────────
INTERVAL=300                        # seconds between checks (5 min)
LOG_FILE="/tmp/overnight_monitor.log"
LIVE_LOG="/tmp/aksumael_live.log"   # systemd appends aksumael stdout/stderr here
LOCK_FILE="/tmp/overnight_monitor.lock"
RECOVER_WAIT=30                     # seconds to wait before verifying a restart
STALL_SECS=180                      # live log untouched this long = bot not ticking
CLAUDE_PROC_MAX=3                   # more claude/agent processes than this = likely session leak
LOW_MEM_MIB=2048                    # available RAM under this = critical

SVC_BOT="aksumael"
SVC_LLM="mesh-llm"
SVC_HONCHO="honcho-api"

# Patterns, all confirmed against the live log format:
#   tick      -> "[1842] 0.04s 🔊 | yolo: ..."   (line starts with [<digits>])
#   death     -> "[RESPAWN] death detected (blank=True, health_confirms=True, ...)"
#   utterance -> "[VOICE] utterance captured (2.1s speech, ...)"
#   crash     -> "double free or corruption (out)" / wrapper's "Aborted (core dumped)"
RE_TICK='^\[[0-9]+\]'
STR_DEATH='[RESPAWN] death detected'
STR_UTTER='[VOICE] utterance captured'
RE_CRASH='double free or corruption|Aborted'

# ── Logging ───────────────────────────────────────────────────────────
# The documented launch line redirects stdout into $LOG_FILE, so we print to
# stdout and let the redirect do the writing — that keeps one copy per line.
# If stdout is a terminal (script run by hand), append to the file ourselves.
log() {
    printf '%s\n' "$*"
    if [ -t 1 ]; then printf '%s\n' "$*" >> "$LOG_FILE"; fi
}
ts()    { date '+%Y-%m-%d %H:%M:%S'; }
hhmm()  { date '+%H:%M'; }

# ── Single instance ───────────────────────────────────────────────────
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "[$(ts)] ABORT — another overnight_monitor.sh already holds $LOCK_FILE"
    exit 1
fi

# ── Service state ─────────────────────────────────────────────────────
# Prints: up | starting | down
svc_state() {
    local raw
    raw=$(systemctl --user is-active "$1" 2>/dev/null)
    case "$raw" in
        active)     echo "up" ;;
        activating|reloading) echo "starting" ;;
        *)          echo "down" ;;
    esac
}

svc_since() { systemctl --user show "$1" -p ActiveEnterTimestamp --value 2>/dev/null; }

# ── Live-log watermark ────────────────────────────────────────────────
# Tick lines carry no wall-clock timestamp and $LIVE_LOG is append-only across
# boots, so "this boot" can't be sliced by time. Instead we remember a byte
# offset and only ever scan bytes appended since the previous check. The
# watermark is seeded at the current EOF so pre-existing history (including old
# crashes from earlier boots) is not re-reported on startup.
OFFSET=0
CHUNK="$(mktemp /tmp/overnight_monitor_chunk.XXXXXX)"
trap 'rm -f "$CHUNK"; log "[$(ts)] monitor stopping (signal received)"; exit 0' INT TERM
trap 'rm -f "$CHUNK"' EXIT

seed_offset() {
    if [ -r "$LIVE_LOG" ]; then
        OFFSET=$(stat -c %s "$LIVE_LOG" 2>/dev/null || echo 0)
    else
        OFFSET=0
    fi
}

# Fills $CHUNK with bytes appended since the last call. Echoes the byte count.
read_new_bytes() {
    : > "$CHUNK"
    [ -r "$LIVE_LOG" ] || { echo 0; return; }

    local size
    size=$(stat -c %s "$LIVE_LOG" 2>/dev/null || echo 0)

    # Truncated or rotated out from under us — restart from the beginning.
    if [ "$size" -lt "$OFFSET" ]; then
        log "[$(ts)] NOTE $LIVE_LOG shrank ($OFFSET -> $size bytes) — log rotated or truncated, resetting watermark"
        OFFSET=0
    fi

    local n=$(( size - OFFSET ))
    if [ "$n" -gt 0 ]; then
        tail -c "+$(( OFFSET + 1 ))" "$LIVE_LOG" 2>/dev/null | head -c "$n" > "$CHUNK"
    fi
    OFFSET="$size"
    echo "$n"
}

log_age_secs() {
    [ -r "$LIVE_LOG" ] || { echo -1; return; }
    local m now
    m=$(stat -c %Y "$LIVE_LOG" 2>/dev/null || echo 0)
    now=$(date +%s)
    echo $(( now - m ))
}

# ── Host resources — LOG ONLY, nothing is ever killed here ─────────────
# 26 leaked Claude agent CLI sessions (~2.78 GB) OOM-killed the box overnight on
# 2026-08-09. This surfaces the same shape early. It deliberately does NOT kill:
# a leaked session and a legitimate one are indistinguishable from the outside.
resource_check() {
    local procs mem_mib

    # `pgrep -c` prints "0" and STILL exits 1 when nothing matches, so the `||`
    # fallback appends a second line — keep the first one only. Guard against a
    # non-numeric result too, since an unreadable /proc would break the compare.
    procs=$(pgrep -c -f "claude" 2>/dev/null || echo 0)
    procs=${procs%%$'\n'*}
    case "$procs" in ''|*[!0-9]*) procs=0 ;; esac

    if [ "$procs" -gt "$CLAUDE_PROC_MAX" ]; then
        log "[$(ts)] [MONITOR] WARNING: $procs claude/agent processes running — possible session leak"
    fi

    # free -m column 7 is "available" (reclaimable cache included), which is the
    # number that actually predicts an OOM — not "free".
    mem_mib=$(free -m 2>/dev/null | awk 'NR==2 {print $7}')
    case "$mem_mib" in ''|*[!0-9]*) mem_mib="" ;; esac

    if [ -z "$mem_mib" ]; then
        log "[$(ts)] WARN could not read available memory from free(1) — skipping memory check"
    elif [ "$mem_mib" -lt "$LOW_MEM_MIB" ]; then
        log "[$(ts)] [MONITOR] CRITICAL: low memory — $(awk -v m="$mem_mib" 'BEGIN{printf "%.1f", m/1024}')g available"
    fi
}

# ── aksumael recovery — the ONLY service this script ever starts ───────
recover_aksumael() {
    local state="$1"
    log "[$(ts)] ALERT aksumael is DOWN (systemd state: $(systemctl --user is-active $SVC_BOT 2>/dev/null || echo unknown)) — starting recovery"

    # Restart=on-failure + StartLimitBurst=5/300s means a crash loop parks the
    # unit in "failed" and plain `start` is refused until the counter is cleared.
    if systemctl --user is-failed --quiet "$SVC_BOT" 2>/dev/null; then
        log "[$(ts)] aksumael is in failed state (start limit likely hit) — clearing with reset-failed"
        systemctl --user reset-failed "$SVC_BOT" 2>&1 | while read -r l; do log "[$(ts)]   reset-failed: $l"; done
    fi

    log "[$(ts)] running: systemctl --user start $SVC_BOT"
    systemctl --user start "$SVC_BOT" 2>&1 | while read -r l; do log "[$(ts)]   start: $l"; done

    log "[$(ts)] waiting ${RECOVER_WAIT}s to verify..."
    sleep "$RECOVER_WAIT"

    local after
    after=$(svc_state "$SVC_BOT")
    if [ "$after" = "up" ]; then
        log "[$(ts)] RECOVERED aksumael is active again (since $(svc_since $SVC_BOT))"
        # A fresh process rewinds the tick counter; re-seed so the next interval
        # measures the new process instead of reporting a bogus tick delta.
        seed_offset
    else
        log "[$(ts)] RECOVERY FAILED aksumael state=$after after ${RECOVER_WAIT}s — NEEDS A HUMAN"
        log "[$(ts)] --- last 15 lines of $LIVE_LOG ---"
        tail -n 15 "$LIVE_LOG" 2>/dev/null | while IFS= read -r l; do log "[$(ts)]   | $l"; done
        log "[$(ts)] --- systemctl status ---"
        systemctl --user status "$SVC_BOT" --no-pager -n 5 2>&1 | while IFS= read -r l; do log "[$(ts)]   | $l"; done
    fi
}

# ── Startup banner ────────────────────────────────────────────────────
seed_offset
TOTAL_TICKS=0; TOTAL_DEATHS=0; TOTAL_UTTER=0; TOTAL_CRASHES=0; TOTAL_RECOVERIES=0
CHECK=0
START_TS=$(ts)

log ""
log "════════════════════════════════════════════════════════════════════"
log "[$START_TS] AKSUMAEL overnight monitor started (pid $$)"
log "[$START_TS] interval=${INTERVAL}s  live_log=$LIVE_LOG  watermark=${OFFSET}B  stall_threshold=${STALL_SECS}s"
log "[$START_TS] watching: $SVC_BOT (auto-restart ON) | $SVC_LLM (log only) | $SVC_HONCHO (log only)"
log "[$START_TS] resource thresholds: >${CLAUDE_PROC_MAX} claude procs = leak warning | <${LOW_MEM_MIB}MiB available = critical (log only, nothing is killed)"
log "[$START_TS] baseline: aksumael=$(svc_state $SVC_BOT) since $(svc_since $SVC_BOT)"
log "[$START_TS] baseline: mesh-llm=$(svc_state $SVC_LLM) since $(svc_since $SVC_LLM)"
log "[$START_TS] baseline: honcho-api=$(svc_state $SVC_HONCHO) since $(svc_since $SVC_HONCHO)"
log "════════════════════════════════════════════════════════════════════"

# ── Main loop ─────────────────────────────────────────────────────────
while true; do
    CHECK=$(( CHECK + 1 ))

    bot_state=$(svc_state "$SVC_BOT")
    llm_state=$(svc_state "$SVC_LLM")
    hon_state=$(svc_state "$SVC_HONCHO")

    # --- metrics from bytes appended since the previous check ---
    new_bytes=$(read_new_bytes)
    # -E is required, not cosmetic: with BRE the '+' in the tick pattern and the
    # '|' in the crash pattern are literals, and both counts silently stay 0.
    ticks=$(grep -acE -- "$RE_TICK" "$CHUNK" 2>/dev/null) || ticks=0
    deaths=$(grep -acF -- "$STR_DEATH" "$CHUNK" 2>/dev/null) || deaths=0
    utter=$(grep -acF -- "$STR_UTTER" "$CHUNK" 2>/dev/null) || utter=0
    crashes=$(grep -acE -- "$RE_CRASH" "$CHUNK" 2>/dev/null) || crashes=0

    TOTAL_TICKS=$(( TOTAL_TICKS + ticks ))
    TOTAL_DEATHS=$(( TOTAL_DEATHS + deaths ))
    TOTAL_UTTER=$(( TOTAL_UTTER + utter ))
    TOTAL_CRASHES=$(( TOTAL_CRASHES + crashes ))

    # --- summary line (required format) ---
    log "[$(hhmm)] aksumael=$bot_state mesh-llm=$llm_state honcho=$hon_state ticks=$ticks deaths=$deaths utterances=$utter"

    # --- degradation signals, logged but never acted on ---
    if [ "$crashes" -gt 0 ]; then
        log "[$(ts)] CRASH SIGNATURE $crashes matching line(s) since last check (double free / Aborted):"
        grep -aE -- "$RE_CRASH" "$CHUNK" 2>/dev/null | tail -n 5 | while IFS= read -r l; do log "[$(ts)]   | $l"; done
    fi

    age=$(log_age_secs)
    if [ "$bot_state" = "up" ]; then
        if [ "$age" -lt 0 ]; then
            log "[$(ts)] WARN $LIVE_LOG is unreadable or missing — cannot confirm the bot is ticking"
        elif [ "$age" -gt "$STALL_SECS" ]; then
            log "[$(ts)] WARN NOT TICKING — service is active but $LIVE_LOG has not been written for ${age}s (no restart per standing rule)"
        elif [ "$ticks" -eq 0 ] && [ "$CHECK" -gt 1 ]; then
            # Check #1 is always 0 — the watermark is seeded at EOF with no
            # elapsed interval behind it, so there is nothing to count yet.
            log "[$(ts)] WARN 0 ticks this interval (log written ${age}s ago, ${new_bytes}B appended) — bot may be blocked in a long action or human-assist gate"
        fi
    fi

    if [ "$deaths" -gt 0 ]; then
        log "[$(ts)] NOTE $deaths death/respawn event(s) this interval (total $TOTAL_DEATHS) — check for false-death GUI misreads"
    fi

    # --- host resources: LOG ONLY, never kill ---
    resource_check

    # --- mesh-llm / honcho: LOG ONLY, never restart ---
    if [ "$llm_state" != "up" ]; then
        log "[$(ts)] ALERT mesh-llm is $llm_state — NOT restarting (standing rule). Vision + LLM calls will fail until a human intervenes."
        systemctl --user status "$SVC_LLM" --no-pager -n 3 2>&1 | while IFS= read -r l; do log "[$(ts)]   | $l"; done
    fi

    if [ "$hon_state" != "up" ]; then
        log "[$(ts)] ALERT honcho-api is $hon_state — NOT restarting (log only). Memory/deriver writes will fail until a human intervenes."
    fi

    # --- aksumael: the one service we recover ---
    if [ "$bot_state" = "down" ]; then
        TOTAL_RECOVERIES=$(( TOTAL_RECOVERIES + 1 ))
        recover_aksumael "$bot_state"
    elif [ "$bot_state" = "starting" ]; then
        log "[$(ts)] NOTE aksumael is still activating — leaving it alone this cycle"
    fi

    # --- hourly cumulative roll-up ---
    if [ $(( CHECK % 12 )) -eq 0 ]; then
        log "[$(ts)] ROLLUP after $CHECK checks (~$(( CHECK * INTERVAL / 60 ))m): ticks=$TOTAL_TICKS deaths=$TOTAL_DEATHS utterances=$TOTAL_UTTER crash_lines=$TOTAL_CRASHES recoveries=$TOTAL_RECOVERIES"
    fi

    sleep "$INTERVAL"
done
