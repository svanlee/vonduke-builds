"""
gesture/recognizer.py — MediaPipe hand gesture recognizer.

Detects 5 gestures from a webcam or any OpenCV-compatible source:

  STOP        — open palm facing camera (all fingers extended, palm up/forward)
  FORWARD     — thumbs up (thumb extended, fingers curled)
  HOLD        — closed fist (all fingers curled)
  TURN_LEFT   — index finger pointing left
  TURN_RIGHT  — index finger pointing right
  NONE        — unrecognized or no hand detected

All geometry is normalized to [0,1] so it's resolution-independent.

Requires: mediapipe, opencv-python
Install:  pip install mediapipe opencv-python --break-system-packages
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass
from typing import Optional

import cv2


class GestureCommand(str, enum.Enum):
    STOP        = "STOP"
    FORWARD     = "FORWARD"
    HOLD        = "HOLD"
    TURN_LEFT   = "TURN_LEFT"
    TURN_RIGHT  = "TURN_RIGHT"
    NONE        = "NONE"


@dataclass
class GestureResult:
    command:    GestureCommand
    confidence: float           # 0.0 – 1.0 (heuristic, not a model probability)
    ts:         float           # unix timestamp
    raw_landmarks: Optional[list] = None


# ── Landmark indices (MediaPipe HandLandmark) ─────────────────────────────────
# https://developers.google.com/mediapipe/solutions/vision/hand_landmarker
WRIST       = 0
THUMB_TIP   = 4
INDEX_TIP   = 8
MIDDLE_TIP  = 12
RING_TIP    = 16
PINKY_TIP   = 20
INDEX_MCP   = 5    # knuckle (metacarpophalangeal)
MIDDLE_MCP  = 9
RING_MCP    = 13
PINKY_MCP   = 17
THUMB_IP    = 3    # thumb interphalangeal joint


def _lm(hand_landmarks, idx: int):
    """Return (x, y) for a landmark index."""
    lm = hand_landmarks.landmark[idx]
    return lm.x, lm.y


def _is_finger_up(hand_landmarks, tip_idx: int, mcp_idx: int) -> bool:
    """True if finger tip is higher (smaller y) than its knuckle by > threshold."""
    tip_y = hand_landmarks.landmark[tip_idx].y
    mcp_y = hand_landmarks.landmark[mcp_idx].y
    return (mcp_y - tip_y) > 0.04  # ~4% of frame height


def _is_thumb_up(hand_landmarks) -> bool:
    """Thumb up: tip is above IP joint and above index knuckle."""
    tip_y = hand_landmarks.landmark[THUMB_TIP].y
    ip_y  = hand_landmarks.landmark[THUMB_IP].y
    idx_y = hand_landmarks.landmark[INDEX_MCP].y
    return tip_y < ip_y - 0.02 and tip_y < idx_y


def _pointing_direction(hand_landmarks) -> Optional[str]:
    """
    Returns 'left' or 'right' if index finger is extended and
    pointing significantly horizontally, else None.
    """
    index_up = _is_finger_up(hand_landmarks, INDEX_TIP, INDEX_MCP)
    middle_up = _is_finger_up(hand_landmarks, MIDDLE_TIP, MIDDLE_MCP)
    if not index_up or middle_up:
        return None  # not a clear single-finger point

    tip_x, tip_y = _lm(hand_landmarks, INDEX_TIP)
    mcp_x, mcp_y = _lm(hand_landmarks, INDEX_MCP)

    dx = tip_x - mcp_x
    dy = tip_y - mcp_y
    # Pointing is horizontal when |dx| > |dy| * 1.5
    if abs(dx) < abs(dy) * 1.5:
        return None
    return "left" if dx < 0 else "right"


def _classify(hand_landmarks) -> GestureCommand:
    """Classify hand landmarks into a GestureCommand."""
    # ── Thumbs up ─────────────────────────────────────────────────────────────
    thumb_up = _is_thumb_up(hand_landmarks)
    index_up = _is_finger_up(hand_landmarks, INDEX_TIP, INDEX_MCP)
    middle_up = _is_finger_up(hand_landmarks, MIDDLE_TIP, MIDDLE_MCP)
    ring_up = _is_finger_up(hand_landmarks, RING_TIP, RING_MCP)
    pinky_up = _is_finger_up(hand_landmarks, PINKY_TIP, PINKY_MCP)

    fingers_up_count = sum([index_up, middle_up, ring_up, pinky_up])

    if thumb_up and fingers_up_count == 0:
        return GestureCommand.FORWARD

    # ── Fist (HOLD) ────────────────────────────────────────────────────────────
    if fingers_up_count == 0 and not thumb_up:
        return GestureCommand.HOLD

    # ── Open palm (STOP) — all 4 fingers extended ─────────────────────────────
    if fingers_up_count >= 4:
        return GestureCommand.STOP

    # ── Point left / right ─────────────────────────────────────────────────────
    direction = _pointing_direction(hand_landmarks)
    if direction == "left":
        return GestureCommand.TURN_LEFT
    if direction == "right":
        return GestureCommand.TURN_RIGHT

    return GestureCommand.NONE


class GestureRecognizer:
    """
    Run MediaPipe Hands on a video source and emit GestureResults.

    Usage (standalone):
        rec = GestureRecognizer(camera_index=0, display=True)
        for result in rec.stream():
            if result.command != GestureCommand.NONE:
                print(result.command)

    Usage (single frame):
        result = rec.recognize_frame(bgr_frame)
    """

    def __init__(self, camera_index: int = 0, display: bool = False,
                 min_detection_confidence: float = 0.7,
                 min_tracking_confidence: float = 0.5,
                 debounce_sec: float = 0.3):
        self._cam_idx = camera_index
        self._display = display
        self._debounce = debounce_sec
        self._last_command = GestureCommand.NONE
        self._last_emit_ts = 0.0
        self._det_conf = min_detection_confidence
        self._trk_conf = min_tracking_confidence
        self._hands = None

    def _get_hands(self):
        if self._hands is None:
            try:
                import mediapipe as mp
                self._mp_hands = mp.solutions.hands
                self._mp_draw = mp.solutions.drawing_utils
                self._hands = self._mp_hands.Hands(
                    static_image_mode=False,
                    max_num_hands=1,
                    min_detection_confidence=self._det_conf,
                    min_tracking_confidence=self._trk_conf,
                )
            except ImportError:
                raise RuntimeError(
                    "mediapipe not installed — run: "
                    "pip install mediapipe --break-system-packages"
                )
        return self._hands

    def recognize_frame(self, bgr_frame) -> GestureResult:
        """Classify a single BGR frame. Returns GestureResult."""
        hands = self._get_hands()
        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        results = hands.process(rgb)

        if not results.multi_hand_landmarks:
            return GestureResult(GestureCommand.NONE, 0.0, time.time())

        hand_lm = results.multi_hand_landmarks[0]
        command = _classify(hand_lm)

        return GestureResult(
            command=command,
            confidence=0.9 if command != GestureCommand.NONE else 0.0,
            ts=time.time(),
            raw_landmarks=hand_lm,
        )

    def stream(self):
        """
        Generator: yields GestureResult from live webcam.
        Applies debounce — only emits a new command if different from last,
        or if debounce_sec has passed.
        """
        cap = cv2.VideoCapture(self._cam_idx)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open camera {self._cam_idx}")

        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    continue

                result = self.recognize_frame(frame)

                now = time.time()
                if (result.command != GestureCommand.NONE and
                        (result.command != self._last_command or
                         now - self._last_emit_ts > self._debounce)):
                    self._last_command = result.command
                    self._last_emit_ts = now
                    yield result

                if self._display and result.raw_landmarks:
                    self._mp_draw.draw_landmarks(
                        frame, result.raw_landmarks, self._mp_hands.HAND_CONNECTIONS
                    )
                    cv2.putText(frame, result.command.value, (10, 40),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 2)

                if self._display:
                    cv2.imshow("Gesture Control", frame)
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break
        finally:
            cap.release()
            if self._display:
                cv2.destroyAllWindows()

    def close(self):
        if self._hands is not None:
            self._hands.close()
            self._hands = None
