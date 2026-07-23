# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Tree Auto-Labeler                         ║
# ║                                                        ║
# ║  When the color detector sees a tree label (log,       ║
# ║  leaves, birch_log) but YOLO sees nothing for that     ║
# ║  class, save the full frame + a YOLO-format .txt       ║
# ║  label file straight into yolo_dataset/train/. The     ║
# ║  color detector's bbox becomes pseudo-ground-truth.    ║
# ║                                                        ║
# ║  auto_trainer.py already watches that folder and       ║
# ║  triggers a retrain once enough new images pile up —   ║
# ║  no other wiring needed.                               ║
# ╚══════════════════════════════════════════════════════╝

from __future__ import annotations

import os
import time

import cv2

# YOLO dataset paths (mirrors tools/yolo_finetune.py's IMAGES_DIR / LABELS_DIR)
_DATASET_ROOT = "data/yolo_dataset"
_IMAGES_TRAIN = os.path.join(_DATASET_ROOT, "images", "train")
_LABELS_TRAIN = os.path.join(_DATASET_ROOT, "labels", "train")

# data.yaml class IDs for tree objects (must stay in sync with data.yaml)
_CLASS_IDS: dict[str, int] = {
    "log":       21,
    "leaves":    22,
    "birch_log": 46,
}

_TREE_LABELS = frozenset(_CLASS_IDS)

# Rate-limit: minimum seconds between saved frames per label type.
# Keeps the dataset from exploding with near-duplicate frames when the bot
# stands still looking at the same tree.
_MIN_SAVE_INTERVAL_SEC = 4.0

# Per-label last-save timestamp
_last_saved: dict[str, float] = {}


def maybe_save_tree_frame(
    frame,
    color_objects: list,
    yolo_objects: list,
) -> int:
    """Save training frames for tree labels that color detected but YOLO missed.

    Call once per tick after merge_with_yolo(), passing:
      - frame        : raw BGR frame (numpy array)
      - color_objects: detections returned by detect_ores_by_color()
      - yolo_objects : raw YOLO detections (before color merge)

    Returns the number of frames saved this call.
    """
    if frame is None:
        return 0

    # Labels that YOLO already detected this tick — no point re-labeling those
    yolo_labels = {o.get("label") for o in yolo_objects}

    # Color tree detections that YOLO missed
    candidates = [
        o for o in color_objects
        if o.get("label") in _TREE_LABELS and o.get("label") not in yolo_labels
    ]
    if not candidates:
        return 0

    os.makedirs(_IMAGES_TRAIN, exist_ok=True)
    os.makedirs(_LABELS_TRAIN, exist_ok=True)

    h, w = frame.shape[:2]
    now = time.time()
    saved = 0

    for det in candidates:
        label = det["label"]

        # Rate-limit per label type
        if now - _last_saved.get(label, 0.0) < _MIN_SAVE_INTERVAL_SEC:
            continue

        box = det.get("box")  # [x1, y1, x2, y2] in pixels
        if not box or len(box) != 4:
            continue

        x1, y1, x2, y2 = (
            max(0, int(box[0])), max(0, int(box[1])),
            min(w, int(box[2])), min(h, int(box[3])),
        )
        if x2 <= x1 or y2 <= y1:
            continue

        # Normalize to YOLO format: cx cy w h (0-1)
        cx = ((x1 + x2) / 2) / w
        cy = ((y1 + y2) / 2) / h
        bw = (x2 - x1) / w
        bh = (y2 - y1) / h
        class_id = _CLASS_IDS[label]
        yolo_line = f"{class_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n"

        stem = f"tree_autolabel_{label}_{int(now * 1000)}"
        img_path = os.path.join(_IMAGES_TRAIN, f"{stem}.jpg")
        lbl_path = os.path.join(_LABELS_TRAIN, f"{stem}.txt")

        try:
            cv2.imwrite(img_path, frame)
            with open(lbl_path, "w") as f:
                f.write(yolo_line)
        except OSError as e:
            print(f"[TREE_LABEL] write failed: {e}")
            continue

        _last_saved[label] = now
        saved += 1
        print(
            f"[TREE_LABEL] saved {label} frame → {stem}.jpg "
            f"(cls={class_id} cx={cx:.3f} cy={cy:.3f} w={bw:.3f} h={bh:.3f})"
        )

    return saved
