#!/usr/bin/env python3
"""
auto_labeler.py — Watches the frame server and saves labeled training samples
for new/sparse classes using COLOR DETECTION (no YOLO needed for bootstrapping).

Detects:
  lava    (class 53) — orange/red glow, very distinctive
  bedrock (class 54) — dark mottled grey pattern underground
  bee     (class 55) — yellow/brown flying object (rare, low priority)
  cave    (class 56) — dark frame with some lit surfaces = underground cave

Runs independently. Start with:
  venv/bin/python3 -u tools/auto_labeler.py
"""

import os
import time
import uuid
import urllib.request

import cv2
import numpy as np

# ── paths ──────────────────────────────────────────────────────────────────
BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG_DIR   = os.path.join(BASE_DIR, "data", "yolo_dataset", "images", "train")
LBL_DIR   = os.path.join(BASE_DIR, "data", "yolo_dataset", "labels", "train")
FRAME_URL = "http://localhost:8765/frame"

# ── config ─────────────────────────────────────────────────────────────────
POLL_INTERVAL_SEC = 15    # check this often
COOLDOWN_SEC      = 45    # don't save same class more often than this
MAX_FRAMES        = 1000  # safety cap

# ── class IDs ──────────────────────────────────────────────────────────────
CLS_LAVA      = 53
CLS_BEDROCK   = 54
CLS_BEE       = 55
CLS_CAVE      = 56
CLS_IRONGOLEM = 57
CLS_CAT       = 58

# ── state ──────────────────────────────────────────────────────────────────
_last_saved: dict[int, float] = {}
_total_saved = 0


# ══════════════════════════════════════════════════════════════════════════════
#  COLOR DETECTORS
#  Each returns (detected: bool, bbox: [x1,y1,x2,y2] in pixels) or (False, None)
# ══════════════════════════════════════════════════════════════════════════════

def detect_lava(frame: np.ndarray):
    """
    Lava: bright orange/red pixels.  HSV hue 5-25, high saturation, high value.
    Excludes the HUD strip at the bottom.
    """
    h, w = frame.shape[:2]
    roi = frame[:int(h * 0.85), :]   # ignore bottom HUD

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

    # Orange-red range (lava glow)
    lo1 = np.array([0,  180, 180])
    hi1 = np.array([20, 255, 255])
    lo2 = np.array([160, 180, 180])   # wraps around red
    hi2 = np.array([180, 255, 255])

    mask = cv2.inRange(hsv, lo1, hi1) | cv2.inRange(hsv, lo2, hi2)

    # Need a decent blob, not just a torch
    total_pixels = roi.shape[0] * roi.shape[1]
    ratio = np.count_nonzero(mask) / total_pixels

    if ratio < 0.04:   # less than 4% orange = not lava
        return False, None

    # Find bounding box of the lava region
    ys, xs = np.where(mask > 0)
    x1, y1, x2, y2 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    return True, [x1, y1, x2, y2]


def detect_bedrock(frame: np.ndarray):
    """
    Bedrock: very dark, uniformly grey-black mottled texture.
    Heuristic: frame is mostly dark (underground) AND has low colour variance
    in the grey range (bedrock is grey, not brown like dirt/stone).
    """
    h, w = frame.shape[:2]
    roi = frame[:int(h * 0.85), :]

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    mean_v = float(np.mean(gray))
    std_v  = float(np.std(gray))

    # Bedrock: medium-dark, low variance (uniform texture)
    # Avoid: pure black (no light), bright scenes (surface)
    if not (30 < mean_v < 90 and std_v < 45):
        return False, None

    # Also check: very little colour saturation (grey, not dirt/stone brown)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mean_sat = float(np.mean(hsv[:, :, 1]))
    if mean_sat > 30:   # colourful = not bedrock
        return False, None

    return True, [0, 0, w - 1, int(h * 0.85) - 1]


def detect_cave(frame: np.ndarray):
    """
    Cave: mostly dark frame (underground) with some lit areas.
    High dynamic range (mix of dark and some bright spots from torches).
    Distinct from bedrock: higher variance (torch-lit walls vs uniform dark).
    """
    h, w = frame.shape[:2]
    roi = frame[:int(h * 0.85), :]

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    mean_v = float(np.mean(gray))
    std_v  = float(np.std(gray))

    # Cave: dark overall but with variance (torches/lava lit spots)
    if not (15 < mean_v < 75 and std_v > 35):
        return False, None

    # Exclude pure-bedrock frames (handled above)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mean_sat = float(np.mean(hsv[:, :, 1]))
    if mean_sat < 8:   # too grey = bedrock, not cave
        return False, None

    # Exclude lava-heavy frames (those go to lava class)
    lo = np.array([0, 180, 180])
    hi = np.array([20, 255, 255])
    lava_ratio = np.count_nonzero(cv2.inRange(hsv, lo, hi)) / (roi.shape[0] * roi.shape[1])
    if lava_ratio > 0.03:
        return False, None

    return True, [0, 0, w - 1, int(h * 0.85) - 1]


def detect_bee(frame: np.ndarray):
    """
    Bee: small yellow/amber object against sky or leaves.
    Very hard to detect reliably with colour alone — only fire when very obvious.
    """
    h, w = frame.shape[:2]
    roi = frame[:int(h * 0.85), :]

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    # Yellow-amber
    lo = np.array([18, 150, 150])
    hi = np.array([35, 255, 255])
    mask = cv2.inRange(hsv, lo, hi)

    ratio = np.count_nonzero(mask) / (roi.shape[0] * roi.shape[1])
    # Very narrow range: some yellow but not a full sunlit scene
    if not (0.005 < ratio < 0.06):
        return False, None

    ys, xs = np.where(mask > 0)
    x1, y1, x2, y2 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    return True, [x1, y1, x2, y2]


