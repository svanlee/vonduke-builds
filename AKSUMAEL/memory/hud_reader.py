# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — HUD Pixel Reader                          ║
# ║  Reads health/hunger straight from HUD pixel colors,  ║
# ║  independent of YOLO's health_bar/hunger_bar boxes    ║
# ╚══════════════════════════════════════════════════════╝
#
# Vanilla Minecraft draws hearts and hunger drumsticks as a row directly
# above the hotbar — hearts occupy roughly the left half of that row,
# hunger the right half. Rather than hardcode absolute pixel coordinates
# (which only hold for one exact resolution/GUI-scale), this samples a
# fractional band of the frame and counts matching HSV pixels, then
# normalizes against the largest pixel count seen so far this session —
# the same running-max calibration behaviors/hunger.py already uses for
# its YOLO-bbox-width read, just applied to raw pixels instead of a box.
#
# This is a coarse, resolution-independent proxy, not per-heart precision:
# it degrades gracefully (stays at its last known fraction) if the HUD row
# is temporarily out of frame or occluded, same as HungerBehavior's bar.

import cv2
import numpy as np

# Fractional ROI: the HUD row just above the hotbar, split left/right.
HUD_ROW_Y_FRAC = (0.86, 0.94)   # vertical band containing hearts/hunger icons
HEALTH_X_FRAC  = (0.32, 0.49)   # left half of that row — hearts
HUNGER_X_FRAC  = (0.51, 0.68)   # right half of that row — hunger drumsticks

# Heart red — bright, highly saturated red
HEALTH_HSV_LO = (0,   150, 150)
HEALTH_HSV_HI = (10,  255, 255)
# Hunger drumstick — brown/orange roasted-meat color
HUNGER_HSV_LO = (8,   100, 100)
HUNGER_HSV_HI = (25,  220, 220)


class HudReader:
    """Tracks health_pct/hunger_pct (0.0-1.0) from raw HUD pixel colors."""

    def __init__(self):
        self._max_health_px = 0
        self._max_hunger_px = 0
        self.health_pct = 1.0
        self.hunger_pct = 1.0

    @staticmethod
    def _count_px(roi_bgr: np.ndarray, lower: tuple, upper: tuple) -> int:
        if roi_bgr.size == 0:
            return 0
        hsv  = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(lower, dtype=np.uint8),
                                 np.array(upper, dtype=np.uint8))
        return int(cv2.countNonZero(mask))

    def update(self, frame_bgr: np.ndarray) -> tuple:
        """Call once per tick with the current gameplay frame.
        Returns (health_pct, hunger_pct); holds last known value on a
        frame miss rather than snapping to 0."""
        if frame_bgr is None:
            return self.health_pct, self.hunger_pct

        h, w = frame_bgr.shape[:2]
        y1, y2 = int(h * HUD_ROW_Y_FRAC[0]), int(h * HUD_ROW_Y_FRAC[1])
        if y2 <= y1:
            return self.health_pct, self.hunger_pct

        hx1, hx2 = int(w * HEALTH_X_FRAC[0]), int(w * HEALTH_X_FRAC[1])
        gx1, gx2 = int(w * HUNGER_X_FRAC[0]), int(w * HUNGER_X_FRAC[1])

        health_px = self._count_px(frame_bgr[y1:y2, hx1:hx2], HEALTH_HSV_LO, HEALTH_HSV_HI)
        hunger_px = self._count_px(frame_bgr[y1:y2, gx1:gx2], HUNGER_HSV_LO, HUNGER_HSV_HI)

        self._max_health_px = max(self._max_health_px, health_px)
        self._max_hunger_px = max(self._max_hunger_px, hunger_px)

        if self._max_health_px:
            self.health_pct = health_px / self._max_health_px
        if self._max_hunger_px:
            self.hunger_pct = hunger_px / self._max_hunger_px
        return self.health_pct, self.hunger_pct
