#!/usr/bin/env python3
"""Standalone screen audit: grab a frame, run YOLO, ask Claude vision, tail live.log.

No AKSUMAEL package imports — safe to run in isolation for diagnostics.
"""
import base64
import json
import os
import sys
import urllib.request
import urllib.error

import cv2
from ultralytics import YOLO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_PATH = os.path.join(ROOT, "data", "audit_raw.jpg")
ANNOTATED_PATH = os.path.join(ROOT, "data", "audit_annotated.jpg")
YOLO_MODEL_PATH = os.path.join(ROOT, "data", "models", "aksumael_mc.pt")
LIVE_LOG_PATH = os.path.join(ROOT, "data", "live.log")

CAMERA_INDEX = 2
CLAUDE_MODEL = "claude-sonnet-5"
ANTHROPIC_VERSION = "2023-06-01"
PROMPT = (
    "This is a live Minecraft screenshot. Describe exactly what the player "
    "is looking at: visible terrain, mobs, items, UI state (inventory "
    "open?), approximate Y-level if F3 is visible, and what action you'd "
    "recommend. Be specific and concise."
)


def capture_frame():
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not cap.isOpened():
        print(f"ERROR: could not open /dev/video{CAMERA_INDEX}", file=sys.stderr)
        sys.exit(1)

    frame = None
    for _ in range(5):
        ok, f = cap.read()
        if ok:
            frame = f
    cap.release()

    if frame is None:
        print("ERROR: failed to read any frame", file=sys.stderr)
        sys.exit(1)
    return frame


def run_yolo(frame):
    model = YOLO(YOLO_MODEL_PATH)
    results = model(frame, verbose=False)[0]
    detections = []
    for b in results.boxes:
        conf = round(float(b.conf), 2)
        box = [round(float(x), 1) for x in b.xyxy[0].tolist()]
        cls = int(b.cls)
        label = results.names[cls]
        detections.append({"label": label, "conf": conf, "box": box})
    return detections


def draw_annotations(frame, detections):
    annotated = frame.copy()
    for d in detections:
        x1, y1, x2, y2 = [int(v) for v in d["box"]]
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
        text = f'{d["label"]} {d["conf"]:.2f}'
        cv2.putText(
            annotated, text, (x1, max(0, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA,
        )
    return annotated


def ask_claude(frame):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in env", file=sys.stderr)
        return None

    b64 = base64.b64encode(
        cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])[1]
    ).decode()

    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 1024,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    }

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"ERROR: Claude API request failed: {e.code} {e.read().decode()}", file=sys.stderr)
        return None

    return "\n".join(b["text"] for b in body.get("content", []) if b.get("type") == "text")


def tail_log(path, n=15):
    if not os.path.exists(path):
        print(f"(no log at {path})")
        return
    with open(path) as f:
        lines = f.readlines()
    for line in lines[-n:]:
        print(line.rstrip())


def main():
    frame = capture_frame()
    cv2.imwrite(RAW_PATH, frame)
    print(f"Saved raw frame -> {RAW_PATH}")

    detections = run_yolo(frame)
    annotated = draw_annotations(frame, detections)
    cv2.imwrite(ANNOTATED_PATH, annotated)
    print(f"Saved annotated frame -> {ANNOTATED_PATH}")

    print("\n=== YOLO detections ===")
    if not detections:
        print("(none)")
    for d in detections:
        print(f'{d["label"]:20s} conf={d["conf"]:.2f}  box={d["box"]}')

    print("\n=== Claude vision response ===")
    claude_text = ask_claude(frame)
    if claude_text:
        print(claude_text)
    else:
        print("(no response)")

    print(f"\n=== Last 15 lines of {LIVE_LOG_PATH} ===")
    tail_log(LIVE_LOG_PATH, 15)


if __name__ == "__main__":
    main()
