#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — YOLO Fine-Tuning Pipeline          ║
# ║  Minecraft-specific object detector training        ║
# ╚══════════════════════════════════════════════════════╝
#
# Workflow:
#   1. Collect  — capture labeled frames from AKSUMAEL sessions
#   2. Annotate — convert AKSUMAEL's label DB to YOLO format
#   3. Split    — train/val split
#   4. Train    — fine-tune yolov8n on Minecraft data
#   5. Export   — save model to data/models/aksumael_mc.pt
#   6. Activate — swap config.YOLO_MODEL to the new model
#
# Usage:
#   python3 tools/yolo_finetune.py collect   # run during gameplay
#   python3 tools/yolo_finetune.py annotate  # build dataset from DB
#   python3 tools/yolo_finetune.py train     # fine-tune
#   python3 tools/yolo_finetune.py status    # show dataset stats

import sys
import os
import json
import shutil
import random
import time
import pathlib

TRAIN_LOCK = pathlib.Path('/tmp/aksumael_training.lock')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import config
from core.class_registry import load_classes, get_or_add_class

DATASET_DIR = 'data/yolo_dataset'
IMAGES_DIR  = f'{DATASET_DIR}/images'
LABELS_DIR  = f'{DATASET_DIR}/labels'
TRAIN_SPLIT = 0.8   # 80% train, 20% val
MODEL_OUT   = 'data/models/aksumael_mc.pt'
MIN_IMAGES  = 30    # minimum before training makes sense


def class_id(label: str) -> int | None:
    """Map a label string to its stable YOLO class ID via the shared
    core.class_registry (data/yolo_dataset/data.yaml is the single source
    of truth — see that module's docstring for why the old hardcoded
    MC_CLASSES list here, and its reuse of skills.skill_system._canonical
    for "canonicalization", were both real bugs: MC_CLASSES had drifted
    stale relative to the real, deployed data.yaml, and _canonical()
    collapses e.g. 'creeper'/'zombie'/'skeleton' all down to the shared
    synonym-group name 'mob', which is correct for fuzzy skill-trigger
    matching but silently misfiled every mob detection under one wrong
    class id instead of its own trained one. Adds a new class
    automatically if the label hasn't been seen before. Returns None if
    the label doesn't normalize to a sane class name."""
    return get_or_add_class(label, source='frame_collector')


def _box_to_yolo(box: list, img_w: int, img_h: int) -> str:
    """Convert [x1,y1,x2,y2] pixel box to YOLO normalised xywh."""
    x1, y1, x2, y2 = box
    cx = ((x1 + x2) / 2) / img_w
    cy = ((y1 + y2) / 2) / img_h
    w  = (x2 - x1) / img_w
    h  = (y2 - y1) / img_h
    return f'{cx:.6f} {cy:.6f} {w:.6f} {h:.6f}'


# ── Collector ─────────────────────────────────────────────────
class FrameCollector:
    """
    Hooks into the AKSUMAEL camera to save labeled frames during gameplay.
    Call .maybe_save(frame, objects) each tick.
    Saves ~1 frame every N seconds to avoid redundant near-identical frames.
    """
    SAVE_INTERVAL_SEC = 3.0

    def __init__(self):
        os.makedirs(f'{IMAGES_DIR}/train', exist_ok=True)
        os.makedirs(f'{IMAGES_DIR}/val', exist_ok=True)
        os.makedirs(f'{LABELS_DIR}/train', exist_ok=True)
        os.makedirs(f'{LABELS_DIR}/val', exist_ok=True)
        self._last_save = 0
        self._count     = self._count_existing()
        print(f'[COLLECT] dataset dir ready. {self._count} images so far.')

    def _count_existing(self) -> int:
        n = 0
        for split in ('train', 'val'):
            d = f'{IMAGES_DIR}/{split}'
            if os.path.exists(d):
                n += len([f for f in os.listdir(d) if f.endswith('.jpg')])
        return n

    def maybe_save(self, frame, objects: list) -> bool:
        """Save frame + annotations if enough time has passed and objects are labeled."""
        import cv2
        now = time.time()
        if now - self._last_save < self.SAVE_INTERVAL_SEC:
            return False
        known = [o for o in objects
                 if not o.get('unknown') and o.get('label')]

        fh, fw = frame.shape[:2]
        split  = 'train' if random.random() < TRAIN_SPLIT else 'val'
        stem   = f'mc_{int(now*1000)}'
        img_path = f'{IMAGES_DIR}/{split}/{stem}.jpg'
        lbl_path = f'{LABELS_DIR}/{split}/{stem}.txt'

        cv2.imwrite(img_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 90])

        lines = []
        for obj in known:
            cid = class_id(obj['label'])
            if cid is None:
                continue   # label didn't normalize to a sane class name — skip this box
            yolo = _box_to_yolo(obj['box'], fw, fh)
            lines.append(f'{cid} {yolo}')

        with open(lbl_path, 'w') as f:
            f.write('\n'.join(lines))

        self._last_save = now
        self._count    += 1
        return True

    def force_save(self, frame, objects: list) -> bool:
        """
        Save frame + annotations immediately, bypassing the time throttle
        and the known-object gate. Used by the survey behavior to capture
        frames from multiple angles while AKSUMAEL is uncertain.
        Saves even if objects is empty. Filenames use a 'survey_' prefix.
        """
        import cv2
        now = time.time()
        known = [o for o in objects
                 if not o.get('unknown') and o.get('label')]

        fh, fw = frame.shape[:2]
        split  = 'train' if random.random() < TRAIN_SPLIT else 'val'
        stem   = f'survey_{int(now*1000)}_{self._count}'
        img_path = f'{IMAGES_DIR}/{split}/{stem}.jpg'
        lbl_path = f'{LABELS_DIR}/{split}/{stem}.txt'

        cv2.imwrite(img_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 90])

        lines = []
        for obj in known:
            cid = class_id(obj['label'])
            if cid is None:
                continue   # label didn't normalize to a sane class name — skip this box
            yolo = _box_to_yolo(obj['box'], fw, fh)
            lines.append(f'{cid} {yolo}')

        with open(lbl_path, 'w') as f:
            f.write('\n'.join(lines))

        self._last_save = now
        self._count    += 1
        return True

    def stats(self) -> dict:
        result = {}
        for split in ('train', 'val'):
            d = f'{IMAGES_DIR}/{split}'
            result[split] = len(os.listdir(d)) if os.path.exists(d) else 0
        result['total'] = sum(result.values())
        return result


