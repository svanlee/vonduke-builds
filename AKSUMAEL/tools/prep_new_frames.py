#!/usr/bin/env python3
"""
prep_new_frames.py — Extract new frame zips + auto-label with aksumael_mc.pt

Run from AKSUMAEL repo root:
    python3 tools/prep_new_frames.py

Or with the local venv:
    venv/bin/python3 tools/prep_new_frames.py

Steps performed:
  1. Extract new_frames.zip / new_frames2.zip / new_frames3.zip / new_frames4.zip
     into data/yolo_dataset/images/train/, renaming any filename collisions.
  2. Delete zips after successful extraction.
  3. Run aksumael_mc.pt inference on ALL unlabeled frames (old + new) at conf=0.25.
  4. Write YOLO .txt label files to data/yolo_dataset/labels/train/.
  5. Print a dataset status summary.

Note: data.yaml is already correct (18 classes). No YAML update needed.
"""

import os
import sys
import zipfile
import shutil
from pathlib import Path

REPO_ROOT  = Path(__file__).resolve().parent.parent
TRAIN_IMG  = REPO_ROOT / 'data' / 'yolo_dataset' / 'images' / 'train'
TRAIN_LBL  = REPO_ROOT / 'data' / 'yolo_dataset' / 'labels' / 'train'
MODEL_PATH = REPO_ROOT / 'data' / 'models' / 'aksumael_mc.pt'
CONF       = 0.25   # lower than runtime 0.5 — cast wider net for bootstrap labels

ZIPS = [
    TRAIN_IMG / 'new_frames.zip',
    TRAIN_IMG / 'new_frames2.zip',
    TRAIN_IMG / 'new_frames3.zip',
    TRAIN_IMG / 'new_frames4.zip',
]

IMG_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}


# ── Step 1 & 2: Extract zips ──────────────────────────────────────────────────

def extract_zips():
    TRAIN_IMG.mkdir(parents=True, exist_ok=True)
    existing = {f.name for f in TRAIN_IMG.iterdir() if f.suffix.lower() in IMG_EXTS}

    total_extracted = 0
    total_renamed   = 0

    for zpath in ZIPS:
        if not zpath.exists():
            print(f'[SKIP] {zpath.name} not found')
            continue

        print(f'\n[EXTRACT] {zpath.name} ...')
        extracted_this_zip = 0

        with zipfile.ZipFile(zpath, 'r') as zf:
            members = [m for m in zf.infolist()
                       if not m.is_dir() and Path(m.filename).suffix.lower() in IMG_EXTS]
            print(f'         {len(members)} image(s) inside')

            for member in members:
                # Use only the basename, ignore directory structure inside zip
                orig_name = Path(member.filename).name
                dest_name = orig_name

                # Collision rename: prepend zip stem until unique
                zip_stem = zpath.stem  # e.g. "new_frames2"
                while dest_name in existing:
                    stem, ext = os.path.splitext(dest_name)
                    dest_name = f'{stem}_{zip_stem}{ext}'

                dest_path = TRAIN_IMG / dest_name

                data = zf.read(member.filename)
                with open(dest_path, 'wb') as f:
                    f.write(data)

                if dest_name != orig_name:
                    print(f'         renamed {orig_name} → {dest_name}')
                    total_renamed += 1

                existing.add(dest_name)
                extracted_this_zip += 1
                total_extracted     += 1

        print(f'         extracted {extracted_this_zip} frames')

        # Delete zip after successful extraction
        zpath.unlink()
        print(f'         deleted {zpath.name}')

    print(f'\n[EXTRACT] done — {total_extracted} frames added, {total_renamed} renamed')
    return total_extracted


# ── Step 3 & 4: Auto-label with YOLO inference ────────────────────────────────

