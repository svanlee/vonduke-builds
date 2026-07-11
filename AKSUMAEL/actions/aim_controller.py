# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Aim Controller                            ║
# ║                                                        ║
# ║  Proportional camera controller: nudges the mouse     ║
# ║  toward a YOLO bounding box center before a skill     ║
# ║  executes its steps.  Works on the 640×360 YOLO frame ║
# ║  coordinate space that bounding boxes are reported in.║
# ╚══════════════════════════════════════════════════════╝

import time

# ── Tuning constants ──────────────────────────────────────────────────────────
# AIM_KP:        proportional gain — screen-pixel offset → mouse-unit delta.
#                Lower = slower/smoother aim; higher = snappier but may overshoot.
# AIM_DEAD_ZONE: pixel radius around screen centre considered "on target".
# AIM_MAX_STEP:  maximum mouse units sent per tick (prevents huge jumps).
# AIM_TICK_S:    sleep between aim ticks (seconds).

AIM_KP        = 0.08
AIM_DEAD_ZONE = 25     # px in YOLO-frame coordinates
AIM_MAX_STEP  = 18     # mouse units per tick
AIM_TICK_S    = 0.05   # 20 Hz aim loop

# YOLO frame dimensions (capture is 1920×1080 downscaled to 640-wide)
YOLO_W = 640
YOLO_H = 360


class AimController:
    """
    Compute and send relative mouse-look packets to centre the crosshair
    on a detected object before a skill's action steps run.

    Usage (one tick):
        pkt = aim.look_packet(box)          # returns action dict or {}
        executor.execute(pkt)

    Usage (blocking, up to max_ticks):
        on_target = aim.aim_until(box, executor, max_ticks=10)
    """

    def __init__(self, frame_w: int = YOLO_W, frame_h: int = YOLO_H):
        self.frame_w = frame_w
        self.frame_h = frame_h

    # ── Core helpers ──────────────────────────────────────────────────────────

    def _offset(self, box: list) -> tuple:
        """Pixel distance from screen centre to box centre (dx, dy)."""
        cx = (box[0] + box[2]) / 2
        cy = (box[1] + box[3]) / 2
        return cx - self.frame_w / 2, cy - self.frame_h / 2

    def is_on_target(self, box: list) -> bool:
        dx, dy = self._offset(box)
        return abs(dx) <= AIM_DEAD_ZONE and abs(dy) <= AIM_DEAD_ZONE

    def look_packet(self, box: list) -> dict:
        """
        Return an action dict with a 'look' nudge toward box.
        Returns {} if already on target (caller can skip the send).
        """
        dx_px, dy_px = self._offset(box)
        if abs(dx_px) <= AIM_DEAD_ZONE and abs(dy_px) <= AIM_DEAD_ZONE:
            return {}
        def clamp(v):
            return int(max(-AIM_MAX_STEP, min(AIM_MAX_STEP, v * AIM_KP)))
        mx, my = clamp(dx_px), clamp(dy_px)
        if mx == 0 and my == 0:
            return {}
        return {'look': {'dx': mx, 'dy': my}, 'source': 'aim'}

    # ── Blocking aim loop ─────────────────────────────────────────────────────

    def aim_until(self, box: list, executor,
                  max_ticks: int = 12,
                  move_key: str = None) -> bool:
        """
        Nudge the camera toward box for up to max_ticks iterations.

        Optionally hold a movement key (e.g. 'w') while aiming so the
        character walks toward the target at the same time.

        Returns True if on target at the end.
        """
        for _ in range(max_ticks):
            if self.is_on_target(box):
                return True
            ad = self.look_packet(box)
            if move_key:
                ad['key'] = move_key
            if ad:
                executor.execute(ad)
            time.sleep(AIM_TICK_S)
        return self.is_on_target(box)

    def move_and_attack(self, box: list, executor,
                        max_aim_ticks: int = 12,
                        move_key: str = 'w',
                        attack_click: bool = True) -> bool:
        """
        Walk toward target while aiming, then attack when on target.
        Returns True if the attack was fired.
        """
        on_target = self.aim_until(box, executor,
                                   max_ticks=max_aim_ticks,
                                   move_key=move_key)
        if on_target and attack_click:
            executor.execute({'click': [50, 50], 'source': 'aim'})
            return True
        return False
