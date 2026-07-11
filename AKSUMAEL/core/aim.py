# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Vision-to-Mouse Aiming System      ║
# ╚══════════════════════════════════════════════════════╝
#
# Converts a YOLO bounding-box centre to the (dx, dy) mouse-move delta
# needed to centre the crosshair on the detected object.
#
# Box format: [x1, y1, x2, y2] in the YOLO inference frame's pixel coords.
# The inference frame is the "small_frame" from VideoCapturePipeline
# (~640 × 360).  Pass frame_w / frame_h to match the actual resolution.
#
# The returned (dx, dy) are passed directly into the action_dict 'look'
# field, e.g. {'look': {'dx': dx, 'dy': dy}}, which kb2040_packer.py
# turns into a pack_mouse_move() call.

import config

# Fraction of half-frame treated as "already on-target" (no nudge sent).
# 5 % of half-width ≈ 16 px on a 640-wide frame — good enough for mining.
DEAD_ZONE_FRAC = 0.05

# How many "look units" to send when the target is at the very edge of frame.
# LOOK_SENSITIVITY × EDGE_SCALE is the maximum delta per tick.
# With LOOK_SENSITIVITY=15 and EDGE_SCALE=6 → max 90 mouse units per tick.
EDGE_SCALE = 6


def bbox_to_mouse_delta(
    bbox,
    frame_w: int = 1920,
    frame_h: int = 1080,
    sensitivity: int = None,
) -> tuple:
    """
    Compute the relative mouse delta to aim the crosshair at a bbox centre.

    Args:
        bbox:        [x1, y1, x2, y2] bounding-box in YOLO frame pixels.
        frame_w:     Width  of the YOLO inference frame (pixels).
        frame_h:     Height of the YOLO inference frame (pixels).
        sensitivity: Mouse-delta scale factor (defaults to config.LOOK_SENSITIVITY).
                     Higher values → faster / coarser camera movement.

    Returns:
        (dx, dy) — signed ints clamped to [-127, 127].
                   (0, 0) when already within the dead-zone.
    """
    if sensitivity is None:
        sensitivity = config.LOOK_SENSITIVITY

    # Centre of the bounding box
    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0

    # Pixel offset from screen centre (positive = right / down)
    scx = frame_w / 2.0
    scy = frame_h / 2.0
    dx_px = cx - scx
    dy_px = cy - scy

    # Dead-zone check
    if abs(dx_px) < scx * DEAD_ZONE_FRAC and abs(dy_px) < scy * DEAD_ZONE_FRAC:
        return 0, 0

    # Normalise to [-1, 1] by half-frame, scale by sensitivity × EDGE_SCALE,
    # then clamp to signed-byte range (KB2040 mouse delta is stored as int8).
    dx = int(dx_px / scx * sensitivity * EDGE_SCALE)
    dy = int(dy_px / scy * sensitivity * EDGE_SCALE)
    dx = max(-127, min(127, dx))
    dy = max(-127, min(127, dy))
    return dx, dy


def bbox_centre(bbox) -> tuple:
    """Return (cx, cy) of a [x1, y1, x2, y2] bounding box."""
    return (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0


def is_on_target(bbox, frame_w: int, frame_h: int,
                 threshold_frac: float = DEAD_ZONE_FRAC) -> bool:
    """
    True when the bbox centre is within threshold_frac of the screen centre.
    Useful for deciding when to start holding left-click (mining).
    """
    scx = frame_w / 2.0
    scy = frame_h / 2.0
    cx, cy = bbox_centre(bbox)
    return (abs(cx - scx) < scx * threshold_frac
            and abs(cy - scy) < scy * threshold_frac)
