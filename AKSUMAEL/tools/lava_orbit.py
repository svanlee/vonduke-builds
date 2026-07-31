#!/usr/bin/env python3
"""
lava_orbit.py — Walk the bot around a visible lava pool, capturing frames
from multiple angles. Saves each as a labeled lava (class 53) training sample.

Run once while the bot is near lava:
  venv/bin/python3 -u tools/lava_orbit.py
"""

import os
import sys
import time
import uuid
import urllib.request

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from uart.kb2040_packer import KB2040Serial

FRAME_URL = "http://localhost:8765/frame"
BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG_DIR   = os.path.join(BASE_DIR, "data", "yolo_dataset", "images", "train")
LBL_DIR   = os.path.join(BASE_DIR, "data", "yolo_dataset", "labels", "train")
CLS_LAVA  = 53
LOOK_STEP = config.LOOK_SCAN_STEP  # px per look unit

# Each step: (action_dict, settle_ms, label_suffix)
# action_dict uses same format as ActionExecutor.execute()
ORBIT_STEPS = [
    (None,                              300,  "center"),
    ({'key': 'a'},                      500,  "strafe_left"),
    ({'look': {'dx': LOOK_STEP*3, 'dy': 0}}, 300, "look_right"),
    ({'key': 'a'},                      500,  "far_left"),
    ({'look': {'dx': -LOOK_STEP*3, 'dy': 0}}, 300, "look_left"),
    ({'key': 'd'},                      500,  "strafe_right_1"),
    ({'key': 'd'},                      500,  "strafe_right_2"),
    ({'key': 'd'},                      500,  "far_right"),
    ({'look': {'dx': LOOK_STEP*3, 'dy': 0}},  300, "far_right_look_left"),
    ({'key': 'a'},                      400,  "recenter_a"),
    ({'key': 'a'},                      400,  "recenter_b"),
    ({'key': 'w'},                      500,  "forward"),
    ({'look': {'dx': 0, 'dy': -LOOK_STEP*2}}, 300, "look_down"),
    ({'look': {'dx': 0, 'dy': LOOK_STEP*2}},  300, "look_up"),
    ({'key': 's'},                      500,  "backward"),
    ({'key': 's'},                      300,  "backward_2"),
]


def fetch_frame():
    try:
        with urllib.request.urlopen(FRAME_URL, timeout=3) as r:
            data = r.read()
        arr = np.frombuffer(data, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception as e:
        print(f"[ORBIT] fetch error: {e}")
        return None


def has_lava(frame):
    if frame is None:
        return False
    h, w = frame.shape[:2]
    roi = frame[:int(h * 0.85), :]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    lo = np.array([0, 180, 180])
    hi = np.array([20, 255, 255])
    mask = cv2.inRange(hsv, lo, hi)
    return np.count_nonzero(mask) / (roi.shape[0] * roi.shape[1]) > 0.02


def save_frame(frame, suffix):
    stem     = f"orbit_lava_{suffix}_{uuid.uuid4().hex[:6]}"
    img_path = os.path.join(IMG_DIR, f"{stem}.jpg")
    lbl_path = os.path.join(LBL_DIR, f"{stem}.txt")
    try:
        cv2.imwrite(img_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        with open(lbl_path, "w") as f:
            f.write(f"{CLS_LAVA} 0.500000 0.400000 0.700000 0.500000\n")
        print(f"[ORBIT] ★ saved {stem}.jpg")
        return True
    except Exception as e:
        print(f"[ORBIT] save error: {e}")
        return False


def main():
    os.makedirs(IMG_DIR, exist_ok=True)
    os.makedirs(LBL_DIR, exist_ok=True)

    print("[ORBIT] connecting to KB2040...")
    hid = KB2040Serial(port=config.UART_PORT)
    if not hid.is_connected:
        print("[ORBIT] KB2040 not connected — aborting")
        sys.exit(1)

    print(f"[ORBIT] starting lava orbit — {len(ORBIT_STEPS)} positions")
    saved = 0

    try:
        for action, settle_ms, label in ORBIT_STEPS:
            if action:
                hid.send_action(action, platform=config.PLATFORM_TARGET)
            time.sleep(settle_ms / 1000)

            frame = fetch_frame()
            if has_lava(frame):
                if save_frame(frame, label):
                    saved += 1
            else:
                print(f"[ORBIT] no lava visible at {label} — skipping")
    finally:
        hid.release_all()
        hid.close()

    print(f"\n[ORBIT] done — {saved}/{len(ORBIT_STEPS)} frames saved as lava")


if __name__ == "__main__":
    main()
