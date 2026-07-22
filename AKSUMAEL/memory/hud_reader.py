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

# The original HUD_ROW_Y_FRAC=(0.86,0.94)/HEALTH_X_FRAC=(0.32,0.49)/
# HUNGER_X_FRAC=(0.51,0.68) all sampled empty background, not the hearts/
# drumstick icons (confirmed against data/debug_snapshot.jpg at 1920x1080 —
# the actual row sits much lower, and both icon groups are narrower/further
# right than the stale fractions gave). That left _max_hunger_px pinned near
# 0, so hunger_pct read ~0% even at full hunger and runtime.py's
# `hunger_pct < 30%` gate fired constantly (2026-07-21). Both rows corrected
# below, verified against the real debug frame.
HEALTH_ROW_Y_FRAC = (0.945, 0.972)
HEALTH_X_FRAC     = (0.432, 0.494)  # left side of the row — hearts
HUNGER_ROW_Y_FRAC = (0.945, 0.972)
HUNGER_X_FRAC     = (0.508, 0.575)  # right side of the row — hunger drumsticks

# Heart red — bright, highly saturated red. OpenCV hue is a 0-179 wheel, so
# red (hue 0 in the usual 0-360 sense) sits at BOTH ends of that range; real
# heart pixels sampled from the debug frame came back at hue~177, which the
# original single-sided (0,150,150)-(10,255,255) range never matched (that's
# why health_pct silently stuck at its default 1.0 forever — _max_health_px
# never left 0 — rather than misbehaving loudly like hunger did). Two ranges,
# OR'd together in _count_px, cover both sides of the wraparound.
HEALTH_HSV_LO = (0,   150, 150)
HEALTH_HSV_HI = (10,  255, 255)
HEALTH_HSV_LO2 = (170, 150, 150)
HEALTH_HSV_HI2 = (179, 255, 255)
# Hunger drumstick — brown/orange roasted-meat color
HUNGER_HSV_LO = (8,   100, 100)
HUNGER_HSV_HI = (25,  220, 220)


class HudReader:
    """Tracks health_pct/hunger_pct (0.0-1.0) from raw HUD pixel colors."""

    def __init__(self):
        self._max_health_px = 0
        self._max_hunger_px = 0
        # Previous tick's raw pixel counts — see _confirm_new_max below.
        self._prev_health_px = 0
        self._prev_hunger_px = 0
        self.health_pct = 1.0
        self.hunger_pct = 1.0

    @staticmethod
    def _count_px(roi_bgr: np.ndarray, lower: tuple, upper: tuple,
                  lower2: tuple = None, upper2: tuple = None) -> int:
        if roi_bgr.size == 0:
            return 0
        hsv  = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(lower, dtype=np.uint8),
                                 np.array(upper, dtype=np.uint8))
        if lower2 is not None:
            mask2 = cv2.inRange(hsv, np.array(lower2, dtype=np.uint8),
                                      np.array(upper2, dtype=np.uint8))
            mask = cv2.bitwise_or(mask, mask2)
        return int(cv2.countNonZero(mask))

    @staticmethod
    def _confirm_new_max(current_max: int, prev_px: int, new_px: int) -> int:
        """Only raise the running-max ceiling if this tick's pixel count is
        close to the PREVIOUS tick's count too. A single spurious spike
        (e.g. Minecraft's full-screen red damage-flash overlay passing
        through the health ROI for one frame) must not permanently poison
        the ceiling — every later normal-health frame would then divide out
        to a near-zero fraction forever, which is why the Overseer kept
        reporting ~0% health / "agent is dead" on a fully healthy agent
        (2026-07-20)."""
        if new_px <= current_max:
            return current_max
        if abs(new_px - prev_px) <= max(5, prev_px * 0.15):
            return new_px
        return current_max

    def update(self, frame_bgr: np.ndarray) -> tuple:
        """Call once per tick with the current gameplay frame.
        Returns (health_pct, hunger_pct); holds last known value on a
        frame miss rather than snapping to 0."""
        if frame_bgr is None:
            return self.health_pct, self.hunger_pct

        h, w = frame_bgr.shape[:2]
        hy1, hy2 = int(h * HEALTH_ROW_Y_FRAC[0]), int(h * HEALTH_ROW_Y_FRAC[1])
        gy1, gy2 = int(h * HUNGER_ROW_Y_FRAC[0]), int(h * HUNGER_ROW_Y_FRAC[1])
        if hy2 <= hy1 or gy2 <= gy1:
            return self.health_pct, self.hunger_pct

        hx1, hx2 = int(w * HEALTH_X_FRAC[0]), int(w * HEALTH_X_FRAC[1])
        gx1, gx2 = int(w * HUNGER_X_FRAC[0]), int(w * HUNGER_X_FRAC[1])

        health_px = self._count_px(frame_bgr[hy1:hy2, hx1:hx2], HEALTH_HSV_LO, HEALTH_HSV_HI,
                                    HEALTH_HSV_LO2, HEALTH_HSV_HI2)
        hunger_px = self._count_px(frame_bgr[gy1:gy2, gx1:gx2], HUNGER_HSV_LO, HUNGER_HSV_HI)

        self._max_health_px = self._confirm_new_max(
            self._max_health_px, self._prev_health_px, health_px)
        self._max_hunger_px = self._confirm_new_max(
            self._max_hunger_px, self._prev_hunger_px, hunger_px)
        self._prev_health_px = health_px
        self._prev_hunger_px = hunger_px

        if self._max_health_px:
            self.health_pct = min(1.0, health_px / self._max_health_px)
        if self._max_hunger_px:
            self.hunger_pct = min(1.0, hunger_px / self._max_hunger_px)
        return self.health_pct, self.hunger_pct