def run_inference():
    TRAIN_LBL.mkdir(parents=True, exist_ok=True)

    # Find all images that don't have a label file yet
    all_imgs = sorted([
        f for f in TRAIN_IMG.iterdir()
        if f.suffix.lower() in IMG_EXTS
    ])
    unlabeled = [f for f in all_imgs if not (TRAIN_LBL / (f.stem + '.txt')).exists()]

    print(f'\n[LABEL] {len(all_imgs)} total images, {len(unlabeled)} unlabeled')

    if not unlabeled:
        print('[LABEL] nothing to do — all frames already labeled')
        return 0

    if not MODEL_PATH.exists():
        print(f'[LABEL] ERROR: model not found at {MODEL_PATH}')
        print('        Cannot auto-label. Label manually or train a model first.')
        return 0

    try:
        from ultralytics import YOLO
    except ImportError:
        print('[LABEL] ERROR: ultralytics not installed.')
        print('        Run: pip install ultralytics  (or use the project venv)')
        return 0

    print(f'[LABEL] loading model: {MODEL_PATH.name}')
    model = YOLO(str(MODEL_PATH))

    labeled_count   = 0
    empty_count     = 0
    BATCH           = 16   # process in batches to avoid OOM on Pi

    img_paths = [str(f) for f in unlabeled]

    for i in range(0, len(img_paths), BATCH):
        batch = img_paths[i:i + BATCH]
        results = model(batch, conf=CONF, verbose=False)

        for img_path, result in zip(batch, results):
            stem     = Path(img_path).stem
            lbl_path = TRAIN_LBL / (stem + '.txt')

            boxes = result.boxes
            if boxes is None or len(boxes) == 0:
                # Write empty label file (valid YOLO — background frame)
                lbl_path.write_text('')
                empty_count  += 1
            else:
                lines = []
                h, w = result.orig_shape
                for box in boxes:
                    cls_id = int(box.cls[0].item())
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    cx = ((x1 + x2) / 2) / w
                    cy = ((y1 + y2) / 2) / h
                    bw = (x2 - x1) / w
                    bh = (y2 - y1) / h
                    lines.append(f'{cls_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}')
                lbl_path.write_text('\n'.join(lines) + '\n')
                labeled_count += 1

        done = min(i + BATCH, len(img_paths))
        print(f'[LABEL] {done}/{len(img_paths)} processed ...')

    total_written = labeled_count + empty_count
    print(f'\n[LABEL] done — {total_written} label files written')
    print(f'         {labeled_count} with detections, {empty_count} empty (background frames)')
    return total_written


# ── Step 5: Status report ─────────────────────────────────────────────────────

def status_report():
    all_imgs = sorted([f for f in TRAIN_IMG.iterdir() if f.suffix.lower() in IMG_EXTS])
    all_lbls = sorted([f for f in TRAIN_LBL.iterdir() if f.suffix == '.txt']) if TRAIN_LBL.exists() else []

    img_stems = {f.stem for f in all_imgs}
    lbl_stems = {f.stem for f in all_lbls}
    missing   = img_stems - lbl_stems

    print('\n' + '═' * 52)
    print('  DATASET STATUS')
    print('═' * 52)
    print(f'  Images in train/ : {len(all_imgs)}')
    print(f'  Labels in train/ : {len(all_lbls)}')
    print(f'  Unlabeled images : {len(missing)}')

    if all_lbls:
        # Count non-empty labels and total annotations
        total_boxes = 0
        empty_lbls  = 0
        for lbl in all_lbls:
            lines = [l.strip() for l in lbl.read_text().splitlines() if l.strip()]
            if lines:
                total_boxes += len(lines)
            else:
                empty_lbls += 1
        print(f'  Total boxes      : {total_boxes}')
        print(f'  Empty label files: {empty_lbls}')

    min_needed = 30
    ready = len(all_imgs) >= min_needed and len(missing) == 0
    print(f'\n  Ready to retrain : {"YES ✓" if ready else "NO"}')
    if not ready:
        if len(all_imgs) < min_needed:
            print(f'  (need {min_needed - len(all_imgs)} more images)')
        if missing:
            print(f'  ({len(missing)} images still need labels)')

    print('\n  To retrain:')
    print('    cd /path/to/AKSUMAEL')
    print('    venv/bin/python3 tools/yolo_finetune.py train 30')
    print('═' * 52)

    return ready


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print('AKSUMAEL — prep_new_frames.py')
    print(f'Repo root: {REPO_ROOT}')

    new_frames = extract_zips()
    labeled    = run_inference()
    ready      = status_report()

    sys.exit(0 if ready else 1)
