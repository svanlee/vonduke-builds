#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Block/Zero-Example Auto-Labeler    ║
# ║  Uses local mesh-llm vision to bootstrap training      ║
# ║  data for classes that currently have zero labeled     ║
# ║  examples.                                              ║
# ╚══════════════════════════════════════════════════════╝
#
# Several YOLO classes (animals, torch, crafting_table, log) have
# never been labeled despite appearing in hundreds of survey frames,
# and several new block classes (oak_planks, cobblestone, oak_door,
# stone, gravel) don't exist in the dataset yet at all. This script
# asks the local mesh-llm vision model to look at every survey frame in
# the training set and draw boxes around any instances of these classes,
# batching several frames into each call to keep this efficient at
# ~600+ frames.
#
# Usage:
#   python3 tools/auto_label_blocks.py [N]   # label the N most recent survey frames (default: all)

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

DATASET_DIR   = 'data/yolo_dataset'
IMAGES_DIR    = f'{DATASET_DIR}/images/train'
LABELS_DIR    = f'{DATASET_DIR}/labels/train'
DATA_YAML     = f'{DATASET_DIR}/data.yaml'
LABEL_DB_PATH = 'data/yolo_labels.json'
MESH_LLM_URL  = 'http://localhost:9337/v1/chat/completions'

# Zero-example classes that already exist in data.yaml.
TARGET_CLASSES = [
    'chicken', 'cow', 'sheep', 'pig',       # animals
    'torch', 'crafting_table', 'log',       # misc HUD/world objects
    # New block classes (added to data.yaml alongside this script).
    'oak_planks', 'cobblestone', 'oak_door', 'stone', 'gravel',
]

BATCH_SIZE   = 10
MAX_RETRIES  = 4
BACKOFF_BASE = 2.0

PROMPT_HEADER = """You are labeling a batch of Minecraft gameplay screenshots for an object detector.

You will see {n} images in order, each preceded by a text marker "IMAGE i:" giving its
zero-based index in this batch.

Look ONLY for these classes in each image:
- chicken, cow, sheep, pig — live animals
- torch — a wall or floor torch (small flame on a stick)
- crafting_table — the 2x2 crafting block with a diagonal cross pattern on top
- log — an oak or spruce tree trunk block (NOT birch — birch has white bark, skip it)
- oak_planks — light tan smooth wood plank blocks
- cobblestone — grey stone block with an irregular/bumpy texture
- oak_door — a wooden door
- stone — smooth solid grey stone block (not cobblestone's bumpy texture)
- gravel — mottled grey/brown loose ground block

For every instance found, give a tight bounding box in PERCENT coordinates
(0-100, relative to that image's own width/height, origin top-left).

Respond with ONLY a JSON object mapping each image index (as a string) to an array of
detections for that image, no prose, no markdown fences. Example:
{{"0": [{{"label": "cow", "x1": 12.5, "y1": 40.0, "x2": 38.0, "y2": 71.5}}], "1": []}}

Every index from 0 to {last} must be present as a key, using an empty array if nothing
from the target classes is visible in that image.
"""


def load_data_yaml() -> dict:
    with open(DATA_YAML) as f:
        return yaml.safe_load(f)


def save_data_yaml(cfg: dict):
    with open(DATA_YAML, 'w') as f:
        yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False)


def load_label_db() -> dict:
    if os.path.exists(LABEL_DB_PATH):
        with open(LABEL_DB_PATH) as f:
            return json.load(f)
    return {}


def save_label_db(db: dict):
    with open(LABEL_DB_PATH, 'w') as f:
        json.dump(db, f, indent=2)


def get_class_id(label: str, names: dict, label_db: dict) -> int:
    """Look up label in the data.yaml class map, adding a new id if needed."""
    for cid, name in names.items():
        if name == label:
            return cid
    new_id = max(names.keys()) + 1 if names else 0
    names[new_id] = label
    label_db.setdefault('auto_labeled_classes', {})[label] = new_id
    print(f'[CLASS] added new class {new_id} -> {label}')
    return new_id


def all_survey_frames(n: int = None) -> list:
    files = [f for f in os.listdir(IMAGES_DIR) if f.startswith('survey_') and f.endswith('.jpg')]
    files = [os.path.join(IMAGES_DIR, f) for f in files]
    files.sort(key=os.path.getmtime, reverse=True)
    return files[:n] if n else files


