# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Color-Based Ore Detector                  ║
# ║                                                        ║
# ║  Detects ores by their distinctive Minecraft colors   ║
# ║  without needing YOLO or an API call.  Injected into  ║
# ║  the object list before FSM/skill evaluation.         ║
# ╚══════════════════════════════════════════════════════╝

import cv2
import numpy as np

# ── Color ranges in HSV ────────────────────────────────────────────────────────
# Each entry: (label, lower_hsv, upper_hsv, min_pixel_count)
# Diamond ore: bright cyan/teal sparkle on stone background
# Emerald ore: bright green
# Gold ore:    bright yellow
# Redstone ore: bright red/pink (glowing)
# Coal ore:    very dark patch (harder to isolate reliably — skipped)

ORE_COLOR_RANGES = [
    # label,            H_lo H_hi  S_lo S_hi  V_lo V_hi  min_px
    # Diamond: tight teal/cyan — high sat+val, large cluster required
    ('diamond_ore',     85,  105,  140,  255,  160, 255,  60),
    # Emerald: bright green, tightened sat floor
    ('emerald_ore',     50,   80,  140,  255,  130, 255,  40),
    # Gold: bright yellow, very high sat+val, large cluster required
    # (torchlight also produces yellow — require 100+ pixels to reduce FP)
    ('gold_ore',        20,   30,  180,  255,  180, 255,  100),
    # Redstone: glowing red/pink — only bright pixels
    ('redstone_ore',     0,   10,  160,  255,  120, 255,  40),
    # Lapis DISABLED — cave stone blue causes constant false positives.
    # YOLO handles lapis detection; re-enable only after tuning HSV ranges.
    # ('lapis_ore',      100,  130,  100,  255,   80, 200,  20),
]

# Exclude bottom 25 % of frame (HUD area) from color detection
HUD_EXCLUSION_FRAC = 0.25


def detect_ores_by_color(frame_bgr: np.ndarray,
                          conf_per_pixel: float = 0.004,
                          max_conf: float = 0.75) -> list:
    """
    Scan a BGR frame for ore-colored pixel clusters.

    Returns a list of detection dicts (same schema as YOLO objects):
        {label, conf, box: [x1, y1, x2, y2]}

    Only the center two-thirds of the frame is searched (excluding HUD).
    Confidence scales with cluster size, capped at max_conf so YOLO
    detections always win if both are present.
    """
    if frame_bgr is None:
        return []

    h, w = frame_bgr.shape[:2]
    # Exclude HUD strip at the bottom
    search_h = int(h * (1.0 - HUD_EXCLUSION_FRAC))
    roi = frame_bgr[:search_h, :]

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    detections = []

    for (label, h_lo, h_hi, s_lo, s_hi, v_lo, v_hi, min_px) in ORE_COLOR_RANGES:
        lower = np.array([h_lo, s_lo, v_lo], dtype=np.uint8)
        upper = np.array([h_hi, s_hi, v_hi], dtype=np.uint8)
        mask  = cv2.inRange(hsv, lower, upper)

        # Morphological close to connect nearby sparkle pixels
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask   = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        # Find connected components
        n_labels, labels_im, stats, _ = cv2.connectedComponentsWithStats(mask)
        for i in range(1, n_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            if area < min_px:
                continue
            x1 = stats[i, cv2.CC_STAT_LEFT]
            y1 = stats[i, cv2.CC_STAT_TOP]
            x2 = x1 + stats[i, cv2.CC_STAT_WIDTH]
            y2 = y1 + stats[i, cv2.CC_STAT_HEIGHT]
            conf = min(max_conf, area * conf_per_pixel)
            detections.append({
                'label': label,
                'conf':  round(conf, 3),
                'box':   [x1, y1, x2, y2],
                'source': 'color',
            })

    return detections


def merge_with_yolo(yolo_objects: list, color_objects: list) -> list:
    """
    Merge color detections into the YOLO object list.

    Rules:
    - If YOLO already has a detection for the same label (any instance),
      skip the color detection (trust YOLO).
    - Otherwise append the color detection so FSM/skills can act on it.
    """
    yolo_labels = {o.get('label') for o in yolo_objects}
    merged = list(yolo_objects)
    for det in color_objects:
        if det['label'] not in yolo_labels:
            merged.append(det)
    return merged