# ── Annotator ─────────────────────────────────────────────────
def annotate_from_label_db():
    """
    Read AKSUMAEL's yolo_labels.json (user-taught labels) and
    create/update YOLO annotation files for any matching saved images.
    Run this after labeling sessions to backfill annotations.
    """
    db_path = config.YOLO_LABEL_DB
    if not os.path.exists(db_path):
        print('No label DB found yet. Label objects in the UI first.')
        return

    with open(db_path) as f:
        db = json.load(f)

    print(f'[ANNOTATE] {len(db)} user labels in DB')
    print('[ANNOTATE] Label DB is used at runtime — YOLO annotations')
    print('           are built from saved frames during collect mode.')
    print()
    print('Class map:')
    seen = set()
    for label in db.values():
        cid = class_id(label)
        if label not in seen:
            tag = f'{cid:3d}' if cid is not None else '  ?'
            print(f'  {tag} → {label}')
            seen.add(label)


# ── Trainer ───────────────────────────────────────────────────
MAX_TRAIN_FRAMES = 800


def _cap_recent_frames(max_frames: int = MAX_TRAIN_FRAMES):
    """
    Keep only the most recently modified `max_frames` images in the
    training set (and drop their orphaned label files), so the training
    set stays bounded as frame collection continues indefinitely.
    """
    train_img_dir = f'{IMAGES_DIR}/train'
    train_lbl_dir = f'{LABELS_DIR}/train'
    if not os.path.exists(train_img_dir):
        return

    img_exts = ('.jpg', '.jpeg', '.png')
    images = [f for f in os.listdir(train_img_dir) if f.lower().endswith(img_exts)]
    if len(images) <= max_frames:
        print(f'[TRAIN] rolling window: {len(images)} frames <= cap of {max_frames}, nothing dropped')
        return

    images.sort(key=lambda f: os.path.getmtime(os.path.join(train_img_dir, f)), reverse=True)
    dropped = images[max_frames:]

    for fname in dropped:
        os.remove(os.path.join(train_img_dir, fname))
        stem = os.path.splitext(fname)[0]
        lbl_path = os.path.join(train_lbl_dir, f'{stem}.txt')
        if os.path.exists(lbl_path):
            os.remove(lbl_path)

    print(f'[TRAIN] rolling window: kept {max_frames} newest frames, dropped {len(dropped)} older frames')


