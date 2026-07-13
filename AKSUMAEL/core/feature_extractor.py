# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Neural Policy Feature Extractor           ║
# ╚══════════════════════════════════════════════════════╝
"""
Converts raw YOLO detections + world/goal state into the fixed-size
feature vectors core/neural_policy.NeuralPolicy expects. Kept separate
from neural_policy.py so the feature layout can evolve (new object
classes, new goal types) without touching the network itself.
"""

import os

import config
from core.neural_policy import ACTION_SPACE

MAX_OBJECTS = 20                 # padded/truncated object slots per tick
FRAME_W, FRAME_H = 640, 360       # YOLO-frame dims (core/capture.py latest_small_frame)

# Fallback class vocabulary — mirrors data/yolo_dataset/data.yaml at the
# time this was written. _load_object_classes() prefers the live file so
# retraining with new classes doesn't require touching this module.
_FALLBACK_CLASSES = [
    'health_bar', 'hunger_bar', 'armor_bar', 'xp_bar', 'hotbar', 'crosshair',
    'fire_hazard', 'chest_row', 'bed', 'torch', 'furnace', 'water',
    'coal_ore', 'redstone_ore', 'emerald_ore', 'copper_ore', 'creeper',
    'diamond_ore', 'iron_ore', 'gold_ore', 'lapis_ore', 'log', 'leaves',
    'zombie', 'skeleton', 'spider', 'crafting_table', 'grass', 'cow',
    'sheep', 'pig', 'chicken', 'fish', 'fishing_bobber', 'wheat', 'carrot',
    'potato', 'sugar_cane', 'pumpkin', 'melon', 'villager', 'sheep_wool',
    'iron_ingot', 'diamond', 'duck', 'village_house', 'birch_log',
    'oak_planks', 'cobblestone', 'oak_door', 'stone', 'gravel', 'sand',
]


def _load_object_classes() -> list[str]:
    path = os.path.join('data', 'yolo_dataset', 'data.yaml')
    try:
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f)
        names = data.get('names')
        if isinstance(names, dict):
            return [names[k] for k in sorted(names, key=int)]
        if isinstance(names, list):
            return list(names)
    except Exception:
        pass
    return list(_FALLBACK_CLASSES)


OBJECT_CLASSES = _load_object_classes()
_CLASS_INDEX = {name: i for i, name in enumerate(OBJECT_CLASSES)}

# Per-object: one-hot class + (x1, y1, x2, y2) normalized bbox + confidence
FEATURES_PER_OBJECT = len(OBJECT_CLASSES) + 5
OBS_DIM = MAX_OBJECTS * FEATURES_PER_OBJECT + len(ACTION_SPACE)

# Fixed goal vocabulary — mirrors memory/goals.py GOAL_PRIORITIES plus the
# craft_*_pickaxe tiers. Any goal outside this list (e.g. a dynamic
# curriculum-generated goal string) falls into the trailing 'other' slot.
GOAL_TYPES = [
    'explore', 'eat', 'survive_night', 'flee_danger', 'return_to_base',
    'find_shelter', 'find_crafting_table', 'mine_diamonds', 'mine_coal',
    'find_and_chop_tree', 'find_food', 'fish_for_food', 'plant_crops',
    'craft_wood_pickaxe', 'craft_stone_pickaxe', 'craft_iron_pickaxe',
    'craft_diamond_pickaxe', 'idle',
]
GOAL_DIM = len(GOAL_TYPES) + 1   # +1 catch-all 'other' bucket


def _object_feature(obj: dict) -> list:
    vec = [0.0] * FEATURES_PER_OBJECT
    idx = _CLASS_INDEX.get(obj.get('label', ''))
    if idx is not None:
        vec[idx] = 1.0

    box = obj.get('box')
    base = len(OBJECT_CLASSES)
    if box and len(box) == 4:
        x1, y1, x2, y2 = box
        vec[base + 0] = max(0.0, min(1.0, x1 / FRAME_W))
        vec[base + 1] = max(0.0, min(1.0, y1 / FRAME_H))
        vec[base + 2] = max(0.0, min(1.0, x2 / FRAME_W))
        vec[base + 3] = max(0.0, min(1.0, y2 / FRAME_H))
    vec[base + 4] = float(obj.get('conf', 0.0))
    return vec


def _action_dict_to_name(action_dict: dict):
    """Best-effort inverse of neural_policy.action_to_dict() — encodes any
    action dict (rule-based or neural in origin) back to a name in
    ACTION_SPACE, so 'last action' context reflects what actually ran, not
    just actions the neural policy itself picked."""
    if not action_dict:
        return None
    key = action_dict.get('key')
    if key in ('w', 'a', 's', 'd', 'space', 'ctrl', 'shift', 'e', 'f', 'q', 'esc'):
        return key
    click = action_dict.get('click')
    if click == 'left':
        return 'click_left'
    if click == 'right':
        return 'click_right'
    look = action_dict.get('look')
    if look:
        dx, dy = look.get('dx', 0), look.get('dy', 0)
        if abs(dx) >= abs(dy):
            if dx > 0:
                return 'look_right'
            if dx < 0:
                return 'look_left'
        else:
            if dy > 0:
                return 'look_down'
            if dy < 0:
                return 'look_up'
    return None


def extract_obs_features(objects: list, last_action: dict = None) -> list:
    """Fixed-size object feature block (highest-confidence first, padded or
    truncated to MAX_OBJECTS) ++ one-hot of the last discrete action taken."""
    objs = sorted(objects or [], key=lambda o: o.get('conf', 0.0), reverse=True)[:MAX_OBJECTS]

    feats = []
    for obj in objs:
        feats.extend(_object_feature(obj))
    pad = MAX_OBJECTS - len(objs)
    if pad > 0:
        feats.extend([0.0] * (pad * FEATURES_PER_OBJECT))

    last_vec = [0.0] * len(ACTION_SPACE)
    last_name = _action_dict_to_name(last_action)
    if last_name in ACTION_SPACE:
        last_vec[ACTION_SPACE.index(last_name)] = 1.0
    feats.extend(last_vec)

    return feats


def extract_goal_embedding(goal: str) -> list:
    """One-hot over GOAL_TYPES, with a trailing 'other' bucket for any
    goal string (dynamic curriculum goals, unrecognized craft_* tiers,
    etc.) that isn't in the fixed vocabulary."""
    vec = [0.0] * GOAL_DIM
    if goal in GOAL_TYPES:
        vec[GOAL_TYPES.index(goal)] = 1.0
    elif goal:
        vec[-1] = 1.0
    return vec
