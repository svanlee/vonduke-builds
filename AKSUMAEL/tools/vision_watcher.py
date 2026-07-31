#!/usr/bin/env python3
"""
vision_watcher.py — Local Vision eye on the live feed

Polls the frame server every POLL_S seconds, sends the frame to the
local Qwen3.5-4B-Vision model (llama-server at localhost:9337) and asks
what novel objects are visible that aren't already in AKSUMAEL's training
class list.  When something new is spotted, it writes
data/learning_trigger.json which runtime.py picks up on the next tick to
start a learning orbit.

Run with:
    venv/bin/python3 -u tools/vision_watcher.py

Uses LOCAL_LLM_URL from config.py (http://localhost:9337/v1).
No API keys or external calls — fully local.

The watcher is intentionally "slow" — one inference call every POLL_S
seconds.  TRIGGER_COOLDOWN_S prevents flooding the bot with back-to-back
learning orbits.
"""

import base64
import json
import os
import time
import urllib.request

import cv2
import numpy as np

# ── config ────────────────────────────────────────────────────────────────────
POLL_S             = 20          # seconds between frame grabs
TRIGGER_COOLDOWN_S = 240         # min seconds between trigger writes
FRAME_URL          = 'http://localhost:8765/frame'
TRIGGER_PATH       = 'data/learning_trigger.json'
DATA_YAML          = os.path.join('data', 'yolo_dataset', 'data.yaml')

# Local llama-server (Qwen3.5-4B-Vision) — from config.py LOCAL_LLM_URL
LOCAL_LLM_BASE     = 'http://localhost:9337/v1'
LOCAL_LLM_TIMEOUT  = 40         # seconds (inference can be slow)

# Minimum fraction of frame an interesting object should occupy (filters tiny noise)
MIN_AREA_FRAC = 0.01

# ── helpers ───────────────────────────────────────────────────────────────────


def _load_class_names() -> list[str]:
    """Read current class list from data.yaml."""
    if not os.path.exists(DATA_YAML):
        return []
    try:
        import yaml
        with open(DATA_YAML) as f:
            data = yaml.safe_load(f) or {}
        names = data.get('names', [])
        if isinstance(names, dict):
            return [names[k] for k in sorted(names, key=int)]
        return list(names)
    except Exception as e:
        print(f'[WATCHER] yaml read error: {e}')
        return []


