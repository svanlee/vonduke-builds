# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Target Lock (HUNT precursor to ROCKET-1)  ║
# ╚══════════════════════════════════════════════════════╝
#
# Lightweight bbox-level target tracking for the HUNT state. Replaces the
# old "predict once, animal gone next tick, bail" behavior: once a mob is
# acquired, TargetLock holds onto it across a bounded run of missed
# detections (occlusion, a frame where YOLO's confidence dips, the mob
# stepping out of frame briefly) instead of dropping it on the very first
# miss.
#
# Detections passed in here are expected to already carry a 'track_id' key
# when available — that comes from the caller running Ultralytics in
# .track(persist=True) mode (see vision/yolo.py's YOLODetector.detect(...,
# track=True)) rather than plain .predict(). TargetLock itself never talks
# to the model; it just reads whatever track_id ByteTrack assigned and uses
# it as the primary match key, falling back to IoU when the ID is dropped.
#
# This is the bbox-tracking precursor to ROCKET-1-style (CVPR '25,
# github.com/CraftJarvis/ROCKET-1) visual-temporal context prompting, which
# uses SAM-2 pixel-level segmentation instead of boxes — see
# vision/visual_context_buffer.py for the (currently unimplemented)
# scaffold that later work will slot into.

LOCK_DROPOUT_TICKS      = 20    # consecutive misses tolerated before is_locked flips False
DEFAULT_LOOKAHEAD_TICKS = 3     # ticks of velocity extrapolation for predicted_centroid
IOU_MATCH_THRESHOLD     = 0.3   # minimum IoU to accept a track-ID-less fallback match


def _bbox_area(box) -> float:
    if not box or len(box) != 4:
        return 0.0
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _centroid(box):
    if not box or len(box) != 4:
        return None
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _iou(box_a, box_b) -> float:
    if not box_a or not box_b or len(box_a) != 4 or len(box_b) != 4:
        return 0.0
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    union = _bbox_area(box_a) + _bbox_area(box_b) - inter
    return inter / union if union > 0 else 0.0


class TargetLock:
    """
    Latches onto one detection and tracks it tick-to-tick.

    Usage (mirrors HUNT's tick shape)::

        lock = TargetLock()
        lock.acquire(detections, label_filter={'cow', 'sheep', 'pig', 'chicken'})
        ...
        matched = lock.update(detections)   # None on a missed tick
        if lock.is_locked:
            aim_at(matched['box'] if matched else lock.predicted_centroid)
        else:
            lock.drop()   # give up, re-enter EXPLORE/COLLECT etc.
    """

    def __init__(self, lock_dropout_ticks: int = LOCK_DROPOUT_TICKS,
                 lookahead_ticks: int = DEFAULT_LOOKAHEAD_TICKS,
                 iou_threshold: float = IOU_MATCH_THRESHOLD):
        self.lock_dropout_ticks = lock_dropout_ticks
        self.lookahead_ticks    = lookahead_ticks
        self.iou_threshold      = iou_threshold

        self.track_id                = None   # Ultralytics ByteTrack ID, or None
        self.label                   = None
        self.last_known_bbox         = None   # [x1, y1, x2, y2]
        self.last_known_centroid     = None    # (cx, cy)
        self.velocity                = (0.0, 0.0)  # centroid delta per tick
        self.consecutive_miss_count  = 0

        self._label_filter = None  # set of labels acquire() was called with

    # ── Public API ────────────────────────────────────────────────────────

    def acquire(self, detections: list, label_filter: set):
        """
        Latch onto the best matching detection: largest bbox area, highest
        confidence as tiebreaker, among labels in `label_filter`.

        Returns the acquired detection dict, or None if nothing matched
        (in which case the lock stays in its previous/empty state).
        """
        candidates = [
            d for d in (detections or [])
            if d.get('label', '').lower() in label_filter
        ]
        if not candidates:
            return None

        best = max(candidates, key=lambda d: (_bbox_area(d.get('box')), d.get('conf', 0.0)))

        self._label_filter          = set(label_filter)
        self.track_id               = best.get('track_id')
        self.label                  = best.get('label')
        self.last_known_bbox        = list(best['box']) if best.get('box') else None
        self.last_known_centroid    = _centroid(best.get('box'))
        self.velocity               = (0.0, 0.0)
        self.consecutive_miss_count = 0
        return best

    def update(self, detections: list):
        """
        Advance the lock by one tick against this tick's detections.

        Match order: track_id first (cheap, exact — survives the mob
        wandering behind another detection of the same label), then IoU
        against the last known bbox as a fallback for when ByteTrack drops
        the ID (e.g. after a long occlusion).

        On a match: updates bbox/centroid/velocity, resets miss count,
        returns the matched detection.
        On a miss: increments consecutive_miss_count, leaves last_known_bbox
        /centroid in place (so predicted_centroid can keep extrapolating),
        returns None.
        """
        if self.last_known_bbox is None:
            return None

        detections = detections or []
        match = None

        if self.track_id is not None:
            for d in detections:
                if d.get('track_id') == self.track_id:
                    match = d
                    break

        if match is None:
            best_iou = 0.0
            for d in detections:
                if self._label_filter and d.get('label', '').lower() not in self._label_filter:
                    continue
                iou = _iou(self.last_known_bbox, d.get('box'))
                if iou > best_iou:
                    best_iou = iou
                    match = d
            if best_iou < self.iou_threshold:
                match = None

        if match is None:
            self.consecutive_miss_count += 1
            return None

        new_box      = match.get('box')
        new_centroid = _centroid(new_box)
        if new_centroid is not None and self.last_known_centroid is not None:
            self.velocity = (new_centroid[0] - self.last_known_centroid[0],
                              new_centroid[1] - self.last_known_centroid[1])
        if new_box:
            self.last_known_bbox = list(new_box)
        if new_centroid is not None:
            self.last_known_centroid = new_centroid
        if match.get('track_id') is not None:
            self.track_id = match.get('track_id')
        self.consecutive_miss_count = 0
        return match

    @property
    def is_locked(self) -> bool:
        """True from acquire() until consecutive_miss_count exceeds
        lock_dropout_ticks — the caller should treat False as "give up"."""
        if self.last_known_bbox is None:
            return False
        return self.consecutive_miss_count <= self.lock_dropout_ticks

    @property
    def predicted_centroid(self):
        """Reynolds-style pursuit prediction: where the target should be
        `lookahead_ticks` ticks from now, extrapolated from the last
        observed velocity. Returns None if nothing has ever been acquired."""
        if self.last_known_centroid is None:
            return None
        cx, cy = self.last_known_centroid
        vx, vy = self.velocity
        return (cx + vx * self.lookahead_ticks, cy + vy * self.lookahead_ticks)

    def drop(self):
        """Explicit release — call on FSM exit from HUNT so the next HUNT
        session starts from a clean, stateless lock."""
        self.track_id               = None
        self.label                  = None
        self.last_known_bbox        = None
        self.last_known_centroid    = None
        self.velocity                = (0.0, 0.0)
        self.consecutive_miss_count = 0
        self._label_filter          = None
