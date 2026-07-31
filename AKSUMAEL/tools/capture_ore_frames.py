#!/usr/bin/env python3
"""
Ore frame capture tool — grabs frames from the AKSUMAEL live view server
and saves them to data/survey_frames/ for labeling by claude_autolabel.py.

Usage (run from ~/vonduke-builds/AKSUMAEL/):
    python3 tools/capture_ore_frames.py [--count N] [--interval SECS]

By default captures 200 frames at 1-second intervals.
Only keeps frames that look different from the last one (avoids duplicates
when the bot is stationary).
"""

import argparse
import hashlib
import sys
import time
import urllib.request
from pathlib import Path

LIVE_VIEW_FRAME_URL = "http://localhost:8765/frame"
AKSUMAEL_DIR = Path(__file__).resolve().parent.parent
SURVEY_DIR   = AKSUMAEL_DIR / "data" / "survey_frames"


def fetch_frame(url: str, timeout: float = 3.0) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        print(f"[CAPTURE] fetch error: {e}", file=sys.stderr)
        return None


def frame_hash(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()[:8]


def main():
    p = argparse.ArgumentParser(description="Capture ore training frames from live view")
    p.add_argument("--count",    type=int,   default=200,  help="Number of frames to capture")
    p.add_argument("--interval", type=float, default=1.0,  help="Seconds between captures")
    p.add_argument("--min-diff", type=float, default=0.0,  help="Min hash change ratio (0 = keep all)")
    args = p.parse_args()

    SURVEY_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(SURVEY_DIR.glob("ore_*.jpg"))
    print(f"[CAPTURE] survey_frames/ exists: {len(existing)} frames already saved")
    print(f"[CAPTURE] capturing {args.count} frames at {args.interval}s intervals → {SURVEY_DIR}")

    saved = 0
    last_hash = None

    for i in range(args.count):
        data = fetch_frame(LIVE_VIEW_FRAME_URL)
        if data is None:
            print(f"[CAPTURE] {i+1}/{args.count}: skipped (fetch failed)")
            time.sleep(args.interval)
            continue

        h = frame_hash(data)
        if last_hash == h:
            print(f"[CAPTURE] {i+1}/{args.count}: skipped (identical to last frame)")
            time.sleep(args.interval)
            continue

        last_hash = h
        ts = int(time.time() * 1000)
        out = SURVEY_DIR / f"ore_{ts}_{h}.jpg"
        out.write_bytes(data)
        saved += 1
        print(f"[CAPTURE] {i+1}/{args.count}: saved {out.name}  ({len(data)//1024}KB)")
        time.sleep(args.interval)

    print(f"\n[CAPTURE] done — {saved} frames saved to {SURVEY_DIR}")
    print(f"[CAPTURE] next: python3 tools/claude_autolabel.py")


if __name__ == "__main__":
    main()
