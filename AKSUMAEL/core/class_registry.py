# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — YOLO Class Registry                       ║
# ╚══════════════════════════════════════════════════════╝
#
# Single source of truth for the trainable class list, backed directly by
# data/yolo_dataset/data.yaml. Before this module existed there were three
# separate copies of the class list (a hardcoded MC_CLASSES in
# tools/yolo_finetune.py, tools/claude_autolabel.py's import of that same
# stale list, and a fallback snapshot in core/feature_extractor.py) that
# had already drifted out of sync with the real, deployed data.yaml —
# the hardcoded copies had 43 entries, the actual training config had 53.
# Anything that needs to know "what classes exist" or "assign this new
# label an id" should go through here instead of keeping its own copy.

import json
import os
import re
import time

DATA_YAML      = os.path.join('data', 'yolo_dataset', 'data.yaml')
DISCOVERY_LOG  = os.path.join('data', 'memory', 'discovered_classes.jsonl')

_NAME_RE = re.compile(r'^[a-z][a-z0-9_]{0,40}$')

_DEFAULT_HEADER = {
    'path':  'data/yolo_dataset',
    'train': 'images/train',
    'val':   'images/val',
}


def normalize_class_name(raw: str) -> str | None:
    """Canonicalize a proposed class name to a stable identity string —
    lowercase, underscored, no semantic collapsing. Deliberately NOT
    skills.skill_system._canonical(): that function groups synonyms for
    fuzzy skill-trigger matching (e.g. 'creeper'/'zombie'/'skeleton' all
    collapse to 'mob') which is correct for "does a skill's trigger loosely
    match what's on screen" but wrong here — it would silently misfile
    every mob detection under one shared 'mob' class id instead of its
    own trained one. Returns None if the result isn't a sane class name
    (empty, too long, or containing anything but lowercase/digits/
    underscore) so the caller can skip the detection instead of forcing
    a bad id.
    """
    if not raw:
        return None
    name = raw.strip().lower().replace(' ', '_').replace('-', '_')
    name = re.sub(r'_+', '_', name).strip('_')
    return name if _NAME_RE.match(name) else None


def _read_yaml(path: str) -> dict:
    import yaml
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_classes() -> list[str]:
    """Current class list, ordered by id. [] if data.yaml doesn't exist
    yet or fails to parse."""
    if not os.path.exists(DATA_YAML):
        return []
    try:
        data = _read_yaml(DATA_YAML)
    except Exception as e:
        print(f'[CLASS_REGISTRY] failed to read {DATA_YAML}: {e}')
        return []
    names = data.get('names')
    if isinstance(names, dict):
        return [names[k] for k in sorted(names, key=int)]
    if isinstance(names, list):
        return list(names)
    return []


def _write_classes(names: list[str]):
    """Persist the full class list back to data.yaml, preserving the
    existing path/train/val header. Atomic (write-temp + rename) so a
    process killed mid-write can't leave a half-written yaml that
    silently breaks every training run after it."""
    header = dict(_DEFAULT_HEADER)
    if os.path.exists(DATA_YAML):
        try:
            existing = _read_yaml(DATA_YAML)
            for k in ('path', 'train', 'val'):
                if k in existing:
                    header[k] = existing[k]
        except Exception:
            pass

    lines = [
        f"path: {header['path']}",
        f"train: {header['train']}",
        f"val: {header['val']}",
        f'nc: {len(names)}',
        'names:',
    ]
    lines += [f'  {i}: {name}' for i, name in enumerate(names)]

    os.makedirs(os.path.dirname(DATA_YAML), exist_ok=True)
    tmp_path = DATA_YAML + '.tmp'
    with open(tmp_path, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    os.replace(tmp_path, DATA_YAML)


def _log_discovery(name: str, class_id: int, source: str):
    try:
        os.makedirs(os.path.dirname(DISCOVERY_LOG), exist_ok=True)
        with open(DISCOVERY_LOG, 'a') as f:
            f.write(json.dumps({
                'class': name, 'id': class_id, 'source': source,
                'ts': time.time(),
            }) + '\n')
    except Exception as e:
        print(f'[CLASS_REGISTRY] discovery log write failed: {e}')


def get_or_add_class(raw_name: str, source: str = 'unknown') -> int | None:
    """Resolve a label string to its stable class id, creating a new
    class (appended to data.yaml, logged to discovered_classes.jsonl) the
    first time it's seen. Returns None if raw_name doesn't normalize to a
    sane class name — callers should skip that detection rather than
    force a bad id.

    Re-reads data.yaml on every call instead of caching in memory: this
    is called from short-lived offline labeling/training subprocesses and
    occasionally from the live agent's survey-save path, not a hot
    per-tick loop, so the extra file read is cheap and it's what keeps
    every caller honest about the current on-disk state instead of
    drifting the way the old hardcoded MC_CLASSES list did.
    """
    name = normalize_class_name(raw_name)
    if name is None:
        return None

    classes = load_classes()
    if name in classes:
        return classes.index(name)

    classes.append(name)
    _write_classes(classes)
    new_id = len(classes) - 1
    print(f"[CLASS_REGISTRY] new class discovered: '{name}' -> id {new_id} (source={source})")
    _log_discovery(name, new_id, source)
    return new_id
