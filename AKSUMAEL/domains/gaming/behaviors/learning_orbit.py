# ╔══════════════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Active Learner (consolidated)                     ║
# ║                                                               ║
# ║  One class, one place.  Combines:                             ║
# ║    • Qwen3.5-4B-Vision watcher (background thread)            ║
# ║    • Approach phase (walk to calibrated distance)             ║
# ║    • 360° orbit capture (24 frames @ 15° each)                ║
# ║    • Auto-register new class in data.yaml                     ║
# ║    • Quick 10-epoch retrain after every orbit                 ║
# ║                                                               ║
# ║  Watcher polls the frame server every POLL_S seconds.         ║
# ║  When Qwen sees something not in the class list it sets       ║
# ║  self._pending_trigger.  The main runtime loop calls          ║
# ║  has_pending() each tick and, when safe, calls run().         ║
# ║                                                               ║
# ║  Orbit math                                                   ║
# ║  ──────────                                                   ║
# ║  Strafing A + rotating right (mouse dx > 0) = clockwise       ║
# ║  circle always facing inward toward the object.               ║
# ║  ORBIT_LOOK_PX = pixels for ~15°. Tune if object drifts.     ║
# ║  ORBIT_STEP_S  = strafe duration per step = orbital radius.   ║
# ╚══════════════════════════════════════════════════════════════╝

import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

import cv2

import config

# Serialize all Qwen calls — llama-server handles one request at a time
_qwen_lock = threading.Lock()
import numpy as np

# ── orbit parameters ───────────────────────────────────────────────────────────
N_FRAMES         = 24          # one full rotation  (360° / 24 = 15° per step)
ORBIT_LOOK_PX    = 40          # mouse pixels per step ≈ 15°
ORBIT_STEP_S     = 1.1         # seconds of strafe per step (controls radius)
SETTLE_S         = 0.15        # settle after each look before capture

# ── approach parameters ────────────────────────────────────────────────────────
TARGET_BBOX_FRAC = 0.22        # desired bbox_height / frame_height
APPROACH_STEP_S  = 0.3         # seconds of walk per correction
APPROACH_MAX_S   = 8.0         # hard limit on approach
APPROACH_TOL     = 0.06        # ± tolerance

# ── watcher parameters ─────────────────────────────────────────────────────────
POLL_S             = 20        # seconds between Qwen polls
TRIGGER_COOLDOWN_S = 240       # min gap between triggers (orbit takes ~38s)
MIN_TRIGGER_CONF   = 0.12      # filter YOLO noise below this
MIN_BOX_AREA       = 0.01      # Qwen bbox must be at least 1% of frame

# ── local LLM ─────────────────────────────────────────────────────────────────
LOCAL_LLM_BASE    = 'http://localhost:9337/v1'
LOCAL_LLM_TIMEOUT = 40

# ── paths ──────────────────────────────────────────────────────────────────────
_REPO    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IMG_DIR = os.path.join(_REPO, 'data', 'yolo_dataset', 'images', 'train')
_LBL_DIR = os.path.join(_REPO, 'data', 'yolo_dataset', 'labels', 'train')
_FRAME_URL = 'http://localhost:8765/frame'
_DATA_YAML = os.path.join(_REPO, 'data', 'yolo_dataset', 'data.yaml')


# ══════════════════════════════════════════════════════════════════════════════
#  Qwen vision helpers
# ══════════════════════════════════════════════════════════════════════════════

def _load_class_names() -> list:
    if not os.path.exists(_DATA_YAML):
        return []
    try:
        import yaml
        with open(_DATA_YAML) as f:
            data = yaml.safe_load(f) or {}
        names = data.get('names', [])
        if isinstance(names, dict):
            return [names[k] for k in sorted(names, key=int)]
        return list(names)
    except Exception:
        return []


