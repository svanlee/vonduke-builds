#!/usr/bin/env python3
"""
Color-based ore labeler for AKSUMAEL survey frames.

Uses HSV color masking to detect Minecraft ore types from their distinctive
pixel colors. No API required — works fully offline and doesn't compete for
VRAM with the running AKSUMAEL process.

Ore HSV ranges (OpenCV HSV: H=0-179, S=0-255, V=0-255):
  coal_ore     (12): dark gray/black spots — low V, low S
  redstone_ore (13): bright red/orange glowing — H≈0-10 or 170-179, high S+V
  iron_ore     (18): tan/beige spots — H≈15-25, medium S, high V
  gold_ore     (19): bright yellow — H≈20-35, high S+V
  diamond_ore  (17): cyan/teal — H≈85-105, high S+V
  lapis_ore    (20): deep blue — H≈105-130, high S, medium V
  emerald_ore  (14): bright green — H≈55-75, high S+V
  copper_ore   (15): orange — H≈8-18, medium-high S+V

Usage (run from ~/vonduke-builds/AKSUMAEL/):
    python3 tools/color_label_ores.py [--dry-run] [--min-area N]
"""

import argparse
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.class_registry import get_or_add_class

SURVEY_DIR = REPO_ROOT / "data" / "survey_frames"
IMG_OUT    = REPO_ROOT / "data" / "yolo_dataset" / "images" / "train"
LBL_OUT    = REPO_ROOT / "data" / "yolo_dataset" / "labels" / "train"

IMG_OUT.mkdir(parents=True, exist_ok=True)
LBL_OUT.mkdir(parents=True, exist_ok=True)

# ── Ore HSV color definitions ─────────────────────────────────────────────────
# Each entry: (class_name, [(h_lo, s_lo, v_lo), (h_hi, s_hi, v_hi)], ...)
# Multiple ranges per ore handle hue-wrap and texture variation.
ORE_DEFS = [
    ("redstone_ore", [
        # Bright red glow — wraps around H=0
        ((0,   160, 140), (12,  255, 255)),
        ((165, 160, 140), (179, 255, 255)),
    ]),
    ("gold_ore", [
        # Bright yellow — H≈20-35, very saturated
        ((18,  200, 160), (35,  255, 255)),
    ]),
    ("diamond_ore", [
        # Cyan/teal — empirically measured from cave frames: H≈62-82, med-high S, low-mid V
        # (diamond sparkle appears darker than expected due to cave lighting)
        ((62,  100, 55),  (83,  255, 165)),
    ]),
    ("lapis_ore", [
        # Deep blue — H≈105-125, high saturation
        ((105, 160, 60),  (128, 255, 200)),
    ]),
    # emerald_ore omitted: not placed in this cave; green HUD elements
    # (hearts, food icons, text) would produce constant false positives.
    ("iron_ore", [
        # Tan/beige — H≈12-22, low-medium saturation, bright
        ((10,  40,  160), (24,  150, 240)),
    ]),
    ("copper_ore", [
        # Orange — H≈8-17, medium-high saturation
        ((7,   140, 120), (19,  255, 230)),
    ]),
    # coal_ore omitted: dark pixels match any shadow in a cave and produce
    # too many false positives relative to the small gain — coal has low
    # economic priority vs. iron/gold/diamond/redstone anyway.
]

# Fraction of frame height to crop from bottom (HUD: hotbar, health/food bars)
# and from the top (debug/chat text overlay).
HUD_CROP_BOTTOM = 0.16
HUD_CROP_TOP    = 0.04


def mask_color(hsv: np.ndarray, ranges: list) -> np.ndarray:
    """Union mask for a list of (lo, hi) HSV range tuples."""
    combined = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for lo, hi in ranges:
        lo_arr = np.array(lo, dtype=np.uint8)
        hi_arr = np.array(hi, dtype=np.uint8)
        combined |= cv2.inRange(hsv, lo_arr, hi_arr)
    return combined


