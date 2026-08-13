# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — YOLO Detector                      ║
# ║  Detects objects; flags unknowns for user labeling  ║
# ╚══════════════════════════════════════════════════════╝

import json
import os
import config

# HUD bars are burned into a fixed screen position by the game itself, so a
# detection with one of these labels is only plausible in its own known
# sub-region of the frame — see memory/hud_reader.py's HUD_ROW_Y_FRAC
# (0.86-0.94) / HEALTH_X_FRAC / HUNGER_X_FRAC for the same convention
# applied to raw-pixel health/hunger reading, and tools/label_empty_surveys.py
# for the documented layout (hotbar bottom-center, health bottom-left,
# hunger bottom-right, xp bar just above hotbar, armor above health).
# Anything labeled this way but sitting outside its region is either the
# model mistaking a background pattern for a HUD bar (seen 2026-07-19: a
# ghost "hotbar" box firing at 0.75 conf on background terrain mid-screen —
# well above YOLO_CONF_THRESHOLD, so it wasn't caught by the
# unknown-confidence path) or one HUD bar mislabeled as a neighboring one
# (e.g. armor_bar tagged hotbar) — a single shared Y-band across all five
# labels doesn't catch that second case since they all sit in the same
# bottom quarter of the frame.
_HUD_BAR_REGIONS = {
    'hotbar':     {'x': (0.30, 0.70), 'y': (0.93, 1.00)},
    'health_bar': {'x': (0.20, 0.49), 'y': (0.86, 0.94)},
    'hunger_bar': {'x': (0.51, 0.80), 'y': (0.86, 0.94)},
    'armor_bar':  {'x': (0.20, 0.49), 'y': (0.78, 0.86)},
    'xp_bar':     {'x': (0.30, 0.70), 'y': (0.88, 0.93)},
}
_HUD_BAR_LABELS = set(_HUD_BAR_REGIONS)

# Passive/hostile mobs are multi-block entities — a sheep, cow or creeper
# at any reasonable range will be at least 40 px in its larger dimension.
# Flowers, tall grass, and other small decorations can fire the same labels
# at tiny box sizes (≤30 px).  Reject mob detections whose *larger* side is
# below this threshold to suppress false-positives from small decor items.
_MOB_LABELS = {
    'sheep', 'sheep_wool', 'cow', 'pig', 'chicken',
    'zombie', 'skeleton', 'spider', 'creeper', 'enderman',
}
_MOB_MIN_SIDE_PX = 35   # smaller dimension must be at least this many pixels


