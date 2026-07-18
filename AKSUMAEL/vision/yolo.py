# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — YOLO Detector                      ║
# ║  Detects objects; flags unknowns for user labeling  ║
# ╚══════════════════════════════════════════════════════╝

import json
import os
import config


class YOLODetector:
    def __init__(self):
        self.model = None
        self.label_db = {}        # user-taught labels: box_hash → label
        self.unknown_queue = []   # boxes below confidence threshold
        self._load_model()
        self._load_label_db()

    def _load_model(self):
        try:
            import torch
            from ultralytics import YOLO
            self._device = 0 if torch.cuda.is_available() else 'cpu'
            self.model = YOLO(config.YOLO_MODEL)
            self.model.to(self._device)
            _dev_name = torch.cuda.get_device_name(0) if self._device == 0 else 'CPU'
            print(f'[YOLO] loaded {config.YOLO_MODEL} → {_dev_name}')
        except Exception as e:
            print(f'[YOLO] failed to load: {e} — running without YOLO')

    def _vram_headroom_ok(self) -> bool:
        """False when free VRAM is below config.YOLO_MIN_FREE_VRAM_MB. The
        GPU here is shared with an external llama-server process that holds
        a big static allocation, so headroom can get tight without this
        process's own allocator ever seeing an OOM coming."""
        try:
            import torch
            free_bytes, _total = torch.cuda.mem_get_info(self._device)
            if free_bytes < config.YOLO_MIN_FREE_VRAM_MB * 1024 * 1024:
                print(f'[YOLO] low VRAM headroom ({free_bytes / 1024**2:.0f}MB free) — skipping this tick')
                return False
            return True
        except Exception:
            return True  # can't check — don't block detection over it

    def reload_weights(self, path: str = None):
        """Hot-swap YOLO model weights. Call after retraining completes."""
        import config as cfg
        weights = path or cfg.YOLO_MODEL
        try:
            from ultralytics import YOLO
            self.model = YOLO(weights)
            print(f'[YOLO] hot-reloaded weights from {weights}')
            return True
        except Exception as e:
            print(f'[YOLO] reload failed: {e}')
            return False

    def _load_label_db(self):
        path = config.YOLO_LABEL_DB
        if os.path.exists(path):
            try:
                with open(path) as f:
                    self.label_db = json.load(f)
                print(f'[YOLO] loaded {len(self.label_db)} user labels')
            except Exception as e:
                print(f'[YOLO] label DB load error: {e}')

    def _save_label_db(self):
        path = config.YOLO_LABEL_DB
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, 'w') as f:
                json.dump(self.label_db, f, indent=2)
        except Exception as e:
            print(f'[YOLO] label DB save error: {e}')

    def detect(self, frame) -> list:
        """
        Run YOLO detection. Returns list of object dicts.
        Objects below YOLO_CONF_THRESHOLD are marked 'unknown'
        and added to unknown_queue for user labeling.
        """
        if self.model is None or frame is None:
            return []
        if self._device == 0 and not self._vram_headroom_ok():
            return []
        try:
            # conf= must be passed here — without it, Ultralytics applies
            # its own internal default (0.25) to decide which boxes even
            # reach results.boxes, before config.YOLO_CONF_THRESHOLD below
            # ever sees them. Tuning YOLO_CONF_THRESHOLD alone (as a filter
            # applied after this call) does nothing for anything Ultralytics
            # already dropped — see 2026-07-15, trees clearly visible in a
            # captured frame but never appearing in detections at all.
            results = self.model(frame, verbose=False, conf=config.YOLO_CONF_THRESHOLD)[0]
            out = []
            for b in results.boxes:
                conf  = round(float(b.conf), 2)
                box   = [round(float(x), 1) for x in b.xyxy[0].tolist()]
                cls   = int(b.cls)
                label = results.names[cls]

                # Check user label DB
                box_key = self._box_key(box)
                user_label = self.label_db.get(box_key)

                obj = {
                    'cls':        cls,
                    'label':      user_label or label,
                    'conf':       conf,
                    'box':        box,        # [x1, y1, x2, y2]
                    'user_label': user_label is not None,
                    'unknown':    conf < config.YOLO_CONF_THRESHOLD,
                }

                # Queue for user labeling if below threshold
                if obj['unknown'] and not user_label:
                    self._add_unknown(obj)

                out.append(obj)
            return out
        except Exception as e:
            print(f'[YOLO] detect error: {e}')
            if self._device == 0:
                try:
                    import torch
                    torch.cuda.empty_cache()
                except Exception:
                    pass
            return []

    def teach_label(self, box: list, label: str):
        """
        User assigns a label to a bounding box.
        Persists to label DB immediately.
        """
        key = self._box_key(box)
        self.label_db[key] = label.strip().lower()
        self._save_label_db()
        # Remove from unknown queue
        self.unknown_queue = [u for u in self.unknown_queue
                              if self._box_key(u['box']) != key]
        print(f'[YOLO] label saved: {label} → {key}')

    def pop_unknown(self):
        """Return and remove the oldest unknown object, or None."""
        return self.unknown_queue.pop(0) if self.unknown_queue else None

    def has_unknowns(self) -> bool:
        return len(self.unknown_queue) > 0

    def _add_unknown(self, obj: dict):
        """Add to unknown queue, avoid duplicates."""
        key = self._box_key(obj['box'])
        existing = [self._box_key(u['box']) for u in self.unknown_queue]
        if key not in existing:
            self.unknown_queue.append(obj)

    @staticmethod
    def _box_key(box: list) -> str:
        """Stable string key for a bounding box (rounded to 10px grid)."""
        rounded = [round(v / 10) * 10 for v in box]
        return '_'.join(str(int(v)) for v in rounded)