def boxes_from_mask(mask: np.ndarray, min_area: int, frame_w: int, frame_h: int,
                    merge_dist: int = 40) -> list[tuple]:
    """Return list of (cx, cy, w, h) in [0,1] from a binary mask.

    Dilates the mask to merge nearby pixels into regions, then finds contours.
    """
    if not mask.any():
        return []

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (merge_dist, merge_dist))
    dilated = cv2.dilate(mask, kernel)
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area:
            continue
        x, y, w, h = cv2.boundingRect(c)
        # clamp to frame
        x2 = min(x + w, frame_w)
        y2 = min(y + h, frame_h)
        bw = x2 - x
        bh = y2 - y
        if bw < 4 or bh < 4:
            continue
        cx = (x + bw / 2) / frame_w
        cy = (y + bh / 2) / frame_h
        nw = bw / frame_w
        nh = bh / frame_h
        boxes.append((cx, cy, nw, nh))

    return boxes


def label_frame(img_path: Path, min_area: int) -> list[dict]:
    """Return list of {class, cx, cy, w, h} dicts for one frame (YOLO fractions
    relative to the full frame dimensions, even though detection runs only on
    the cropped active region to exclude HUD elements)."""
    img = cv2.imread(str(img_path))
    if img is None:
        return []
    full_h, full_w = img.shape[:2]

    # Crop out HUD band at top and bottom before color detection
    top_px = int(full_h * HUD_CROP_TOP)
    bot_px = int(full_h * (1.0 - HUD_CROP_BOTTOM))
    roi = img[top_px:bot_px, :]
    roi_h, roi_w = roi.shape[:2]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

    results = []
    for class_name, ranges in ORE_DEFS:
        mask = mask_color(hsv, ranges)
        for (cx_roi, cy_roi, bw_roi, bh_roi) in boxes_from_mask(mask, min_area, roi_w, roi_h):
            # Re-express as fractions of the full frame
            cx = cx_roi
            cy = (top_px + cy_roi * roi_h) / full_h
            bw = bw_roi
            bh = bh_roi * roi_h / full_h
            results.append({"class": class_name, "cx": cx, "cy": cy,
                            "w": bw, "h": bh})
    return results


def write_yolo_label(labels: list[dict], out_path: Path) -> int:
    lines = []
    for obj in labels:
        cid = get_or_add_class(obj["class"], source="color_label_ores")
        if cid is None:
            continue
        lines.append(f"{cid} {obj['cx']:.6f} {obj['cy']:.6f} {obj['w']:.6f} {obj['h']:.6f}")
    out_path.write_text("\n".join(lines))
    return len(lines)


def run(dry_run=False, min_area=200):
    frames = sorted(SURVEY_DIR.glob("ore_*.jpg")) + sorted(SURVEY_DIR.glob("ore_*.png"))
    already_labeled = {p.stem for p in LBL_OUT.glob("*.txt")}
    pending = [f for f in frames if f.stem not in already_labeled]

    print(f"[COLOR_LABEL] {len(pending)} frames to label ({len(already_labeled)} already done)")
    total_boxes = 0

    for i, img_path in enumerate(pending):
        labels = label_frame(img_path, min_area)
        ore_labels = [l for l in labels if l["class"] != "coal_ore" or len(labels) == 1]
        # Deduplicate overlapping boxes by class (keep first occurrence)
        seen = set()
        deduped = []
        for l in labels:
            key = (l["class"], round(l["cx"], 1), round(l["cy"], 1))
            if key not in seen:
                seen.add(key)
                deduped.append(l)

        print(f"[COLOR_LABEL] {i+1}/{len(pending)}: {img_path.name} → {len(deduped)} objects: "
              f"{[d['class'] for d in deduped]}")

        if dry_run:
            continue

        shutil.copy(img_path, IMG_OUT / img_path.name)
        lbl_path = LBL_OUT / f"{img_path.stem}.txt"
        written = write_yolo_label(deduped, lbl_path)
        total_boxes += written

    print(f"[COLOR_LABEL] done — {total_boxes} boxes written across {len(pending)} frames")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="Preview without saving")
    p.add_argument("--min-area", type=int, default=200, help="Min pixel area for detection")
    args = p.parse_args()
    run(dry_run=args.dry_run, min_area=args.min_area)
