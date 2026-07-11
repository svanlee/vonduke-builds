"""
Installs a systemd user service that watches AKSUMAEL and restarts it on crash.
Run once: python3 tools/install_watchdog.py
"""

import os, subprocess, textwrap
from pathlib import Path

AKSUMAEL_DIR = Path("~/vonduke-builds/AKSUMAEL").expanduser()
VENV_PYTHON = AKSUMAEL_DIR / "venv/bin/python3"
LAUNCH_SCRIPT = AKSUMAEL_DIR / "tools/launch.py"

SERVICE = textwrap.dedent(f"""
[Unit]
Description=AKSUMAEL Minecraft AI Agent
After=network.target

[Service]
Type=simple
WorkingDirectory={AKSUMAEL_DIR}
ExecStart={VENV_PYTHON} {LAUNCH_SCRIPT}
Restart=on-failure
RestartSec=10
StandardOutput=append:/tmp/aksumael_live.log
StandardError=append:/tmp/aksumael_live.log
Environment=DISPLAY=:0

[Install]
WantedBy=default.target
""").strip()

def install():
    unit_dir = Path("~/.config/systemd/user").expanduser()
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / "aksumael.service"
    unit_path.write_text(SERVICE)
    print(f"[WATCHDOG] wrote {unit_path}")

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "aksumael.service"], check=True)
    print("[WATCHDOG] service enabled. To start: systemctl --user start aksumael")
    print("[WATCHDOG] To check status: systemctl --user status aksumael")
    print("[WATCHDOG] Logs: journalctl --user -u aksumael -f")

if __name__ == "__main__":
    install()