def train(epochs: int = 30, imgsz: int = 320, batch: int = 8):
    """
    Fine-tune aksumael_mc.pt (or bootstrap from yolov8n.pt if it doesn't
    exist yet) on the labeled Minecraft dataset in data/yolo_dataset.
    Trains on the RTX 4050 GPU when available; falls back to CPU otherwise.
    """
    if TRAIN_LOCK.exists():
        print(f'[TRAIN] another training run is already in progress ({TRAIN_LOCK.read_text().strip()}) — aborting.')
        return

    try:
        from ultralytics import YOLO
    except ImportError:
        print('ultralytics not installed: pip install ultralytics')
        return

    yaml_path = f'{DATASET_DIR}/data.yaml'
    if not os.path.exists(yaml_path):
        print(f'[TRAIN] {yaml_path} not found — run tools/prep_new_frames.py first.')
        return

    _cap_recent_frames()

    train_dir = f'{IMAGES_DIR}/train'
    img_exts  = ('.jpg', '.jpeg', '.png')
    total = (len([f for f in os.listdir(train_dir) if f.lower().endswith(img_exts)])
             if os.path.exists(train_dir) else 0)
    print(f'[TRAIN] dataset: {total} images in {train_dir}')

    if total < MIN_IMAGES:
        print(f'[TRAIN] only {total} images — need at least {MIN_IMAGES}.')
        print('        Keep playing with collect mode on to gather more data.')
        return

    os.makedirs('data/models', exist_ok=True)

    base_weights = MODEL_OUT if os.path.exists(MODEL_OUT) else 'yolov8s.pt'
    print(f'[TRAIN] starting fine-tune from {base_weights}: '
          f'epochs={epochs} imgsz={imgsz} batch={batch}')
    print()

    import torch
    _has_cuda = torch.cuda.is_available()
    _device   = 0 if _has_cuda else 'cpu'
    _amp      = _has_cuda           # AMP only with CUDA
    # 0 workers — this process shares a GPU/CUDA context with the live
    # AKSUMAEL capture/YOLO threads, and DataLoader worker subprocesses
    # opening a second CUDA context on top of that is what was tearing
    # down the training subprocess's stdout/stderr pipe mid-run.
    _workers  = 0
    _batch    = batch
    print(f'[TRAIN] device={_device}  amp={_amp}  workers={_workers}  batch={_batch}')

    TRAIN_LOCK.write_text(str(os.getpid()))
    try:
        model = YOLO(base_weights)
        results = model.train(
            data=yaml_path,
            epochs=epochs,
            imgsz=imgsz,
            batch=_batch,
            project='data/models',
            name='aksumael_mc',
            exist_ok=True,
            verbose=True,
            workers=_workers,
            cache=False,
            amp=_amp,
            device=_device,
        )
    finally:
        TRAIN_LOCK.unlink(missing_ok=True)

    # Copy best weights to standard path. Use results.save_dir (the actual
    # directory ultralytics wrote to) rather than a hardcoded guess — this
    # version of ultralytics nests it under runs/detect/<project>/<name>
    # instead of directly at <project>/<name>.
    best = str(results.save_dir / 'weights' / 'best.pt')
    if os.path.exists(best):
        shutil.copy(best, MODEL_OUT)
        print(f'\n[TRAIN] done. Best model saved to {MODEL_OUT}')
        print(f'        To use it: set YOLO_MODEL = "{MODEL_OUT}" in config.py')
    else:
        print(f'[TRAIN] training finished but {best} not found — check logs')


# ── Status ────────────────────────────────────────────────────
def status():
    col  = FrameCollector()
    stats = col.stats()
    print(f'Dataset: {stats}')
    print(f'Classes: {len(load_classes())}')
    needed = MIN_IMAGES - stats["total"]
    ready  = 'yes' if stats['total'] >= MIN_IMAGES else f'no (need {needed} more images)'
    print(f'Ready to train: {ready}')
    print(f'Model output:   {MODEL_OUT}')
    active = getattr(config, 'YOLO_MODEL', 'yolov8n.pt')
    print(f'Active model:   {active}')
    if os.path.exists(MODEL_OUT):
        import os
        sz = os.path.getsize(MODEL_OUT) // 1024
        print(f'Trained model:  {MODEL_OUT} ({sz} KB)')
    else:
        print('Trained model:  not yet trained')


# ── Main ──────────────────────────────────────────────────────
USAGE = """
YOLO Fine-Tuning Pipeline for AKSUMAEL / Minecraft

Commands:
  python3 tools/yolo_finetune.py status           — dataset stats
  python3 tools/yolo_finetune.py annotate         — inspect label DB
  python3 tools/yolo_finetune.py train            — train (30 epochs)
  python3 tools/yolo_finetune.py train 10         — quick train (10 epochs)
  python3 tools/yolo_finetune.py train 50 --gpu   — full train on GPU machine

To collect training data: set COLLECT_FRAMES=True in config.py
and run AKSUMAEL normally — frames are saved automatically during gameplay.
"""

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == 'status':
        status()
    elif cmd == 'annotate':
        annotate_from_label_db()
    elif cmd == 'train':
        epochs = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        train(epochs=epochs)
    else:
        print(USAGE)
