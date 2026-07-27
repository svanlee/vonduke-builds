#!/usr/bin/env python3
# ============================================================
# Claude Usage Bridge — Linux / Axon rig port
#
# Small local HTTP server that exposes the Claude subscription
# usage (5h session window + weekly limits) as a pre-digested
# JSON for the ESP32 "Cheap Yellow Display".
#
# This is the Linux port of the upstream macOS bridge. The only
# platform-specific piece is where the Claude Code OAuth token
# lives:
#   - macOS  : Keychain, service "Claude Code-credentials"
#   - Linux  : ~/.claude/.credentials.json  (this file)
#              or libsecret (secret-tool) if the install uses it
# Claude Code itself keeps that token refreshed; this script only
# ever reads it, never writes credentials.
#
# - Calls https://api.anthropic.com/api/oauth/usage
# - Caches the result for CACHE_TTL seconds
# - Serves GET /usage on 0.0.0.0:8787 so the ESP32 on the LAN
#   can poll it.
#
# Python 3 stdlib only (secret-tool fallback is optional and only
# used if the credentials file is absent). Run:
#     python3 claude_usage_bridge.py
# On the Axon rig it runs under systemd — see
# claude-usage-bridge.service / install.sh.
# ============================================================

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("BRIDGE_PORT", "8787"))
CACHE_TTL = 60  # seconds
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

# Where Claude Code keeps the OAuth credentials on Linux. Override with
# CLAUDE_CREDENTIALS_FILE if the rig stores them somewhere non-default.
CREDENTIALS_FILE = os.environ.get(
    "CLAUDE_CREDENTIALS_FILE",
    os.path.expanduser("~/.claude/.credentials.json"),
)
# libsecret attributes some Claude Code installs use instead of a flat file.
SECRET_TOOL_SERVICE = "Claude Code-credentials"

# ASCII only: these strings are rendered by TFT_eSPI ASCII fonts on the ESP32.
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

_lock = threading.Lock()
_cache = {"payload": None, "fetched_at": 0.0}
_last_good = {"payload": None}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _token_from_blob(blob):
    """Pull the access token out of a Claude Code credentials JSON blob."""
    creds = json.loads(blob)
    return creds["claudeAiOauth"]["accessToken"]


def read_token():
    """Read the Claude Code OAuth access token on Linux.

    Tries the on-disk credentials file first (the common case), then
    falls back to libsecret via `secret-tool` for installs that keep the
    token in the GNOME keyring instead of a flat file.
    """
    if os.path.isfile(CREDENTIALS_FILE):
        with open(CREDENTIALS_FILE, "r") as fh:
            return _token_from_blob(fh.read())

    # Fallback: libsecret / gnome-keyring (mirrors the macOS Keychain path).
    try:
        result = subprocess.run(
            ["secret-tool", "lookup", "service", SECRET_TOOL_SERVICE],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        raise RuntimeError(
            f"no credentials file at {CREDENTIALS_FILE} and secret-tool "
            "is not installed — is Claude Code logged in on this rig?"
        )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(
            f"no credentials file at {CREDENTIALS_FILE} and secret-tool "
            f"lookup failed: {result.stderr.strip() or 'empty result'}"
        )
    return _token_from_blob(result.stdout.strip())


def fetch_usage_raw(token):
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def fmt_12h(dt):
    """'3:05 PM' — 12-hour clock without the leading zero."""
    s = dt.strftime("%I:%M %p")
    return s[1:] if s.startswith("0") else s


def parse_reset(iso_str, include_day):
    """ISO-8601 UTC -> (local '3:00 PM' or 'Sun 3:00 AM', minutes until reset)."""
    if not iso_str:
        return "--:--", 0
    dt = datetime.fromisoformat(iso_str).astimezone()
    now = datetime.now(dt.tzinfo)
    minutes = max(0, int((dt - now).total_seconds() // 60))
    if include_day:
        return f"{WEEKDAYS[dt.weekday()]} {fmt_12h(dt)}", minutes
    return fmt_12h(dt), minutes


def simplify(raw):
    """Reduce the oauth/usage response to what the ESP32 renders."""
    five = raw.get("five_hour") or {}
    seven = raw.get("seven_day") or {}

    severities = {}
    opus_pct = None
    for lim in raw.get("limits") or []:
        kind = lim.get("kind")
        severities[kind] = lim.get("severity") or "normal"
        if kind == "weekly_scoped" and lim.get("percent") is not None:
            opus_pct = int(round(lim["percent"]))

    s_local, s_min = parse_reset(five.get("resets_at"), include_day=False)
    w_local, w_min = parse_reset(seven.get("resets_at"), include_day=True)

    return {
        "session_pct": int(round(five.get("utilization") or 0)),
        "session_severity": severities.get("session", "normal"),
        "session_resets_local": s_local,
        "session_resets_in_min": s_min,
        "weekly_pct": int(round(seven.get("utilization") or 0)),
        "weekly_severity": severities.get("weekly_all", "normal"),
        "weekly_resets_local": w_local,
        "weekly_resets_in_min": w_min,
        "opus_pct": opus_pct,
        "stale": False,
        "updated_at_local": fmt_12h(datetime.now().astimezone()),
    }


def get_payload():
    """Cached fetch. On failure, serve the last good data flagged stale."""
    with _lock:
        now = time.time()
        if _cache["payload"] and now - _cache["fetched_at"] < CACHE_TTL:
            return _cache["payload"]

        try:
            token = read_token()
            try:
                raw = fetch_usage_raw(token)
            except urllib.error.HTTPError as e:
                if e.code != 401:
                    raise
                # Token may have just been refreshed by Claude Code — re-read once.
                log("401 from usage API, re-reading credentials and retrying")
                raw = fetch_usage_raw(read_token())

            payload = simplify(raw)
            _cache["payload"] = payload
            _cache["fetched_at"] = now
            _last_good["payload"] = payload
            log(f"usage OK: session={payload['session_pct']}% weekly={payload['weekly_pct']}%")
            return payload
        except Exception as e:
            log(f"fetch FAILED: {e}")
            if _last_good["payload"]:
                stale = dict(_last_good["payload"])
                stale["stale"] = True
                # Brief negative cache so a dead API isn't hammered per poll.
                _cache["payload"] = stale
                _cache["fetched_at"] = now - CACHE_TTL + 15
                return stale
            raise


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.rstrip("/") not in ("/usage", ""):
            self.send_error(404)
            return
        try:
            body = json.dumps(get_payload()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode()
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        log(f"request from {self.client_address[0]}: {self.path}")


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log(f"Claude Usage Bridge (Linux) listening on http://0.0.0.0:{PORT}/usage")
    log(f"credentials source: {CREDENTIALS_FILE}"
        + ("" if os.path.isfile(CREDENTIALS_FILE) else " (missing — will try secret-tool)"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("bye")


if __name__ == "__main__":
    main()