def _fetch_frame() -> np.ndarray | None:
    try:
        with urllib.request.urlopen(FRAME_URL, timeout=4) as resp:
            data = resp.read()
        arr = np.frombuffer(data, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception as e:
        print(f'[WATCHER] fetch failed: {e}')
        return None


def _frame_to_b64(frame: np.ndarray) -> str:
    """Encode BGR frame as JPEG base64 for the API."""
    _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return base64.b64encode(buf.tobytes()).decode()


def _ask_local_vision(b64_img: str, known_classes: list[str]) -> dict | None:
    """
    Ask the local Qwen3.5-4B-Vision model via OpenAI-compatible chat endpoint.
    Returns a dict with keys: found (bool), label (str), description (str),
    box_frac ([cx, cy, w, h] as fractions), or None on error.
    """
    class_list_str = ', '.join(known_classes[:80])

    system = (
        'You are a Minecraft computer vision assistant helping train a YOLO object detector. '
        'You will be shown a Minecraft game frame. '
        'Identify any significant object, entity, or block clearly visible in the '
        'gameplay area (ignore HUD: hearts, hunger, hotbar, crosshair). '
        'Respond ONLY with a single JSON object — no prose, no markdown. '
        'If you find a novel object not in the known class list: '
        '{"found": true, "label": "snake_case_name", "description": "brief description", '
        '"box_frac": [cx, cy, width, height]} '
        'where box_frac values are 0.0-1.0 fractions of image dimensions. '
        'If everything visible is already in the known list, respond: {"found": false} '
        'Only report something if you are confident it is a real distinct game object.'
    )

    user_text = (
        f'Known training classes: [{class_list_str}]\n\n'
        f'What novel Minecraft object do you see that is NOT in this list? '
        f'If nothing new is visible, respond {{"found": false}}.'
    )

    payload = {
        'model':                'auto',
        'max_tokens':           400,
        'temperature':          0.1,
        'chat_template_kwargs': {'enable_thinking': False},  # disable Qwen3 chain-of-thought
        'messages': [
            {'role': 'system', 'content': system},
            {
                'role':    'user',
                'content': [
                    {
                        'type':      'image_url',
                        'image_url': {'url': f'data:image/jpeg;base64,{b64_img}'},
                    },
                    {'type': 'text', 'text': user_text},
                ],
            },
        ],
    }

    req = urllib.request.Request(
        f'{LOCAL_LLM_BASE}/chat/completions',
        data=json.dumps(payload).encode(),
        headers={'content-type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=LOCAL_LLM_TIMEOUT) as resp:
            body = json.loads(resp.read())
        msg = body['choices'][0]['message']
        # Qwen3 thinking mode puts output in reasoning_content; content is empty.
        # /no_think in system prompt prevents this, but fall back just in case.
        text = (msg.get('content') or msg.get('reasoning_content') or '').strip()
        # Strip markdown fences if model wraps in them
        if text.startswith('```'):
            text = text.split('```')[1]
            if text.startswith('json'):
                text = text[4:]
        parsed = json.loads(text)
        # Guard against model returning a JSON array instead of an object
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else None
        return parsed
    except Exception as e:
        print(f'[WATCHER] local LLM error: {e}')
        return None


def _box_frac_to_pixels(box_frac: list, frame_h: int, frame_w: int) -> list:
    """Convert [cx, cy, w, h] fractions to [x1, y1, x2, y2] pixels."""
    cx, cy, bw, bh = box_frac
    x1 = int((cx - bw / 2) * frame_w)
    y1 = int((cy - bh / 2) * frame_h)
    x2 = int((cx + bw / 2) * frame_w)
    y2 = int((cy + bh / 2) * frame_h)
    return [max(0, x1), max(0, y1), min(frame_w - 1, x2), min(frame_h - 1, y2)]


# ── main loop ─────────────────────────────────────────────────────────────────

def main():
    print('[WATCHER] Local vision watcher starting (Qwen3.5-4B-Vision @ localhost:9337)')
    print(f'[WATCHER] polling every {POLL_S}s | trigger cooldown {TRIGGER_COOLDOWN_S}s')

    last_trigger = 0.0
    poll_count   = 0
    trigger_count = 0

    os.makedirs(os.path.dirname(TRIGGER_PATH) or '.', exist_ok=True)

    while True:
        try:
            time.sleep(POLL_S)
            poll_count += 1

            # Don't flood triggers if orbit just finished
            if time.time() - last_trigger < TRIGGER_COOLDOWN_S:
                print(f'[WATCHER] cooldown active, skipping poll #{poll_count}')
                continue

            frame = _fetch_frame()
            if frame is None:
                continue

            h, w = frame.shape[:2]
            known = _load_class_names()
            b64   = _frame_to_b64(frame)

            print(f'[WATCHER] poll #{poll_count} — asking Qwen3.5-Vision '
                  f'({len(known)} known classes)...')
            result = _ask_local_vision(b64, known)

            if result is None:
                continue

            if not result.get('found'):
                print(f'[WATCHER] nothing novel this frame')
                continue

            label       = result.get('label', 'unknown_vision')
            description = result.get('description', '')
            box_frac    = result.get('box_frac', [0.5, 0.5, 0.3, 0.3])

            # Sanity-check box area
            area = box_frac[2] * box_frac[3]
            if area < MIN_AREA_FRAC:
                print(f'[WATCHER] "{label}" box too small ({area:.3f}) — skip')
                continue

            pixel_box = _box_frac_to_pixels(box_frac, h, w)

            trigger = {
                'label':       label,
                'description': description,
                'box':         pixel_box,
                'box_frac':    box_frac,
                'conf':        0.25,    # synthetic confidence for runtime.py
                'ts':          time.time(),
                'source':      'vision_watcher',
            }

            with open(TRIGGER_PATH, 'w') as f:
                json.dump(trigger, f)

            last_trigger  = time.time()
            trigger_count += 1
            print(f'[WATCHER] ★ trigger #{trigger_count}: "{label}" — {description}')
            print(f'[WATCHER]   box={pixel_box}  area={area:.3f}')

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f'[WATCHER] loop error: {e}')
            time.sleep(5)

    print(f'\n[WATCHER] exited — {poll_count} polls, {trigger_count} triggers fired')


if __name__ == '__main__':
    main()
