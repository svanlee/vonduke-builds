"""
core/mode.py — Hardware-aware mode detection for AKSUMAEL.

On every boot, AKSUMAEL probes its hardware and selects an operating mode.
Nothing is hardcoded. The system reads what's present and adapts.

Modes
-----
  game        Capture card + KB2040 detected → full game-agent pipeline
              (vision via capture card, HID control via KB2040)
  desktop     No game hardware → laptop camera + screen capture
              (sees the local machine, operates as desktop agent)
  headless    No camera at all → text-only, voice-in / voice-out

The mode is stored in data/mode.json and re-read by every subsystem
that cares. It also writes AKSUMAEL_MODE to the environment so child
processes can read it without importing this module.
"""

import glob
import json
import os
import pathlib
import time

BASE_DIR  = pathlib.Path(__file__).parent.parent
MODE_FILE = BASE_DIR / "data" / "mode.json"

# Device signatures for game hardware
_CAPTURE_CARD_NAMES = {"usb3.0 video", "usb video", "magewell", "rybozen",
                       "capture", "hdmi", "av to usb"}
_KB2040_VIDS        = {"0403", "239a", "2e8a"}   # FTDI, Adafruit, RP2040


# ── Hardware probes ────────────────────────────────────────────────────────────

def _video_devices() -> list[dict]:
    """Return all /dev/video* with their sysfs product name."""
    found = []
    for path in sorted(glob.glob("/dev/video*"),
                       key=lambda p: int("".join(c for c in p if c.isdigit()) or 0)):
        idx = int("".join(c for c in path if c.isdigit()) or 0)
        name = ""
        try:
            with open(f"/sys/class/video4linux/video{idx}/name") as fh:
                name = fh.read().strip().lower()
        except OSError:
            pass
        found.append({"path": path, "index": idx, "name": name})
    return found


def _serial_ports() -> list[dict]:
    """Return /dev/ttyUSB* and /dev/ttyACM* with USB vendor IDs."""
    ports = []
    for path in sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")):
        node = os.path.basename(path)
        vid  = None
        try:
            link = os.path.realpath(f"/sys/class/tty/{node}/device")
            for _ in range(6):
                vid_f = os.path.join(link, "idVendor")
                if os.path.exists(vid_f):
                    with open(vid_f) as fh:
                        vid = fh.read().strip().lower()
                    break
                link = os.path.dirname(link)
        except OSError:
            pass
        ports.append({"path": path, "vid": vid,
                      "writable": os.access(path, os.W_OK)})
    return ports


def _laptop_camera() -> str | None:
    """Return path of first built-in camera (not a capture card)."""
    for dev in _video_devices():
        name = dev["name"]
        if any(k in name for k in _CAPTURE_CARD_NAMES):
            continue          # skip capture cards
        return dev["path"]
    return None


# ── Mode detection ─────────────────────────────────────────────────────────────

def detect() -> dict:
    """Probe hardware and return a mode descriptor.

    Returns:
        {
          "mode":          "game" | "desktop" | "headless",
          "capture_card":  path or None,
          "kb2040":        path or None,
          "laptop_camera": path or None,
          "detected_at":   ISO timestamp,
          "reason":        human-readable explanation,
        }
    """
    videos  = _video_devices()
    serials = _serial_ports()

    # Capture card: any video device whose name matches known capture cards,
    # or video2+ (convention: video0/1 = built-in, video2+ = external card)
    capture_card = None
    for dev in videos:
        if any(k in dev["name"] for k in _CAPTURE_CARD_NAMES):
            capture_card = dev["path"]
            break
    if not capture_card:
        # Fall back: any video device at index >= 2 is likely an external card
        external = [d for d in videos if d["index"] >= 2]
        if external:
            capture_card = external[0]["path"]

    # KB2040: FTDI / Adafruit / RP2040 USB-serial port
    kb2040 = None
    for port in serials:
        if port["vid"] in _KB2040_VIDS and port["writable"]:
            kb2040 = port["path"]
            break

    laptop_cam = _laptop_camera()

    # ── Mode selection ──────────────────────────────────────────────────────
    if capture_card and kb2040:
        mode   = "game"
        reason = (f"capture card {capture_card} + KB2040 {kb2040} detected "
                  "→ full game-agent pipeline")
    elif laptop_cam:
        mode   = "desktop"
        reason = (f"no game hardware; laptop camera {laptop_cam} available "
                  "→ desktop self-awareness mode")
    else:
        mode   = "headless"
        reason = "no camera at all → text + voice only"

    result = {
        "mode":          mode,
        "capture_card":  capture_card,
        "kb2040":        kb2040,
        "laptop_camera": laptop_cam,
        "detected_at":   time.strftime("%Y-%m-%dT%H:%M:%S"),
        "reason":        reason,
    }
    return result


def load_or_detect(force: bool = False) -> dict:
    """Return cached mode (≤5 min old) or re-detect.

    force=True always re-probes (call after hotplug events).
    """
    if not force and MODE_FILE.exists():
        try:
            cached = json.loads(MODE_FILE.read_text())
            age = time.time() - time.mktime(
                time.strptime(cached["detected_at"], "%Y-%m-%dT%H:%M:%S"))
            if age < 300:
                return cached
        except Exception:
            pass

    result = detect()
    MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODE_FILE.write_text(json.dumps(result, indent=2))
    os.environ["AKSUMAEL_MODE"] = result["mode"]

    print(f"[MODE] {result['mode'].upper()} — {result['reason']}")
    return result


def current_mode() -> str:
    """Cheapest read — environment variable set at boot, else re-detect."""
    return os.environ.get("AKSUMAEL_MODE") or load_or_detect()["mode"]


if __name__ == "__main__":
    info = detect()
    print(json.dumps(info, indent=2))
