#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Auto-Labeler                       ║
# ║  Auto-label survey frames using local mesh-llm        ║
# ║  vision, open-vocabulary — can name a class that      ║
# ║  doesn't exist yet, not just pick from the known list.║
# ╚══════════════════════════════════════════════════════╝
#
# Produces YOLO-format label files in data/yolo_dataset/labels/train/
# and copies images to data/yolo_dataset/images/train/
#
# Previously this script: (1) called api.anthropic.com directly with its
# own hardcoded key file, bypassing config.py/core/llm_router.py entirely
# — the one call site in the whole codebase that did; (2) imported a
# stale, hardcoded 43-class list from tools/yolo_finetune.py (the real,
# deployed data.yaml had already grown to 53) and forced Claude to pick a
# class_id from that fixed list, so a genuinely novel object (anything
# from the Nether/End, or just anything added to data.yaml since this
# list was last hand-updated) had no way to become a new trainable class
# — it either got silently dropped or misassigned to the nearest existing
# label. Both fixed: routed through core.llm_router.try_claude() for
# shared retry/backoff/key handling, and the prompt now asks for class
# *names* (resolved through core.class_registry.get_or_add_class, which
# creates a new class automatically the first time a name is seen)
# instead of forcing a match against a fixed snapshot.

import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.class_registry import load_classes, get_or_add_class, normalize_class_name
from core.llm_router import try_claude

SURVEY_DIR = Path("data/survey_frames")
IMG_OUT = Path("data/yolo_dataset/images/train")
LBL_OUT = Path("data/yolo_dataset/labels/train")
REVIEW_DIR = Path("data/label_review")

IMG_OUT.mkdir(parents=True, exist_ok=True)
LBL_OUT.mkdir(parents=True, exist_ok=True)
REVIEW_DIR.mkdir(parents=True, exist_ok=True)


def _build_prompt() -> str:
    known = load_classes()
    known_list = "\n".join(f"- {c}" for c in known) or "(none yet)"
    return f"""You are labeling a Minecraft screenshot for YOLO object detection.

Classes already known to the detector:
{known_list}

Look at the image and return a JSON list of all clearly visible, nameable
objects. For each object:
- "class": a short lowercase_snake_case name. Reuse one of the known
  classes above whenever it reasonably applies. Only propose a NEW name
  (not on the list) when the object clearly doesn't match anything
  above — e.g. a Nether/End mob or block, a tool, an item the list has
  no entry for. New names should be generic and reusable (e.g. "blaze",
  "ender_pearl", "obsidian"), not a full sentence or a one-off
  description.
- "cx": center x as fraction of image width (0.0-1.0)
- "cy": center y as fraction of image height (0.0-1.0)
- "w": bounding box width as fraction of image width (0.0-1.0)
- "h": bounding box height as fraction of image height (0.0-1.0)
- "confidence": your confidence 0.0-1.0

Only include objects you can clearly see and confidently name. Return
ONLY valid JSON, no prose.
Example: [{{"class": "diamond_ore", "cx": 0.5, "cy": 0.4, "w": 0.1, "h": 0.12, "confidence": 0.9}}]
If nothing recognizable: []
"""


def label_frame(img_path: Path) -> list[dict]:
    with open(img_path, "rb") as f:
        raw_bytes = f.read()
    # frame_to_b64() expects an OpenCV BGR frame and re-encodes it; the
    # survey frames on disk are already JPEGs, so base64-encode directly
    # instead of decoding+reencoding through cv2 for no reason.
    import base64
    img_b64 = base64.standard_b64encode(raw_bytes).decode()

    raw = try_claude(_build_prompt(), max_tokens=1024, images=[img_b64], timeout=30.0)
    if raw is None:
        raise RuntimeError("claude call failed (see core.llm_router logs)")
    return json.loads(raw)


def write_yolo_label(labels: list[dict], out_path: Path) -> int:
    """Resolve each label's class name to a stable id (creating a new
    class the first time it's seen) and write the YOLO-format label
    file. Returns the number of boxes actually written (entries with an
    unnameable/invalid class string are skipped, not written as garbage)."""
    lines = []
    for obj in labels:
        raw_name = obj.get("class", "")
        cid = get_or_add_class(raw_name, source="claude_autolabel")
        if cid is None:
            print(f"    skipping unnameable class {raw_name!r}")
            continue
        cx, cy, w, h = obj["cx"], obj["cy"], obj["w"], obj["h"]
        lines.append(f"{cid} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
    out_path.write_text("\n".join(lines))
    return len(lines)


def save_for_review(img_path: Path, labels: list[dict]):
    """Save image + labels to review dir for the web UI."""
    stem = img_path.stem
    shutil.copy(img_path, REVIEW_DIR / img_path.name)
    (REVIEW_DIR / f"{stem}.json").write_text(json.dumps(labels, indent=2))


def run(dry_run=False, min_confidence=0.6):
    frames = sorted(SURVEY_DIR.glob("*.jpg")) + sorted(SURVEY_DIR.glob("*.png"))
    already_labeled = {p.stem for p in LBL_OUT.glob("*.txt")}
    pending = [f for f in frames if f.stem not in already_labeled]

    print(f"[AUTOLABEL] {len(pending)} frames to label ({len(already_labeled)} already done)")
    classes_before = len(load_classes())

    for i, img_path in enumerate(pending):
        print(f"[AUTOLABEL] {i+1}/{len(pending)}: {img_path.name}")
        try:
            labels = label_frame(img_path)
            # Filter low-confidence predictions and validate class names
            # up front so a garbage/hallucinated "class" doesn't count
            # toward whether this frame produced anything.
            labels = [l for l in labels
                      if l.get("confidence", 1.0) >= min_confidence
                      and normalize_class_name(l.get("class", "")) is not None]

            if dry_run:
                print(f"  -> {len(labels)} objects (dry run, not saving)")
                continue

            save_for_review(img_path, labels)
            shutil.copy(img_path, IMG_OUT / img_path.name)
            lbl_path = LBL_OUT / f"{img_path.stem}.txt"
            written = write_yolo_label(labels, lbl_path)
            print(f"  -> {written} objects labeled")
        except Exception as e:
            print(f"  ERROR: {e}")

    new_classes = len(load_classes()) - classes_before
    print(f"[AUTOLABEL] done — {new_classes} new class(es) discovered this run")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--min-confidence", type=float, default=0.6)
    args = p.parse_args()
    run(dry_run=args.dry_run, min_confidence=args.min_confidence)
