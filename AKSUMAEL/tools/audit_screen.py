#!/usr/bin/env python3
"""Standalone diagnostic: capture a frame, run YOLO, ask local mesh-llm vision what's on screen."""
import base64
import json
import os
import sys
import urllib.error
import urllib.request

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
    "This is a live Minecraft screenshot. What is the player currently doing "
    "or looking at? List all visible objects, any UI elements open, the "
    "approximate Y level if F3 is visible, and what action you'd recommend "
    "next. Be specific."
)


def capture_frame():
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)

    if not cap.isOpened():
        print(f"ERROR: could not open /dev/video{CAMERA_INDEX}", file=sys.stderr)
        sys.exit(1)

    frame = None
    for _ in range(5):
        ok, frame = cap.read()
        if not ok:
            frame = None
    cap.release()

    if frame is None:
        print("ERROR: failed to read a frame", file=sys.stderr)
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

    payload = json.dumps({
        "model": "auto",
        "max_tokens": 1024,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
    }).encode()
    req = urllib.request.Request(
        MESH_LLM_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"ERROR: mesh-llm request failed: {e}", file=sys.stderr)
        return None


def main():
    frame = capture_frame()
    cv2.imwrite(RAW_PATH, frame)
    print(f"Saved raw frame: {RAW_PATH}")

    detections = run_yolo(frame)
    annotated = draw_annotations(frame, detections)
    cv2.imwrite(ANNOTATED_PATH, annotated)
    print(f"Saved annotated frame: {ANNOTATED_PATH}")

    print("\n=== YOLO detections ===")
    if detections:
        for d in detections:
            print(f'{d["label"]:<20} conf={d["conf"]:.2f}  box={d["box"]}')
    else:
        print("(none)")

    print("\n=== mesh-llm vision analysis ===")
    answer = ask_mesh_llm(frame)
    if answer:
        print(answer)

    print("\n=== data/live.log (last 20 lines) ===")
    if os.path.exists(LIVE_LOG_PATH):
        with open(LIVE_LOG_PATH, "r", errors="replace") as f:
            lines = f.readlines()
        for line in lines[-20:]:
            print(line.rstrip())
    else:
        print("(no live.log found)")


if __name__ == "__main__":
    main()
