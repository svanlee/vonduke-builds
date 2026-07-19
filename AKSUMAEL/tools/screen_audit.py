#!/usr/bin/env python3
"""Standalone screen audit: grab a frame, run YOLO, ask local mesh-llm vision, tail live.log.

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
MESH_LLM_URL = "http://localhost:9337/v1/chat/completions"
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


def ask_mesh_llm(frame):
    b64 = base64.b64encode(
        cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])[1]
    ).decode()

    payload = {
        "model": "auto",
        "max_tokens": 1024,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
    }

    req = urllib.request.Request(
        MESH_LLM_URL,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"ERROR: mesh-llm request failed: {e.code} {e.read().decode()}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"ERROR: mesh-llm request failed: {e}", file=sys.stderr)
        return None

    return body["choices"][0]["message"]["content"].strip()


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

    print("\n=== mesh-llm vision response ===")
    mesh_text = ask_mesh_llm(frame)
    if mesh_text:
        print(mesh_text)
    else:
        print("(no response)")

    print(f"\n=== Last 15 lines of {LIVE_LOG_PATH} ===")
    tail_log(LIVE_LOG_PATH, 15)


if __name__ == "__main__":
    main()
