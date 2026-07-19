#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Animal Auto-Labeler                ║
# ║  Uses local mesh-llm vision to bootstrap animal/       ║
# ║  villager training data from already-collected survey ║
# ║  frames.                                                ║
# ╚══════════════════════════════════════════════════════╝
#
# AKSUMAEL's YOLO model has zero animal training examples — survey
# frames get saved whenever the model is uncertain, but nothing ever
# labels the animals in them. This script asks the local mesh-llm vision
# model to look at recent survey frames and draw boxes around any
# animals/villagers it finds, then writes those boxes as YOLO-format
# labels alongside the existing (HUD-only) annotations.
#
# Usage:
#   python3 tools/auto_label_animals.py [N]     # label the N most recent survey frames (default 100)

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

TARGET_CLASSES = ['chicken', 'duck', 'cow', 'sheep', 'pig', 'villager', 'village_house']

MAX_RETRIES = 4
BACKOFF_BASE = 2.0

PROMPT = """You are labeling a Minecraft gameplay screenshot for an object detector.

Look ONLY for these classes: chicken, duck, cow, sheep, pig, villager, village_house.
("village_house" means a visible villager building/structure, e.g. a wooden or
cobblestone house with a bell/path typical of a village — not a random player build.)

For every instance you find, give a tight bounding box in PERCENT coordinates
(0-100, relative to image width/height, origin top-left).

Respond with ONLY a JSON array, no prose, no markdown fences. Example:
[{"label": "cow", "x1": 12.5, "y1": 40.0, "x2": 38.0, "y2": 71.5}]

If none of the target classes are visible, respond with exactly: []
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


def recent_survey_frames(n: int) -> list:
    files = [f for f in os.listdir(IMAGES_DIR) if f.startswith('survey_') and f.endswith('.jpg')]
    files = [os.path.join(IMAGES_DIR, f) for f in files]
    files.sort(key=os.path.getmtime, reverse=True)
    return files[:n]


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


def call_mesh_llm(img_path: str) -> list:
    with open(img_path, 'rb') as f:
        b64 = base64.standard_b64encode(f.read()).decode('utf-8')

    payload = json.dumps({
        "model": "auto",
        "max_tokens": 500,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
    }).encode()

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(
                MESH_LLM_URL, data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=40) as resp:
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

    print(f'[WARN] {os.path.basename(img_path)}: {last_error}')
    return []


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
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100

    yaml_cfg = load_data_yaml()
    names = {int(k): v for k, v in yaml_cfg['names'].items()}
    label_db = load_label_db()

    target_ids = set()
    for cls in TARGET_CLASSES:
        target_ids.add(get_class_id(cls, names, label_db))

    frames = recent_survey_frames(n)
    print(f'[LABEL] scanning {len(frames)} most recent survey frames for {TARGET_CLASSES}')

    frames_with_animals = 0
    class_counts = {}

    for i, img_path in enumerate(frames, 1):
        stem = os.path.splitext(os.path.basename(img_path))[0]
        label_path = os.path.join(LABELS_DIR, f'{stem}.txt')

        if already_labeled(label_path, target_ids):
            continue

        detections = call_mesh_llm(img_path)
        detections = [d for d in detections if isinstance(d, dict) and d.get('label') in TARGET_CLASSES]
        rejected = [d for d in detections if not is_valid_box(d)]
        for d in rejected:
            print(f'[REJECT] {stem}: bad box for {d.get("label")}: {d}')
        detections = [d for d in detections if is_valid_box(d)]

        if detections:
            frames_with_animals += 1
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

            print(f'[{i}/{len(frames)}] {stem}: {[d["label"] for d in detections]}')
        else:
            print(f'[{i}/{len(frames)}] {stem}: none')

    yaml_cfg['nc'] = len(names)
    yaml_cfg['names'] = dict(sorted(names.items()))
    save_data_yaml(yaml_cfg)
    save_label_db(label_db)

    print()
    print(f'[DONE] {frames_with_animals}/{len(frames)} frames had animals/villagers')
    print(f'[DONE] class breakdown: {class_counts}')


if __name__ == '__main__':
    main()
