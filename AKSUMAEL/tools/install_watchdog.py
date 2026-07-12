"""
Installs a systemd user service that runs the AKSUMAEL wrapper.
The wrapper starts AKSUMAEL, restarts it on crash, and watches
~/vonduke-builds/AKSUMAEL/.aksumael_ctl for control commands.

Run once: python3 tools/install_watchdog.py
"""

import os
import subprocess
import textwrap
from pathlib import Path

AKSUMAEL_DIR  = Path("~/vonduke-builds/AKSUMAEL").expanduser()
WRAPPER       = AKSUMAEL_DIR / "tools/aksumael_wrapper.sh"

SERVICE = textwrap.dedent(f"""
[Unit]
Description=AKSUMAEL Minecraft AI Agent
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory={AKSUMAEL_DIR}
ExecStart=/usr/bin/bash {WRAPPER}
Restart=on-failure
RestartSec=10
StandardOutput=append:/tmp/aksumael_wrapper.log
StandardError=append:/tmp/aksumael_wrapper.log
Environment=DISPLAY=:0

[Install]
WantedBy=default.target
""").strip()


def install():
    # Make wrapper executable
    WRAPPER.chmod(0o755)
    print(f"[WATCHDOG] wrapper: {WRAPPER}")

    unit_dir  = Path("~/.config/systemd/user").expanduser()
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / "aksumael.service"
    unit_path.write_text(SERVICE)
    print(f"[WATCHDOG] wrote {unit_path}")

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "aksumael.service"], check=True)
    print("[WATCHDOG] service enabled.")
    print()
    print("To start now:   systemctl --user start aksumael")
    print("To check logs:  tail -f /tmp/aksumael_live.log")
    print("To restart:     echo restart > ~/vonduke-builds/AKSUMAEL/.aksumael_ctl")
    print("To stop:        echo stop    > ~/vonduke-builds/AKSUMAEL/.aksumael_ctl")


if __name__ == "__main__":
    install()