# ══════════════════════════════════════════════════════════════════════════════

def detect_iron_golem(frame: np.ndarray):
    """
    Iron golem: large light-grey body (iron colour) with distinctive brown
    vines/flowers decoration. Look for a significant blob of desaturated
    light-grey (iron) in the frame — larger than a single block.
    """
    h, w = frame.shape[:2]
    roi = frame[:int(h * 0.85), :]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    # Light-grey iron: low saturation, medium-high value
    sat_mask  = hsv[:, :, 1] < 40           # near-grey
    val_mask  = (hsv[:, :, 2] > 120) & (hsv[:, :, 2] < 210)  # not white, not dark
    iron_mask = (sat_mask & val_mask).astype(np.uint8) * 255

    ratio = np.count_nonzero(iron_mask) / (roi.shape[0] * roi.shape[1])
    # Some grey but not too much (don't confuse with stone walls)
    if not (0.02 < ratio < 0.25):
        return False, None

    # Check frame isn't mostly stone/cobblestone (golem stands out from bg)
    # Use variance of the grey mask region as a proxy for "distinct object"
    ys, xs = np.where(iron_mask > 0)
    if len(ys) < 200:
        return False, None
    x1, y1, x2, y2 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    blob_h = y2 - y1
    blob_w = x2 - x1
    # Golem is tall — expect height > width
    if blob_h < blob_w * 0.8:
        return False, None

    return True, [x1, y1, x2, y2]


def detect_cat(frame: np.ndarray):
    """
    Cat: small orange/tabby or black/white object. Minecraft cats are small
    (~20-40px tall) with orange-brown or black colouring.
    Orange tabby hue: 10-25; black: very dark; siamese: beige.
    Use orange/brown blob detection (most common cat colour).
    """
    h, w = frame.shape[:2]
    roi = frame[:int(h * 0.85), :]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

    # Orange-brown cat colour
    lo = np.array([8, 100, 80])
    hi = np.array([28, 255, 200])
    mask = cv2.inRange(hsv, lo, hi)

    ratio = np.count_nonzero(mask) / (roi.shape[0] * roi.shape[1])
    # Small object — don't need much, but need some
    if not (0.001 < ratio < 0.08):
        return False, None

    # Avoid false positives from dirt/wooden planks — check blob is compact
    ys, xs = np.where(mask > 0)
    if len(ys) < 50:
        return False, None
    x1, y1, x2, y2 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())

    return True, [x1, y1, x2, y2]


# ══════════════════════════════════════════════════════════════════════════════

DETECTORS = [
    (CLS_LAVA,      "lava",       detect_lava),
    (CLS_BEDROCK,   "bedrock",    detect_bedrock),
    (CLS_CAVE,      "cave",       detect_cave),
    (CLS_BEE,       "bee",        detect_bee),
    (CLS_IRONGOLEM, "iron_golem", detect_iron_golem),
    (CLS_CAT,       "cat",        detect_cat),
]


def fetch_frame() -> np.ndarray | None:
    try:
        with urllib.request.urlopen(FRAME_URL, timeout=3) as resp:
            data = resp.read()
        arr = np.frombuffer(data, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception as e:
        print(f"[LABELER] fetch failed: {e}")
        return None


def cooldown_ok(cls_id: int) -> bool:
    return time.time() - _last_saved.get(cls_id, 0) >= COOLDOWN_SEC


def save_detection(frame: np.ndarray, cls_id: int, name: str, bbox) -> bool:
    global _total_saved
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    cx = ((x1 + x2) / 2) / w
    cy = ((y1 + y2) / 2) / h
    bw = (x2 - x1) / w
    bh = (y2 - y1) / h

    stem     = f"autolabel_{name}_{uuid.uuid4().hex[:8]}"
    img_path = os.path.join(IMG_DIR, f"{stem}.jpg")
    lbl_path = os.path.join(LBL_DIR, f"{stem}.txt")

    try:
        cv2.imwrite(img_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        with open(lbl_path, "w") as f:
            f.write(f"{cls_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n")
        _last_saved[cls_id] = time.time()
        _total_saved += 1
        print(f"[LABELER] ★ {name} saved (total={_total_saved})")
        return True
    except Exception as e:
        print(f"[LABELER] save failed: {e}")
        return False


def run_once() -> int:
    frame = fetch_frame()
    if frame is None:
        return 0

    saved = 0
    for cls_id, name, detector in DETECTORS:
        if not cooldown_ok(cls_id):
            continue
        try:
            detected, bbox = detector(frame)
        except Exception as e:
            print(f"[LABELER] detector {name} error: {e}")
            continue
        if detected and bbox is not None:
            if save_detection(frame, cls_id, name, bbox):
                saved += 1

    return saved


def main():
    os.makedirs(IMG_DIR, exist_ok=True)
    os.makedirs(LBL_DIR, exist_ok=True)

    print(f"[LABELER] color-based detector started")
    print(f"[LABELER] watching: lava, bedrock, cave, bee, iron_golem, cat")
    print(f"[LABELER] polling every {POLL_INTERVAL_SEC}s — Ctrl+C to stop\n")

    try:
        while _total_saved < MAX_FRAMES:
            run_once()
            time.sleep(POLL_INTERVAL_SEC)
    except KeyboardInterrupt:
        pass

    print(f"\n[LABELER] done — {_total_saved} frames saved")


if __name__ == "__main__":
    main()
