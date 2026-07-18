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
    # Raised sat+val floors to exclude dull greenish cave blocks. min_px raised
    # to 150 to avoid FP from bright leaf pixels when near trees.
    ('emerald_ore',     55,   75,  180,  255,  170, 255,  150),
    # Gold: bright yellow, very high sat+val, large cluster required
    # (torchlight and sunlit wood also produce yellow — raised to 200px to
    # further reduce FP near trees)
    ('gold_ore',        20,   30,  180,  255,  180, 255,  200),
    # Redstone: glowing red/pink — tightened to avoid copper orange (H~10-20)
    # Raised sat+val floors; also covers high-H red wraparound (H 168-180)
    ('redstone_ore',     0,    8,  190,  255,  160, 255,  100),
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
    #
    # V_lo dropped 35 -> 20 on 2026-07-18: torch-lit wood at night reads
    # darker than the daylight calibration frame above and was falling
    # entirely below the V floor, blinding color detection after dark.
    # Upper bound left at 72 (unaffected — night frames don't overshoot it).
    # 2026-07-18 second pass: expanded H 12-32 -> 5-45 (torch warms hue
    # toward orange), S floor 55 -> 30 (dark scenes desaturate), V floor
    # 20 -> 5 (torchlit trunks can be very dim), min_px 150 -> 80 (fewer
    # visible pixels at night). A second 'torch_log' entry (same label
    # 'log') covers the very-dark warm-toned case separately.
    ('log',              5,   45,   30,  150,    5,   72,   80),
    # Torch-lit log variant: dark brownish-orange glow, very low V.
    # Uses label 'log' so FSM/skill TREE_TARGETS matching is identical.
    ('log',              5,   25,   80,  200,    5,   55,   50),
    # Oak leaves: min_px kept high (real canopy blobs are large) as a second
    # line of defense against the grass/leaves hue overlap noted above.
    # V_lo dropped 30 -> 15 alongside 'log' above for the same night-vision
    # reason; leaves sit even darker than logs under torchlight.
    # 2026-07-18 second pass: H widened 38-65 -> 35-70, S floor 100 -> 70,
    # V floor 15 -> 5, min_px 300 -> 150 — all for nighttime visibility.
    ('leaves',          35,   70,   70,  220,    5,   90,  150),
    # Birch log: light gray/near-white bark — no birch tree in the frame
    # used to calibrate the two ranges above, so this one is still the
    # original unverified estimate (S/V converted from a 0-100% spec).
    # Hue is unreliable at this low saturation regardless, hence the full
    # H range.
    ('birch_log',        0,  179,    0,   51,  179,  230,  100),

    # ── Mob/threat color signatures — added for FLEE/COMBAT gating ──
    # These are threat SIGNALS only (fed into the same detection list YOLO
    # results go into); core/fsm.py's HOSTILE_MOBS/FLEE logic decides what to
    # do with them. Broad/low-saturation ranges (skeleton, spider) will false-
    # positive on ordinary bright/dark terrain more than the ore ranges above
    # do — min_px is raised accordingly to require a reasonably large,
    # coherent blob before firing, but expect more noise here than from the
    # tightly-calibrated ore/tree ranges.
    #
    # Zombie: green-grey rotting skin
    ('zombie',           50,   80,   40,  100,   40,  120,  120),
    # Skeleton: off-white bone — very low saturation, so hue is unreliable;
    # high min_px to avoid firing on clouds/snow/white wool.
    ('skeleton',          0,   30,    0,   40,  160,  230,  220),
    # Creeper: bright pure green (distinct from oak leaves' duller green —
    # see 'leaves' above — and from emerald ore's higher V floor)
    ('creeper',          60,   80,  120,  200,   80,  160,  120),
    # Spider: near-black body — extremely broad match against any dark
    # pixel cluster (shadow, night sky, cave wall); high min_px to require a
    # sizeable coherent blob, but still expect false positives in dark caves.
    ('spider',            0,   30,    0,   50,    5,   40,  250),
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


def sample_box_pixel_count(frame_bgr: np.ndarray, box: list, label: str) -> int:
    """Count pixels matching `label`'s HSV range(s) inside `box` only.

    Used by core/fsm.py's MINE state to confirm a color-sourced target has
    actually broken by re-checking the target's OWN last-known bbox region
    directly, rather than waiting for the next full-frame detect_ores_by_color()
    pass to stop reporting that label anywhere in frame — the latter can lag
    (or, on a re-detect elsewhere in frame, never fire at all) when the block
    everyone's aiming at breaks but a same-colored block sits elsewhere in view.
    """
    if frame_bgr is None or not box or len(box) != 4:
        return 0
    h, w = frame_bgr.shape[:2]
    x1, y1, x2, y2 = (max(0, int(box[0])), max(0, int(box[1])),
                      min(w, int(box[2])), min(h, int(box[3])))
    if x2 <= x1 or y2 <= y1:
        return 0
    roi = frame_bgr[y1:y2, x1:x2]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    total = 0
    for (lbl, h_lo, h_hi, s_lo, s_hi, v_lo, v_hi, _min_px) in ORE_COLOR_RANGES:
        if lbl != label:
            continue
        lower = np.array([h_lo, s_lo, v_lo], dtype=np.uint8)
        upper = np.array([h_hi, s_hi, v_hi], dtype=np.uint8)
        total += int(cv2.countNonZero(cv2.inRange(hsv, lower, upper)))
    return total


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
