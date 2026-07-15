# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Adaptive Self-Labeling Queue               ║
# ║  Low-confidence YOLO detections -> mesh-llm labels ->  ║
# ║  background retrain, per env_id.                       ║
# ╚══════════════════════════════════════════════════════╝
#
# This is the mechanism that lets a fresh core/env_profile.py bootstrap
# profile (yolo_classes=[], no fine-tuned weights) turn into a usable
# detector without a human sitting down to label anything: every tick
# where YOLO comes back under-confident about a region, crop it, ask the
# on-box mesh-llm what it thinks it's looking at, and once enough labels
# pile up, ask for a retrain. Distinct from vision.yolo.YOLODetector's own
# unknown_queue (which exists for the interactive human-labeling UI in
# ui/labeling.py) — this one is autonomous and scoped per environment.

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import cv2

import config
from core.llm_router import route_llm_call, frame_to_b64

QUEUE_ROOT         = "data/label_queue"
LOW_CONF_THRESHOLD = 0.4
RETRAIN_BATCH_SIZE = 50

_LABEL_PROMPT = ("What is this UI element or object? "
                 "Give a short label (1-3 words), nothing else.")


class LabelQueue:
    """Per-env-id queue of low-confidence YOLO crops awaiting a label.

    Usage from the runtime loop::

        queue.maybe_queue(frame, objects, env_id)          # every tick
        queue.label_pending(env_id)                        # every N ticks
        if queue.should_retrain(env_id):
            queue.trigger_retrain(env_id, retrain_fn=...)  # every N ticks
    """

    def __init__(self, root: str = QUEUE_ROOT,
                 conf_threshold: float = LOW_CONF_THRESHOLD,
                 retrain_batch_size: int = RETRAIN_BATCH_SIZE):
        self.root               = root
        self.conf_threshold     = conf_threshold
        self.retrain_batch_size = retrain_batch_size
        self._lock       = threading.Lock()
        self._retraining = set()   # env_ids with a retrain currently in flight

    # ── Paths ──────────────────────────────────────────────────
    def _env_dir(self, env_id: str) -> str:
        return os.path.join(self.root, env_id)

    def _crops_dir(self, env_id: str) -> str:
        return os.path.join(self._env_dir(env_id), 'crops')

    def _labels_path(self, env_id: str) -> str:
        return os.path.join(self._env_dir(env_id), 'labels.jsonl')

    def _state_path(self, env_id: str) -> str:
        return os.path.join(self._env_dir(env_id), 'state.json')

    def _meta_path(self, env_id: str, stem: str) -> str:
        return os.path.join(self._crops_dir(env_id), f'{stem}.json')

    # ── Queuing (hot path — called every tick with this tick's detections) ──
    def maybe_queue(self, frame, objects: list, env_id: str) -> int:
        """Crop and queue every detection in `objects` below
        self.conf_threshold. Returns how many were queued this call."""
        if frame is None or not objects:
            return 0
        low_conf = [o for o in objects if o.get('conf', 1.0) < self.conf_threshold]
        if not low_conf:
            return 0

        crops_dir = self._crops_dir(env_id)
        os.makedirs(crops_dir, exist_ok=True)
        h, w = frame.shape[:2]
        queued = 0

        for i, obj in enumerate(low_conf):
            box = obj.get('box')
            if not box or len(box) != 4:
                continue
            x1, y1 = max(0, int(box[0])), max(0, int(box[1]))
            x2, y2 = min(w, int(box[2])), min(h, int(box[3]))
            if x2 <= x1 or y2 <= y1:
                continue

            ts   = time.time()
            stem = f'{int(ts * 1000)}_{i}'
            img_path = os.path.join(crops_dir, f'{stem}.jpg')
            try:
                cv2.imwrite(img_path, frame[y1:y2, x1:x2])
            except Exception as e:
                print(f'[LABEL_QUEUE] crop write failed: {e}')
                continue

            meta = {
                'stem': stem, 'image': img_path, 'box': box,
                'conf': obj.get('conf'), 'yolo_label': obj.get('label'),
                'queued_at': ts, 'labeled': False,
            }
            with open(self._meta_path(env_id, stem), 'w') as f:
                json.dump(meta, f)
            queued += 1

        if queued:
            print(f'[LABEL_QUEUE] {env_id}: queued {queued} low-confidence crop(s)')
        return queued

    def _pending_meta(self, env_id: str) -> list:
        crops_dir = self._crops_dir(env_id)
        if not os.path.isdir(crops_dir):
            return []
        pending = []
        for fn in sorted(os.listdir(crops_dir)):
            if not fn.endswith('.json'):
                continue
            try:
                with open(os.path.join(crops_dir, fn)) as f:
                    meta = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            if not meta.get('labeled'):
                pending.append(meta)
        return pending

    def _mark_labeled(self, env_id: str, stem: str, skipped: bool = False):
        path = self._meta_path(env_id, stem)
        try:
            with open(path) as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        meta['labeled'] = True
        if skipped:
            meta['skipped'] = True
        with open(path, 'w') as f:
            json.dump(meta, f)

    def _append_label(self, env_id: str, record: dict):
        os.makedirs(self._env_dir(env_id), exist_ok=True)
        with open(self._labels_path(env_id), 'a') as f:
            f.write(json.dumps(record) + '\n')

    # ── LLM labeling (called periodically, off the hot path) ───
    def label_pending(self, env_id: str, max_items: int = 10) -> int:
        """Send up to `max_items` pending crops to mesh-llm for a short
        label, append results to labels.jsonl, and mark them labeled.
        Each call is one or more blocking LLM round-trips — call this every
        N ticks, not every tick. Returns how many crops were newly labeled.
        """
        pending = self._pending_meta(env_id)[:max_items]
        if not pending:
            return 0

        labeled = 0
        for meta in pending:
            img_path = meta['image']
            frame = cv2.imread(img_path) if os.path.exists(img_path) else None
            if frame is None:
                self._mark_labeled(env_id, meta['stem'], skipped=True)
                continue

            raw, provider = route_llm_call(
                _LABEL_PROMPT, max_tokens=20, images=[frame_to_b64(frame)],
                timeout=config.LOCAL_LLM_TIMEOUT)
            if raw is None:
                continue   # mesh-llm unreachable — leave pending, retry next pass

            label = raw.strip().strip('."\'').lower()
            self._append_label(env_id, {**meta, 'label': label,
                                         'labeled_at': time.time(), 'provider': provider})
            self._mark_labeled(env_id, meta['stem'])
            labeled += 1

        if labeled:
            print(f'[LABEL_QUEUE] {env_id}: labeled {labeled} crop(s) via mesh-llm')
        return labeled

    # ── Retrain trigger ────────────────────────────────────────
    def _state(self, env_id: str) -> dict:
        path = self._state_path(env_id)
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                pass
        return {'labels_at_last_retrain': 0, 'last_retrain': 0.0}

    def _save_state(self, env_id: str, state: dict):
        os.makedirs(self._env_dir(env_id), exist_ok=True)
        with open(self._state_path(env_id), 'w') as f:
            json.dump(state, f)

    def label_count(self, env_id: str) -> int:
        path = self._labels_path(env_id)
        if not os.path.exists(path):
            return 0
        with open(path) as f:
            return sum(1 for _ in f)

    def should_retrain(self, env_id: str) -> bool:
        with self._lock:
            if env_id in self._retraining:
                return False
        new_since = self.label_count(env_id) - self._state(env_id).get('labels_at_last_retrain', 0)
        return new_since >= self.retrain_batch_size

    def trigger_retrain(self, env_id: str, retrain_fn=None) -> bool:
        """Kick off a background retrain for `env_id` once
        retrain_batch_size new labels have accumulated since the last one.

        `retrain_fn(env_id)` does the actual training work — defaults to
        self._launch_background_train, which hands off to
        tools/yolo_finetune.py's generic per-env trainer
        (train(env_id=...)) via a detached systemd-run scope, same as
        behaviors/auto_trainer.py does for the Minecraft training loop.
        Pass a different retrain_fn to override that (e.g. tests, or a
        hand-written adapter for env_id == config.ACTIVE_ENV). Returns True
        if a retrain was started (False if one was already in flight for
        this env_id).
        """
        with self._lock:
            if env_id in self._retraining:
                return False
            self._retraining.add(env_id)

        count_before = self.label_count(env_id)
        fn = retrain_fn or self._launch_background_train

        def _run():
            try:
                fn(env_id)
            finally:
                # Persist state before releasing the in-flight marker, so a
                # should_retrain() call that sees this env_id as no longer
                # in flight always sees the post-retrain count too.
                self._save_state(env_id, {'labels_at_last_retrain': count_before,
                                           'last_retrain': time.time()})
                with self._lock:
                    self._retraining.discard(env_id)

        threading.Thread(target=_run, daemon=True, name=f'retrain_{env_id[:16]}').start()
        print(f'[LABEL_QUEUE] {env_id}: retrain triggered ({count_before} labels accumulated)')
        return True

    def _launch_background_train(self, env_id: str):
        """Default retrain_fn — launch tools/yolo_finetune.py's `train`
        command for this env_id in its own transient systemd-run scope
        (background.slice), the same detachment pattern
        behaviors/auto_trainer.py uses: training shares the GPU/CUDA
        context with whatever's already running, so it needs to be a
        separate process, not just a background thread in this one."""
        try:
            repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            script    = os.path.join(repo_root, 'tools', 'yolo_finetune.py')
            log_path  = f'/tmp/aksumael_retrain_{env_id}.log'
            subprocess.Popen(
                ['systemd-run', '--user', '--scope', '--slice=background.slice',
                 sys.executable, script, 'train', '30', env_id],
                stdout=open(log_path, 'w'), stderr=subprocess.STDOUT,
                cwd=repo_root,
            )
            print(f'[LABEL_QUEUE] {env_id}: launched background training via systemd-run (log: {log_path})')
        except OSError as e:
            print(f'[LABEL_QUEUE] {env_id}: systemd-run launch failed ({e}) — writing retrain flag instead')
            self._write_retrain_flag(env_id, self.label_count(env_id))

    def _write_retrain_flag(self, env_id: str, label_count: int):
        os.makedirs(self._env_dir(env_id), exist_ok=True)
        flag_path = os.path.join(self._env_dir(env_id), 'retrain_requested.json')
        with open(flag_path, 'w') as f:
            json.dump({'env_id': env_id, 'label_count': label_count,
                       'requested_at': time.time()}, f)
        print(f'[LABEL_QUEUE] {env_id}: no retrain_fn wired up — wrote {flag_path}')
