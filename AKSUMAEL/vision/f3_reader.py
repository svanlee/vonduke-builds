# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — F3 Debug Screen Reader             ║
# ║  OCRs Minecraft's F3 overlay for real Y-level/biome  ║
# ╚══════════════════════════════════════════════════════╝
#
# Reads Minecraft's F3 debug overlay from a captured frame.
# Uses pytesseract OCR on a cropped region of the top-left corner.
# Returns y_level (int) and biome (str), or None if F3 is not open.

import re

import cv2
import numpy as np

try:
    import pytesseract
    TESSERACT_OK = True
except ImportError:
    TESSERACT_OK = False

# F3 data appears in the top-left quadrant (1920x1080).
# Expanded from 600x400 to cover larger GUI scales and different MC versions.
F3_CROP = (0, 0, 960, 600)  # x1, y1, x2, y2

XYZ_RE   = re.compile(r"XYZ:\s*[-\d.]+\s*/\s*([-\d.]+)\s*/\s*[-\d.]+", re.IGNORECASE)
BIOME_RE = re.compile(r"Biome:\s*([\w:]+)", re.IGNORECASE)


def _preprocess(frame_bgr: np.ndarray) -> np.ndarray:
    """Crop + upscale + threshold for better OCR on MC debug text."""
    h, w = frame_bgr.shape[:2]
    x1, y1, x2, y2 = F3_CROP
    x2, y2 = min(x2, w), min(y2, h)
    crop = frame_bgr[y1:y2, x1:x2]
    # Upscale 2x
    crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR)
    # Convert to grayscale
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    # Threshold: MC F3 text is white/yellow on dark background
    _, thresh = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY)
    return thresh


def read_f3(frame_bgr: np.ndarray) -> dict:
    """
    Returns dict with keys:
      y_level: int or None
      biome: str or None
      f3_active: bool (True if XYZ line found)
    """
    result = {"y_level": None, "biome": None, "f3_active": False}
    if not TESSERACT_OK or frame_bgr is None:
        return result

    img = _preprocess(frame_bgr)
    try:
        text = pytesseract.image_to_string(img, config="--psm 6 --oem 3")
    except Exception:
        return result

    xyz_match = XYZ_RE.search(text)
    if xyz_match:
        result["f3_active"] = True
        try:
            result["y_level"] = int(float(xyz_match.group(1)))
        except ValueError:
            pass

    biome_match = BIOME_RE.search(text)
    if biome_match:
        raw = biome_match.group(1).strip()
        # Strip minecraft: namespace
        result["biome"] = raw.replace("minecraft:", "")

    return result
