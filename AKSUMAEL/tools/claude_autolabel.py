#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Claude Auto-Labeler                ║
# ║  Auto-label survey frames using Claude vision         ║
# ╚══════════════════════════════════════════════════════╝
#
# Produces YOLO-format label files in data/yolo_dataset/labels/train/
# and copies images to data/yolo_dataset/images/train/

import anthropic, base64, json, os, shutil, sys
from pathlib import Path

# Load classes from yolo_finetune
sys.path.insert(0, str(Path(__file__).parent))
from yolo_finetune import MC_CLASSES

KEY_FILE = os.path.expanduser("~/.config/anthropic/key")
SURVEY_DIR = Path("data/survey_frames")
IMG_OUT = Path("data/yolo_dataset/images/train")
LBL_OUT = Path("data/yolo_dataset/labels/train")
REVIEW_DIR = Path("data/label_review")

IMG_OUT.mkdir(parents=True, exist_ok=True)
LBL_OUT.mkdir(parents=True, exist_ok=True)
REVIEW_DIR.mkdir(parents=True, exist_ok=True)

def load_api_key():
    with open(KEY_FILE) as f:
        return f.read().strip()

LABEL_PROMPT = f"""You are labeling Minecraft screenshots for YOLO object detection.

Known classes (id: name):
{chr(10).join(f"{i}: {c}" for i, c in enumerate(MC_CLASSES))}

Look at the image and return a JSON list of all visible objects. For each object:
- "class_id": integer from the list above
- "cx": center x as fraction of image width (0.0-1.0)
- "cy": center y as fraction of image height (0.0-1.0)
- "w": bounding box width as fraction of image width (0.0-1.0)
- "h": bounding box height as fraction of image height (0.0-1.0)
- "confidence": your confidence 0.0-1.0

Only include objects you can clearly see. Return ONLY valid JSON, no prose.
Example: [{{"class_id": 5, "cx": 0.5, "cy": 0.4, "w": 0.1, "h": 0.12, "confidence": 0.9}}]
If nothing recognizable: []
"""

def label_frame(client: anthropic.Anthropic, img_path: Path) -> list[dict]:
    with open(img_path, "rb") as f:
        img_b64 = base64.standard_b64encode(f.read()).decode()

    ext = img_path.suffix.lower().lstrip(".")
    media_type = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"

    resp = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": img_b64}},
                {"type": "text", "text": LABEL_PROMPT}
            ]
        }]
    )

    text_blocks = [b.text for b in resp.content if b.type == "text"]
    raw = "".join(text_blocks).strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)

def write_yolo_label(labels: list[dict], out_path: Path):
    lines = []
    for obj in labels:
        cid = obj["class_id"]
        cx, cy, w, h = obj["cx"], obj["cy"], obj["w"], obj["h"]
        lines.append(f"{cid} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
    out_path.write_text("\n".join(lines))

def save_for_review(img_path: Path, labels: list[dict]):
    """Save image + labels to review dir for the web UI."""
    stem = img_path.stem
    shutil.copy(img_path, REVIEW_DIR / img_path.name)
    (REVIEW_DIR / f"{stem}.json").write_text(json.dumps(labels, indent=2))

def run(dry_run=False, min_confidence=0.6):
    key = load_api_key()
    client = anthropic.Anthropic(api_key=key)

    frames = sorted(SURVEY_DIR.glob("*.jpg")) + sorted(SURVEY_DIR.glob("*.png"))
    already_labeled = {p.stem for p in LBL_OUT.glob("*.txt")}
    pending = [f for f in frames if f.stem not in already_labeled]

    print(f"[AUTOLABEL] {len(pending)} frames to label ({len(already_labeled)} already done)")

    for i, img_path in enumerate(pending):
        print(f"[AUTOLABEL] {i+1}/{len(pending)}: {img_path.name}")
        try:
            labels = label_frame(client, img_path)
            # Filter low-confidence predictions
            labels = [l for l in labels if l.get("confidence", 1.0) >= min_confidence]

            if dry_run:
                print(f"  → {len(labels)} objects (dry run, not saving)")
                continue

            # Save for review
            save_for_review(img_path, labels)

            # Copy image to dataset
            shutil.copy(img_path, IMG_OUT / img_path.name)

            # Write YOLO label
            lbl_path = LBL_OUT / f"{img_path.stem}.txt"
            write_yolo_label(labels, lbl_path)

            print(f"  → {len(labels)} objects labeled")
        except Exception as e:
            print(f"  ERROR: {e}")

    print("[AUTOLABEL] done")

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--min-confidence", type=float, default=0.6)
    args = p.parse_args()
    run(dry_run=args.dry_run, min_confidence=args.min_confidence)
