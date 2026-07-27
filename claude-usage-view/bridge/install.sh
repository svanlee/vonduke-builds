#!/usr/bin/env bash
# ============================================================
# Install the Claude Usage Bridge as a systemd --user service
# on the Axon rig (Linux, user 'ros').
#
# Mirrors how AKSUMAEL's axon voice hub is installed: a --user
# unit under ~/.config/systemd/user, so it runs as the login
# user and can read ~/.claude/.credentials.json.
#
# Run as the 'ros' user (NOT sudo):
#     ./install.sh
# ============================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_SRC="$HERE/claude-usage-bridge.service"
UNIT_DST_DIR="$HOME/.config/systemd/user"
UNIT_DST="$UNIT_DST_DIR/claude-usage-bridge.service"

echo "[install] target user: $(whoami)"

# --- Preflight: is Claude Code logged in on this rig? ---
CREDS="${CLAUDE_CREDENTIALS_FILE:-$HOME/.claude/.credentials.json}"
if [[ -f "$CREDS" ]]; then
  echo "[install] found Claude Code credentials at $CREDS"
elif command -v secret-tool >/dev/null 2>&1; then
  echo "[install] no $CREDS — the bridge will try libsecret (secret-tool) at runtime"
else
  echo "[install] WARNING: no $CREDS and no secret-tool." >&2
  echo "[install] Log in with 'claude' first, or the bridge will 503." >&2
fi

# --- Install the unit ---
mkdir -p "$UNIT_DST_DIR"
cp "$UNIT_SRC" "$UNIT_DST"
echo "[install] installed unit -> $UNIT_DST"

systemctl --user daemon-reload
systemctl --user enable --now claude-usage-bridge.service
echo "[install] enabled + started claude-usage-bridge.service"

# Keep the --user service alive when ros is not logged in (headless rig).
if command -v loginctl >/dev/null 2>&1; then
  loginctl enable-linger "$(whoami)" 2>/dev/null \
    && echo "[install] lingering enabled — service survives logout/reboot" \
    || echo "[install] note: could not enable lingering (run: sudo loginctl enable-linger $(whoami))"
fi

echo
echo "[install] done. Verify with:"
echo "    systemctl --user status claude-usage-bridge.service"
echo "    curl -s http://localhost:8787/usage | python3 -m json.tool"
echo
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "[install] point the ESP32 bridge URL at:  http://${IP:-<axon-rig-ip>}:8787/usage"
