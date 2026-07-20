# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Axon Mode Switcher                 ║
# ╚══════════════════════════════════════════════════════╝
#
# Terminal helper for switching axon/hub.py's listening mode without
# restarting the process. Writes straight to data/axon_mode.txt, which
# AxonHub.run() polls every MODE_POLL_SEC seconds and picks up live.
#
#   python axon/set_mode.py ptt
#   python axon/set_mode.py always_on
#   python axon/set_mode.py off

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODE_FILE_PATH = os.path.join(BASE_DIR, "data", "axon_mode.txt")
VALID_MODES = ("ptt", "always_on", "off")


def main():
    if len(sys.argv) != 2 or sys.argv[1].lower() not in VALID_MODES:
        print(f"usage: python {os.path.basename(__file__)} {{{'|'.join(VALID_MODES)}}}")
        sys.exit(1)

    mode = sys.argv[1].lower()
    os.makedirs(os.path.dirname(MODE_FILE_PATH), exist_ok=True)
    with open(MODE_FILE_PATH, "w") as f:
        f.write(mode)
    print(f"[AXON] mode set to: {mode}")


if __name__ == "__main__":
    main()