class YOLODetector:
    def __init__(self):
        self.model = None
        self._ort_session = None   # onnxruntime CPU fallback
        self.label_db = {}        # user-taught labels: box_hash → label
        self.unknown_queue = []   # boxes below confidence threshold
        self._load_model()
        self._load_onnx_fallback()
        self._load_label_db()

    def _load_model(self):
        try:
            import torch
            self._device = 0 if torch.cuda.is_available() else 'cpu'
            _dev_name = torch.cuda.get_device_name(0) if self._device == 0 else 'CPU'
            if getattr(config, 'YOLO_USE_WORLD', False):
                from ultralytics import YOLOWorld
                self.model = YOLOWorld(config.YOLO_WORLD_MODEL)
                self.model.set_classes(config.YOLO_WORLD_CLASSES)
                self.model.to(self._device)
                print(f'[YOLO] YOLO-World loaded ({len(config.YOLO_WORLD_CLASSES)} classes) → {_dev_name}')
            else:
                from ultralytics import YOLO
                self.model = YOLO(config.YOLO_MODEL)
                self.model.to(self._device)
                print(f'[YOLO] loaded {config.YOLO_MODEL} → {_dev_name}')
        except Exception as e:
            print(f'[YOLO] failed to load: {e} — running without YOLO')

    def _load_onnx_fallback(self):
        """Load ONNX model via onnxruntime for CPU fallback when GPU VRAM is low."""
        self._ort_session = None
        if getattr(config, 'YOLO_USE_WORLD', False):
            return   # YOLO-World uses the world model directly; no retrained ONNX to load
        onnx_path = config.YOLO_MODEL.replace('.pt', '.onnx')
        if not os.path.exists(onnx_path):
            print(f'[YOLO] no ONNX fallback at {onnx_path} — skipping will stay as-is')
            return
        try:
            import onnxruntime as ort
            sess_opts = ort.SessionOptions()
            sess_opts.inter_op_num_threads = 4
            sess_opts.intra_op_num_threads = 4
            self._ort_session = ort.InferenceSession(
                onnx_path, sess_options=sess_opts,
                providers=['CPUExecutionProvider'],
            )
            self._ort_input_name = self._ort_session.get_inputs()[0].name
            # Output shape [1, nc+4, 8400] — nc derived at load time
            out_shape = self._ort_session.get_outputs()[0].shape
            self._ort_nc = out_shape[1] - 4
            print(f'[YOLO] ONNX CPU fallback ready: {onnx_path} (nc={self._ort_nc})')
        except Exception as e:
            print(f'[YOLO] ONNX fallback load error: {e}')
            self._ort_session = None

    def _detect_cpu(self, frame) -> list:
        """Run ONNX CPU inference — called when GPU VRAM is too low for PyTorch."""
        if self._ort_session is None:
            return []
        import numpy as np
        import cv2 as _cv2
        try:
            h0, w0 = frame.shape[:2]
            imgsz = 320
            r = min(imgsz / h0, imgsz / w0)
            nh, nw = int(h0 * r), int(w0 * r)
            resized = _cv2.resize(frame, (nw, nh))
            top, left = (imgsz - nh) // 2, (imgsz - nw) // 2
            blob_img = np.zeros((imgsz, imgsz, 3), dtype=np.uint8)
            blob_img[top:top + nh, left:left + nw] = resized
            # BGR→RGB, HWC→CHW, normalise
            inp = blob_img[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
            inp = inp[np.newaxis]  # [1, 3, 320, 320]

            raw = self._ort_session.run(None, {self._ort_input_name: inp})[0]  # [1, nc+4, 8400]
            preds = raw[0].T  # [8400, nc+4]

            class_scores = preds[:, 4:]
            max_scores   = class_scores.max(axis=1)
            cls_ids      = class_scores.argmax(axis=1)
            boxes_xywh   = preds[:, :4]

            mask = max_scores >= config.YOLO_CONF_THRESHOLD
            boxes_xywh = boxes_xywh[mask]
            max_scores  = max_scores[mask]
            cls_ids     = cls_ids[mask]
            if len(boxes_xywh) == 0:
                return []

            # centre-xywh → xyxy, undo letterbox
            x1 = (boxes_xywh[:, 0] - boxes_xywh[:, 2] / 2 - left) / r
            y1 = (boxes_xywh[:, 1] - boxes_xywh[:, 3] / 2 - top)  / r
            x2 = (boxes_xywh[:, 0] + boxes_xywh[:, 2] / 2 - left) / r
            y2 = (boxes_xywh[:, 1] + boxes_xywh[:, 3] / 2 - top)  / r
            boxes_xyxy = np.stack([x1, y1, x2, y2], axis=1)

            indices = _cv2.dnn.NMSBoxes(
                boxes_xyxy.tolist(), max_scores.tolist(),
                config.YOLO_CONF_THRESHOLD, 0.45,
            )
            if len(indices) == 0:
                return []

            names = self.model.names if self.model else {}
            out = []
            for i in (indices.flatten() if hasattr(indices, 'flatten') else indices):
                box   = [round(float(v), 1) for v in boxes_xyxy[i]]
                conf  = round(float(max_scores[i]), 2)
                cls   = int(cls_ids[i])
                label = names.get(cls, str(cls)).replace(' ', '_')

                if label in _HUD_BAR_LABELS:
                    cx_frac = ((box[0] + box[2]) / 2) / w0
                    cy_frac = ((box[1] + box[3]) / 2) / h0
                    region  = _HUD_BAR_REGIONS[label]
                    if not (region['x'][0] <= cx_frac <= region['x'][1]
                            and region['y'][0] <= cy_frac <= region['y'][1]):
                        continue

                # Mob size gate (same as GPU path — see _MOB_MIN_SIDE_PX)
                if label in _MOB_LABELS:
                    bw = box[2] - box[0]
                    bh = box[3] - box[1]
                    if min(bw, bh) < _MOB_MIN_SIDE_PX:
                        continue

                box_key    = self._box_key(box)
                user_label = self.label_db.get(box_key)
                obj = {
                    'cls':        cls,
                    'label':      user_label or label,
                    'conf':       conf,
                    'box':        box,
                    'user_label': user_label is not None,
                    'unknown':    conf < config.YOLO_CONF_THRESHOLD,
                    'track_id':   None,  # no ByteTrack in CPU path
                }
                if obj['unknown'] and not user_label:
                    self._add_unknown(obj)
                out.append(obj)

            print(f'[YOLO] CPU fallback: {len(out)} det(s)')
            return out
        except Exception as e:
            print(f'[YOLO] CPU detect error: {e}')
            return []

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
        """Hot-swap YOLO model weights (GPU + ONNX CPU fallback). Call after retraining.
        In YOLO-World mode this refreshes the class list instead (no weights to swap)."""
        import config as cfg
        if getattr(cfg, 'YOLO_USE_WORLD', False):
            if self.model and hasattr(self.model, 'set_classes'):
                self.model.set_classes(cfg.YOLO_WORLD_CLASSES)
                print(f'[YOLO] YOLO-World class list refreshed ({len(cfg.YOLO_WORLD_CLASSES)} classes)')
            return True
        weights = path or cfg.YOLO_MODEL
        ok = True
        try:
            from ultralytics import YOLO
            self.model = YOLO(weights)
            print(f'[YOLO] hot-reloaded weights from {weights}')
        except Exception as e:
            print(f'[YOLO] reload failed: {e}')
            ok = False
        # Also rebuild the ONNX CPU session from the newly exported .onnx
        self._load_onnx_fallback()
        return ok

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

    def detect(self, frame, track: bool = False) -> list:
        """
        Run YOLO detection. Returns list of object dicts.
        Objects below YOLO_CONF_THRESHOLD are marked 'unknown'
        and added to unknown_queue for user labeling.

        track=True runs Ultralytics' .track(persist=True) instead of plain
        inference, so ByteTrack assigns a persistent 'track_id' per object
        (see vision/target_lock.py's TargetLock, which HUNT uses to hold a
        lock on one mob across ticks instead of re-detecting from scratch).
        Every other caller (EXPLORE/MINE/etc.) leaves this False and gets
        the original untracked behavior.
        """
        if self.model is None or frame is None:
            return []
        if self._device == 0 and not self._vram_headroom_ok():
            return self._detect_cpu(frame)
        try:
            # conf= must be passed here — without it, Ultralytics applies
            # its own internal default (0.25) to decide which boxes even
            # reach results.boxes, before config.YOLO_CONF_THRESHOLD below
            # ever sees them. Tuning YOLO_CONF_THRESHOLD alone (as a filter
            # applied after this call) does nothing for anything Ultralytics
            # already dropped — see 2026-07-15, trees clearly visible in a
            # captured frame but never appearing in detections at all.
            if track:
                results = self.model.track(frame, persist=True, verbose=False,
                                            conf=config.YOLO_CONF_THRESHOLD)[0]
            else:
                results = self.model(frame, verbose=False, conf=config.YOLO_CONF_THRESHOLD)[0]
            frame_h = frame.shape[0]
            out = []
            for b in results.boxes:
                conf  = round(float(b.conf), 2)
                box   = [round(float(x), 1) for x in b.xyxy[0].tolist()]
                cls   = int(b.cls)
                label = results.names[cls].replace(' ', '_')   # normalise YOLO-World labels
                track_id = int(b.id) if getattr(b, 'id', None) is not None else None

                # Check user label DB
                box_key = self._box_key(box)
                user_label = self.label_db.get(box_key)

                hud_label = user_label or label
                if hud_label in _HUD_BAR_LABELS:
                    frame_w = frame.shape[1]
                    cx_frac = ((box[0] + box[2]) / 2) / frame_w
                    cy_frac = ((box[1] + box[3]) / 2) / frame_h
                    region  = _HUD_BAR_REGIONS[hud_label]
                    if not (region['x'][0] <= cx_frac <= region['x'][1]
                            and region['y'][0] <= cy_frac <= region['y'][1]):
                        continue   # outside this bar's known region — noise or mislabel

                # Mob size gate: mobs are multi-block entities; tiny boxes
                # (< _MOB_MIN_SIDE_PX on their smaller side) are almost
                # certainly flowers, tall grass, or other small decor items
                # mislabelled as mobs by the model.
                eff_label = user_label or label
                if eff_label in _MOB_LABELS:
                    bw = box[2] - box[0]
                    bh = box[3] - box[1]
                    if min(bw, bh) < _MOB_MIN_SIDE_PX:
                        continue

                obj = {
                    'cls':        cls,
                    'label':      user_label or label,
                    'conf':       conf,
                    'box':        box,        # [x1, y1, x2, y2]
                    'user_label': user_label is not None,
                    'unknown':    conf < config.YOLO_CONF_THRESHOLD,
                    'track_id':   track_id,   # set only when track=True; else None
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
