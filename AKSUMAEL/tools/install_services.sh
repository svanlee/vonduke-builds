#!/usr/bin/env bash
# Install AKSUMAEL systemd user services so the bot and watchdog
# auto-start on login and restart on crash.
# Run once: bash tools/install_services.sh
set -e

AKSUMAEL_DIR="$HOME/vonduke-builds/AKSUMAEL"
SERVICE_DIR="$HOME/.config/systemd/user"

mkdir -p "$SERVICE_DIR"

# Copy service files
cp "$AKSUMAEL_DIR/tools/systemd/aksumael.service"          "$SERVICE_DIR/"
cp "$AKSUMAEL_DIR/tools/systemd/aksumael-watchdog.service" "$SERVICE_DIR/"

systemctl --user daemon-reload
systemctl --user enable aksumael.service
systemctl --user enable aksumael-watchdog.service

echo "[INSTALL] Services installed and enabled."
echo "To start now:  systemctl --user start aksumael.service aksumael-watchdog.service"
echo "To check logs: journalctl --user -u aksumael.service -f"
echo "To disable:    systemctl --user disable aksumael.service aksumael-watchdog.service"