def _fetch_frame() -> np.ndarray | None:
    try:
        with urllib.request.urlopen(_FRAME_URL, timeout=4) as resp:
            data = resp.read()
        arr = np.frombuffer(data, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:
        return None


def _frame_to_b64(frame: np.ndarray) -> str:
    _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return base64.b64encode(buf.tobytes()).decode()


def _ask_qwen(b64_img: str, known: list) -> dict | None:
    # Skip vision call if the loaded model is text-only (llm_router latches
    # _model_rejects_images on first HTTP 500 from an image payload).
    # This silences the recurring [LEARNER] Qwen error: HTTP Error 500 seen
    # when Qwen3-4B (text-only+LoRA) is loaded instead of Qwen3-4B-Vision.
    try:
        from core.llm_router import _model_rejects_images as _mri
        if _mri:
            return None  # text-only model — vision watcher disabled silently
    except Exception:
        pass
    class_list = ', '.join(known[:80])
    system = (
        'You are a Minecraft computer vision assistant helping train a YOLO detector. '
        'You will be shown a Minecraft game frame. '
        'Identify any significant object, entity, or block clearly visible in the '
        'gameplay area (ignore HUD: hearts, hunger, hotbar, crosshair). '
        'Respond ONLY with a single JSON object — no prose, no markdown fences. '
        'If you find a novel object not in the known class list: '
        '{"found": true, "label": "snake_case_name", "description": "one line", '
        '"box_frac": [cx, cy, width, height]} '
        'where box_frac values are 0.0-1.0 fractions of image dimensions. '
        'If everything is already in the known list: {"found": false} '
        'Only report if you are confident it is a real distinct game object.'
    )
    user_text = (
        f'Known training classes: [{class_list}]\n\n'
        f'What novel Minecraft object do you see that is NOT in this list? '
        f'If nothing new is visible, respond {{"found": false}}.'
    )
    payload = {
        'model':                 'auto',
        'max_tokens':            400,
        'temperature':           0.1,
        'chat_template_kwargs':  {'enable_thinking': False},  # disable Qwen3 chain-of-thought
        'messages': [
            {'role': 'system', 'content': system},
            {
                'role': 'user',
                'content': [
                    {'type': 'image_url',
                     'image_url': {'url': f'data:image/jpeg;base64,{b64_img}'}},
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
        with _qwen_lock:  # serialize — llama-server handles one request at a time
            with urllib.request.urlopen(req, timeout=LOCAL_LLM_TIMEOUT) as resp:
                body = json.loads(resp.read())
        msg = body['choices'][0]['message']
        # Qwen3 thinking mode puts output in reasoning_content; content is empty.
        # /no_think in system prompt prevents this, but fall back just in case.
        text = (msg.get('content') or msg.get('reasoning_content') or '').strip()
        if text.startswith('```'):
            text = text.split('```')[1]
            if text.startswith('json'):
                text = text[4:]
        return json.loads(text)
    except urllib.error.HTTPError as e:
        if e.code in (400, 422, 500):
            # Model rejected image content (text-only model loaded).
            # Latch the router flag so future calls skip immediately.
            try:
                import core.llm_router as _router
                _router._model_rejects_images = True
            except Exception:
                pass
            # Suppress the noisy error — this is expected with a text-only model.
            return None
        print(f'[LEARNER] Qwen HTTP error {e.code}: {e}')
        return None
    except Exception as e:
        print(f'[LEARNER] Qwen error: {e}')
        return None


def _box_frac_to_pixels(box_frac: list, h: int, w: int) -> list:
    cx, cy, bw, bh = box_frac
    x1 = int((cx - bw / 2) * w);  y1 = int((cy - bh / 2) * h)
    x2 = int((cx + bw / 2) * w);  y2 = int((cy + bh / 2) * h)
    return [max(0, x1), max(0, y1), min(w - 1, x2), min(h - 1, y2)]


# ══════════════════════════════════════════════════════════════════════════════
#  Main class
# ══════════════════════════════════════════════════════════════════════════════

class LearningOrbit:
    """
    Drop-in for the old split vision_watcher.py + learning_orbit.py pair.

    Usage in runtime.py:
        learning_orbiter = LearningOrbit(executor,
                                         capture_fn=lambda: pipeline.latest_raw_frame,
                                         auto_trainer=auto_trainer)
        learning_orbiter.start_watcher()   # call once after init

        # In main tick loop (EXPLORE state only):
        if learning_orbiter.has_pending() and not replayer.is_active():
            learning_orbiter.run_pending(frame)
    """

    def __init__(self, executor, capture_fn=None, auto_trainer=None):
        self._executor     = executor
        self._capture      = capture_fn
        self._auto_trainer = auto_trainer

        self._active          = False
        self._last_run        = 0.0
        self._pending_trigger = None   # dict or None
        self._lock            = threading.Lock()
        self._watcher_thread  = None

    # ── watcher control ───────────────────────────────────────────────────────

    def start_watcher(self):
        """Start background Qwen polling thread.  Call once after init."""
        if self._watcher_thread and self._watcher_thread.is_alive():
            return
        self._watcher_thread = threading.Thread(
            target=self._watcher_loop, daemon=True, name='learner-watcher')
        self._watcher_thread.start()
        print(f'[LEARNER] Qwen watcher started (poll={POLL_S}s, '
              f'cooldown={TRIGGER_COOLDOWN_S}s)')

    def _watcher_loop(self):
        time.sleep(5)   # let bot start up first
        while True:
            try:
                time.sleep(POLL_S)

                # Don't pile up triggers
                with self._lock:
                    if self._pending_trigger is not None:
                        continue
                    if self._active:
                        continue
                    if time.time() - self._last_run < TRIGGER_COOLDOWN_S:
                        continue

                frame = _fetch_frame()
                if frame is None:
                    continue

                known = _load_class_names()
                b64   = _frame_to_b64(frame)
                result = _ask_qwen(b64, known)

                if not result or not result.get('found'):
                    continue

                box_frac = result.get('box_frac', [0.5, 0.5, 0.3, 0.3])
                area = box_frac[2] * box_frac[3]
                if area < MIN_BOX_AREA:
                    print(f'[LEARNER] "{result.get("label")}" box too small — skip')
                    continue

                h, w  = frame.shape[:2]
                pixel_box = _box_frac_to_pixels(box_frac, h, w)
                trigger = {
                    'label':       result.get('label', 'unknown_vision'),
                    'description': result.get('description', ''),
                    'box':         pixel_box,
                    'box_frac':    box_frac,
                    'conf':        0.25,
                    'source':      'qwen_watcher',
                }
                with self._lock:
                    self._pending_trigger = trigger
                print(f'[LEARNER] ★ Qwen spotted "{trigger["label"]}" '
                      f'— {trigger["description"]}')

            except Exception as e:
                print(f'[LEARNER] watcher error: {e}')
                time.sleep(10)

    # ── runtime API ───────────────────────────────────────────────────────────

    def has_pending(self) -> bool:
        with self._lock:
            return self._pending_trigger is not None and not self._active

    def should_trigger(self, unknown: dict) -> bool:
        """For YOLO-sourced unknowns (legacy path)."""
        if self._active:
            return False
        conf = unknown.get('conf', 0.0)
        if conf < MIN_TRIGGER_CONF:
            return False
        if time.time() - self._last_run < TRIGGER_COOLDOWN_S:
            return False
        return True

    def trigger_from_yolo(self, unknown: dict):
        """Set a pending trigger from a YOLO low-conf detection."""
        with self._lock:
            if self._pending_trigger is None and not self._active:
                self._pending_trigger = {
                    'label': f'unknown_yolo',
                    'box':   unknown.get('box', [100, 100, 300, 300]),
                    'conf':  unknown.get('conf', 0.15),
                    'source': 'yolo',
                }

    def run_pending(self, fallback_frame) -> int:
        """Execute orbit for the pending trigger. Returns frames saved."""
        with self._lock:
            trigger = self._pending_trigger
            self._pending_trigger = None
            if trigger is None or self._active:
                return 0
            self._active = True
            self._last_run = time.time()

        saved = 0
        try:
            saved = self._do_orbit(trigger, fallback_frame)
        finally:
            with self._lock:
                self._active = False

        if saved > 0:
            if config.ENABLE_AUTO_RETRAIN:
                print(f'[LEARNER] orbit done — {saved} frames — triggering CPU mini-retrain')
                self._cpu_mini_retrain()
            else:
                print(f'[LEARNER] orbit done — {saved} frames saved (auto-retrain disabled, review before training)')

        return saved

    # ── orbit ─────────────────────────────────────────────────────────────────

    def _do_orbit(self, trigger: dict, fallback_frame) -> int:
        box  = trigger['box']
        label = trigger.get('label', 'unknown')
        description = trigger.get('description', '')

        # Use Qwen label if valid; fall back to box hash for yolo/unknown triggers
        try:
            from core.class_registry import get_or_add_class, normalize_class_name
            normalized = normalize_class_name(label)
            if normalized and not normalized.startswith('unknown'):
                class_name = normalized
            else:
                key = hashlib.sha1(
                    f'{box[0]:.0f},{box[1]:.0f},{box[2]:.0f},{box[3]:.0f}'.encode()
                ).hexdigest()[:6]
                class_name = f'unknown_{key}'
            class_id = get_or_add_class(class_name, source='learning_orbit')
        except Exception as e:  # noqa: BLE001
            print(f'[LEARNER] class registry error: {e}')
            return 0

        print(f'[LEARNER] starting orbit: "{label}" ({description}) '
              f'→ class "{class_name}" id={class_id}')

        try:
            self._approach(box, fallback_frame)
            saved = self._orbit_and_capture(class_id, class_name, box, fallback_frame)
        except Exception as e:
            print(f'[LEARNER] orbit error: {e}')
            self._stop_all_keys()
            return 0

        return saved

    def _approach(self, box: list, fallback_frame):
        deadline = time.time() + APPROACH_MAX_S
        frame    = self._fresh(fallback_frame)
        if frame is None:
            return
        fh = frame.shape[0]
        target_h = TARGET_BBOX_FRAC * fh
        box_h = box[3] - box[1]

        while time.time() < deadline:
            err = (box_h - target_h) / fh
            if abs(err) <= APPROACH_TOL:
                break
            self._key_timed('w' if err < 0 else 's', APPROACH_STEP_S)
            time.sleep(0.1)

        self._stop_all_keys()

    def _orbit_and_capture(self, class_id: int, class_name: str,
                            ref_box: list, fallback_frame) -> int:
        os.makedirs(_IMG_DIR, exist_ok=True)
        os.makedirs(_LBL_DIR, exist_ok=True)
        saved = 0

        for step in range(N_FRAMES):
            # Rotate right
            self._executor.execute({
                'look': {'dx': ORBIT_LOOK_PX, 'dy': 0},
                'source': 'learning_orbit',
            })
            # Strafe left for arc
            self._key_timed('a', ORBIT_STEP_S)
            time.sleep(SETTLE_S)

            frame = self._fresh(fallback_frame)
            if frame is not None:
                if self._save_frame(frame, class_id, class_name, ref_box, step):
                    saved += 1
                    print(f'[LEARNER] frame {saved}/{N_FRAMES} '
                          f'({(step+1)*360//N_FRAMES}°)')

        self._stop_all_keys()
        return saved

    def _save_frame(self, frame, class_id: int, class_name: str,
                    ref_box: list, step: int) -> bool:
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = ref_box
        x1, x2 = max(0, x1), min(w - 1, x2)
        y1, y2 = max(0, y1), min(h - 1, y2)
        cx = ((x1 + x2) / 2) / w;  cy = ((y1 + y2) / 2) / h
        bw = (x2 - x1) / w;        bh = (y2 - y1) / h

        stem = f'learn_{class_name}_{uuid.uuid4().hex[:8]}_{step:02d}'
        try:
            cv2.imwrite(os.path.join(_IMG_DIR, f'{stem}.jpg'), frame,
                        [cv2.IMWRITE_JPEG_QUALITY, 92])
            with open(os.path.join(_LBL_DIR, f'{stem}.txt'), 'w') as f:
                f.write(f'{class_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n')
            return True
        except Exception as e:
            print(f'[LEARNER] save error: {e}')
            return False

    # ── quick retrain (10 epochs, fires immediately after 24-frame orbit) ─────

    def _cpu_mini_retrain(self, epochs: int = 5):
        """Run a short CPU-only fine-tune in the background without stopping
        the aksumael service.

        CPU training doesn't compete with GPU inference (YOLO) or mesh-llm
        (Qwen), so the bot can keep running uninterrupted.  After training
        completes, a signal file triggers hot-reload of the new weights in the
        main runtime loop (no restart needed).
        """
        import pathlib
        lock = pathlib.Path('/tmp/aksumael_training.lock')
        if lock.exists():
            print('[LEARNER] training already in progress — skipping mini-retrain')
            return

        python = sys.executable
        script = os.path.join(_REPO, 'tools', 'yolo_finetune.py')
        log    = '/tmp/learner_retrain.log'

        try:
            proc = subprocess.Popen(
                [python, script, 'train', str(epochs), 'minecraft_survival', 'cpu'],
                stdout=open(log, 'w'), stderr=subprocess.STDOUT,
            )
            print(f'[LEARNER] CPU mini-retrain started (PID {proc.pid}, {epochs} epochs, log: {log})')
        except Exception as e:
            print(f'[LEARNER] mini-retrain launch error: {e}')
            return

        # Background thread waits for completion, re-exports ONNX, then signals
        def _wait_and_signal():
            proc.wait()
            if proc.returncode == 0:
                # Re-export ONNX so the CPU fallback session stays in sync
                try:
                    import config as _cfg
                    _pt = _cfg.YOLO_MODEL
                    _exp = subprocess.run(
                        [python, '-c',
                         f'from ultralytics import YOLO; '
                         f'm=YOLO("{_pt}"); '
                         f'm.export(format="onnx",imgsz=320,opset=12,dynamic=False,simplify=True)'],
                        capture_output=True, timeout=120,
                    )
                    if _exp.returncode == 0:
                        print('[LEARNER] ONNX re-exported after retrain')
                    else:
                        print(f'[LEARNER] ONNX export warning: {_exp.stderr[-200:].decode()}')
                except Exception as _e:
                    print(f'[LEARNER] ONNX export error (non-fatal): {_e}')
                pathlib.Path('/tmp/aksumael_yolo_reload').touch()
                print(f'[LEARNER] mini-retrain complete — reload signal written')
            else:
                print(f'[LEARNER] mini-retrain failed (exit {proc.returncode}) — check {log}')

        import threading
        threading.Thread(target=_wait_and_signal, daemon=True).start()

    # ── executor helpers ───────────────────────────────────────────────────────

    def _fresh(self, fallback):
        if self._capture:
            f = self._capture()
            if f is not None:
                return f
        return fallback

    def _key_timed(self, key: str, duration_s: float):
        self._executor.execute({'key_hold': 'down', 'key': key,
                                'source': 'learning_orbit'})
        time.sleep(duration_s)
        self._executor.execute({'key_hold': 'up',   'key': key,
                                'source': 'learning_orbit'})

    def _stop_all_keys(self):
        for key in ('w', 's', 'a', 'd'):
            try:
                self._executor.execute({'key_hold': 'up', 'key': key,
                                        'source': 'learning_orbit'})
            except Exception:
                pass