def already_labeled(label_path: str, class_ids: set) -> bool:
    """Skip frames whose label file already has one of our target classes."""
    if not os.path.exists(label_path):
        return False
    with open(label_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                cid = int(line.split()[0])
            except ValueError:
                continue
            if cid in class_ids:
                return True
    return False


def call_mesh_llm_batch(img_paths: list) -> dict:
    """Send a batch of images in one message; returns {index: [detections]}."""
    content = []
    for i, img_path in enumerate(img_paths):
        with open(img_path, 'rb') as f:
            b64 = base64.standard_b64encode(f.read()).decode('utf-8')
        content.append({"type": "text", "text": f"IMAGE {i}:"})
        content.append({"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    prompt = PROMPT_HEADER.format(n=len(img_paths), last=len(img_paths) - 1)
    content.append({"type": "text", "text": prompt})

    payload = json.dumps({
        "model": "auto",
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": content}],
    }).encode()

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(
                MESH_LLM_URL, data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            text = data["choices"][0]["message"]["content"].strip()
            if text.startswith('```'):
                text = '\n'.join(text.split('\n')[1:-1])
            return json.loads(text)
        except json.JSONDecodeError as e:
            last_error = f'bad JSON from model: {e}'
            break  # not transient, retrying won't help
        except Exception as e:
            last_error = str(e)
            if attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF_BASE * (2 ** attempt))

    print(f'[WARN] batch of {len(img_paths)}: {last_error}')
    return {}


def is_valid_box(det: dict) -> bool:
    """Reject boxes with out-of-range or degenerate coordinates (model hallucinations)."""
    x1, y1, x2, y2 = det.get('x1'), det.get('y1'), det.get('x2'), det.get('y2')
    if any(v is None for v in (x1, y1, x2, y2)):
        return False
    if not all(0 <= v <= 100 for v in (x1, y1, x2, y2)):
        return False
    return x2 > x1 and y2 > y1


def box_to_yolo(det: dict) -> str:
    x1, y1, x2, y2 = det['x1'], det['y1'], det['x2'], det['y2']
    cx = ((x1 + x2) / 2) / 100.0
    cy = ((y1 + y2) / 2) / 100.0
    w  = (x2 - x1) / 100.0
    h  = (y2 - y1) / 100.0
    return f'{cx:.6f} {cy:.6f} {w:.6f} {h:.6f}'


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else None

    yaml_cfg = load_data_yaml()
    names = {int(k): v for k, v in yaml_cfg['names'].items()}
    label_db = load_label_db()

    target_ids = set()
    for cls in TARGET_CLASSES:
        target_ids.add(get_class_id(cls, names, label_db))

    frames = all_survey_frames(n)
    pending = []
    for img_path in frames:
        stem = os.path.splitext(os.path.basename(img_path))[0]
        label_path = os.path.join(LABELS_DIR, f'{stem}.txt')
        if not already_labeled(label_path, target_ids):
            pending.append(img_path)

    print(f'[LABEL] {len(frames)} survey frames found, {len(pending)} pending scan for {TARGET_CLASSES}')

    frames_with_detections = 0
    class_counts = {}
    scanned = 0

    for batch_start in range(0, len(pending), BATCH_SIZE):
        batch = pending[batch_start:batch_start + BATCH_SIZE]
        result = call_mesh_llm_batch(batch)
        scanned += len(batch)

        for i, img_path in enumerate(batch):
            stem = os.path.splitext(os.path.basename(img_path))[0]
            label_path = os.path.join(LABELS_DIR, f'{stem}.txt')

            detections = result.get(str(i), [])
            detections = [d for d in detections if isinstance(d, dict) and d.get('label') in TARGET_CLASSES]
            rejected = [d for d in detections if not is_valid_box(d)]
            for d in rejected:
                print(f'[REJECT] {stem}: bad box for {d.get("label")}: {d}')
            detections = [d for d in detections if is_valid_box(d)]

            if detections:
                frames_with_detections += 1
                lines = []
                for det in detections:
                    cid = get_class_id(det['label'], names, label_db)
                    lines.append(f'{cid} {box_to_yolo(det)}')
                    class_counts[det['label']] = class_counts.get(det['label'], 0) + 1

                needs_newline = os.path.exists(label_path) and os.path.getsize(label_path) > 0
                with open(label_path, 'a') as f:
                    if needs_newline:
                        f.write('\n')
                    f.write('\n'.join(lines))

                print(f'[{scanned - len(batch) + i + 1}/{len(pending)}] {stem}: {[d["label"] for d in detections]}')
            else:
                print(f'[{scanned - len(batch) + i + 1}/{len(pending)}] {stem}: none')

        # Persist progress after every batch so a crash doesn't lose work.
        yaml_cfg['nc'] = len(names)
        yaml_cfg['names'] = dict(sorted(names.items()))
        save_data_yaml(yaml_cfg)
        save_label_db(label_db)

    print()
    print(f'[DONE] scanned {scanned} frames, {frames_with_detections} had target-class detections')
    print(f'[DONE] class breakdown: {class_counts}')


if __name__ == '__main__':
    main()
