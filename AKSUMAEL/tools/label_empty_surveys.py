#!/usr/bin/env python3
"""
Fill empty YOLO survey label files using Claude vision API.
Run from ~/vonduke-builds/AKSUMAEL/:
    python3 tools/label_empty_surveys.py

Only touches label files that are currently 0 bytes (never re-labels filled files).
"""

import base64
import glob
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────
AKSUMAEL_DIR = Path(__file__).parent.parent.resolve()
DATASET_ROOT  = AKSUMAEL_DIR / "data" / "yolo_dataset"
KEY_FILE      = Path.home() / ".config" / "anthropic" / "key"
CLAUDE_MODEL  = "claude-sonnet-5"

# ── 44 classes ─────────────────────────────────────────────────
CLASSES = {
    0: "health_bar", 1: "hunger_bar", 2: "armor_bar", 3: "xp_bar",
    4: "hotbar", 5: "crosshair", 6: "fire_hazard", 7: "chest_row",
    8: "bed", 9: "torch", 10: "furnace", 11: "water", 12: "coal_ore",
    13: "redstone_ore", 14: "emerald_ore", 15: "copper_ore", 16: "creeper",
    17: "diamond_ore", 18: "iron_ore", 19: "gold_ore", 20: "lapis_ore",
    21: "log", 22: "leaves", 23: "zombie", 24: "skeleton", 25: "spider",
    26: "crafting_table", 27: "grass", 28: "cow", 29: "sheep", 30: "pig",
    31: "chicken", 32: "fish", 33: "fishing_bobber", 34: "wheat",
    35: "carrot", 36: "potato", 37: "sugar_cane", 38: "pumpkin",
    39: "melon", 40: "villager", 41: "sheep_wool", 42: "iron_ingot",
    43: "diamond",
}

CLASS_LIST_STR = "\n".join(f"{i}: {name}" for i, name in CLASSES.items())

# HUD bars are burned into a fixed screen position by the game itself —
# bottom band of the frame — so a claimed detection of one of these classes
# outside that band is a hallucination, not a real box. See 2026-07-19:
# this script placed hotbar/health_bar/etc. boxes near the top-left of
# frames (and even on a non-Minecraft Windows dialog screenshot), which
# then fed straight into the training set with no review step.
_HUD_BAR_CLASS_IDS = {0, 1, 2, 3, 4}   # health_bar, hunger_bar, armor_bar, xp_bar, hotbar
_HUD_BAR_Y_RANGE    = (0.75, 1.0)

SYSTEM = f"""You are labeling Minecraft screenshots for YOLO object detection training.
Image resolution: 640 × 360 pixels.

Known class IDs:
{CLASS_LIST_STR}

For each visible object output one line in YOLO format:
  class_id cx cy w h
Where cx, cy, w, h are all normalized [0, 1] to the image dimensions.
cx/cy = bounding box center. w/h = bounding box size.

HUD elements are almost always present in Minecraft screenshots:
- hotbar (class 4): horizontal bar of 9 slots at the bottom center
- health_bar (class 0): red hearts row at bottom-left
- hunger_bar (class 1): drumstick icons at bottom-right
- xp_bar (class 3): green/yellow bar just above the hotbar
- crosshair (class 5): small + symbol at the exact center of screen

Only annotate objects you are confident you see. Do not double-annotate.
Output ONLY the annotation lines — no explanation, no markdown, no JSON.
If truly no objects are visible, output nothing (empty response is fine)."""


def load_key() -> str:
    if not KEY_FILE.exists():
        sys.exit(f"[ERROR] API key not found at {KEY_FILE}")
    return KEY_FILE.read_text().strip()


def img_to_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def call_claude(api_key: str, img_path: Path) -> str:
    b64 = img_to_b64(img_path)
    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 512,
        "system": SYSTEM,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": b64,
                }},
                {"type": "text",
                 "text": "Annotate all visible objects in this Minecraft screenshot."},
            ],
        }],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read())
            return data["content"][0]["text"].strip()
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            print(f"    HTTP {e.code}: {body[:150]}")
            if e.code not in (429, 500, 502, 503, 529):
                return ""
        except Exception as exc:
            print(f"    error: {exc}")
        if attempt < 2:
            time.sleep(2 ** attempt)
    return ""


def validate(line: str):
    """Return cleaned YOLO line or None."""
    parts = line.strip().split()
    if len(parts) != 5:
        return None
    try:
        cid = int(parts[0])
        vals = [float(x) for x in parts[1:]]
    except ValueError:
        return None
    if cid not in CLASSES:
        return None
    if not all(0.0 <= v <= 1.0 for v in vals):
        return None
    if cid in _HUD_BAR_CLASS_IDS:
        cy = vals[1]
        if not (_HUD_BAR_Y_RANGE[0] <= cy <= _HUD_BAR_Y_RANGE[1]):
            return None
    return f"{cid} {vals[0]:.6f} {vals[1]:.6f} {vals[2]:.6f} {vals[3]:.6f}"


def label_one(api_key: str, img_path: Path, label_path: Path) -> int:
    print(f"  {img_path.name} ... ", end="", flush=True)
    raw = call_claude(api_key, img_path)
    lines = []
    for line in raw.splitlines():
        cleaned = validate(line)
        if cleaned:
            lines.append(cleaned)
        elif line.strip():
            print(f"\n    [skip invalid] {line!r}", end="")
    label_path.write_text("\n".join(lines) + ("\n" if lines else ""))
    print(f"{len(lines)} boxes")
    return len(lines)


def main():
    api_key = load_key()
    print(f"[LABELER] key loaded ({len(api_key)} chars)")

    total_images = 0
    total_boxes  = 0

    for split in ("train", "val"):
        lbl_dir = DATASET_ROOT / "labels" / split
        img_dir = DATASET_ROOT / "images" / split
        empty = sorted(
            p for p in lbl_dir.glob("survey_*.txt") if p.stat().st_size == 0
        )
        if not empty:
            print(f"[{split}] nothing to label")
            continue
        print(f"[{split}] {len(empty)} empty survey labels:")
        for lbl_path in empty:
            img_path = img_dir / (lbl_path.stem + ".jpg")
            if not img_path.exists():
                print(f"  [missing image] {lbl_path.stem}")
                continue
            boxes = label_one(api_key, img_path, lbl_path)
            total_images += 1
            total_boxes  += boxes
            time.sleep(0.4)   # gentle rate-limit

    print(f"\n[DONE] {total_images} images labeled, {total_boxes} total boxes.")


if __name__ == "__main__":
    main()
