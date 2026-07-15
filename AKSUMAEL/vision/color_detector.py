# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Color-Based Ore & Tree Detector           ║
# ║                                                        ║
# ║  Detects ores/trees by their distinctive Minecraft    ║
# ║  colors without needing YOLO or an API call. Injected ║
# ║  into the object list before FSM/skill evaluation.    ║
# ╚══════════════════════════════════════════════════════╝

import cv2
import numpy as np

# ── Color ranges in HSV ────────────────────────────────────────────────────────
# Each entry: (label, lower_hsv, upper_hsv, min_pixel_count)
# H is OpenCV's 0-179 scale (matches the rest of this file); S/V here are also
# OpenCV's 0-255 scale — entries given as a 0-100% spec were converted
# (%/100 * 255) rather than copied in directly.
#
# Diamond ore:  bright cyan/teal sparkle on stone background
# Emerald ore:  bright green
# Gold ore:     bright yellow
# Redstone ore: bright red/pink (glowing)
# Coal ore:     very dark patch (harder to isolate reliably — skipped)
# Oak log:      earthy brown trunk
# Oak leaves:   medium green canopy — see 2026-07-15 note below; can overlap
#               grass block color, tune min_px up first if this over-fires
# Birch log:    light gray/near-white bark, essentially hue-independent

ORE_COLOR_RANGES = [
    # label,            H_lo H_hi  S_lo S_hi  V_lo V_hi  min_px
    # Diamond: tight teal/cyan — high sat+val, large cluster required
    ('diamond_ore',     85,  105,  140,  255,  160, 255,  60),
    # Emerald: bright pure green — tightened to avoid oxidised copper (teal H~85-100)
    # Raised sat+val floors to exclude dull greenish cave blocks
    ('emerald_ore',     55,   75,  180,  255,  170, 255,  60),
    # Gold: bright yellow, very high sat+val, large cluster required
    # (torchlight also produces yellow — require 100+ pixels to reduce FP)
    ('gold_ore',        20,   30,  180,  255,  180, 255,  100),
    # Redstone: glowing red/pink — tightened to avoid copper orange (H~10-20)
    # Raised sat+val floors; also covers high-H red wraparound (H 168-180)
    ('redstone_ore',     0,    8,  190,  255,  160, 255,  60),
    # Lapis DISABLED — cave stone blue causes constant false positives.
    # YOLO handles lapis detection; re-enable only after tuning HSV ranges.
    # ('lapis_ore',      100,  130,  100,  255,   80, 200,  20),

    # ── Trees — added 2026-07-15 as a fallback while YOLO can't see them ──
    # (both the retrained and pre-retrain-backup weights detect zero
    # log/leaves at any confidence — the training set has zero examples of
    # either class, so this isn't a threshold problem; see config.py's
    # YOLO_CONF_THRESHOLD comment). Labels match the YOLO class names
    # ('log', 'leaves', 'birch_log' — model.names classes 21/22/46) so the
    # skill system's TREE_TARGETS matching treats these identically to a
    # real YOLO detection.
    #
    # Initial H=20-35/S=40-80%/V=50-80% (oak log) and H=70-100/S=40-80%/
    # V=30-65% (oak leaves) estimates produced zero detections on real
    # gameplay frames — sampled actual trunk/canopy pixels from a captured
    # forest frame instead (data/yolo_dataset/images/train/
    # survey_1784141596277_4223.jpg) and calibrated against that ground
    # truth: real trunk pixels measured H~17-43/S~78-125/V~43-70, real
    # canopy pixels measured H~49-55/S~132-200/V~42-68 — both noticeably
    # darker (lower V) than the initial estimate, and leaves' true hue is
    # nowhere near 70-100. Ranges below have margin added around those
    # measurements. Grass ground samples the same shaded frame at
    # H~44/S~151/V~122 — V is the main separator from leaves here (bright
    # sunlit grass runs V~108+), so leaves' V_hi is capped at 90 to avoid
    # flagging ordinary lit grass as canopy.
    #
    # log's H/V window was tightened further than the raw trunk measurement
    # (17-43/43-70) after the first calibration pass mistook a sunlit dirt
    # path for a trunk — dirt shares nearly the same hue in this texture
    # pack and only reliably runs brighter (V~66-114 vs trunk's ~43-70).
    # H(12-32)/V(35-72) was the tightest window that still won the largest-
    # connected-component race against dirt on 3/3 test frames; still not a
    # clean separation (some dirt pixels do fall in this range too), so
    # false positives on ground/dirt are possible, just less likely to be
    # the *largest* blob than an actual trunk.
    ('log',             12,   32,   55,  150,   35,   72,  150),
    # Oak leaves: min_px kept high (real canopy blobs are large) as a second
    # line of defense against the grass/leaves hue overlap noted above.
    ('leaves',          38,   65,  100,  220,   30,   90,  300),
    # Birch log: light gray/near-white bark — no birch tree in the frame
    # used to calibrate the two ranges above, so this one is still the
    # original unverified estimate (S/V converted from a 0-100% spec).
    # Hue is unreliable at this low saturation regardless, hence the full
    # H range.
    ('birch_log',        0,  179,    0,   51,  179,  230,  100),
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

        # Find connected components — keep only the largest qualifying cluster
        # to avoid emitting 5+ detections for the same ore vein per frame
        n_labels, labels_im, stats, _ = cv2.connectedComponentsWithStats(mask)
        best = None
        for i in range(1, n_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            if area < min_px:
                continue
            if best is None or area > best[0]:
                best = (area, i)

        if best is not None:
            area, i = best
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
