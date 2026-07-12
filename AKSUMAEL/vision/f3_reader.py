# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — F3 Debug Screen Reader             ║
# ║  OCRs Minecraft's F3 overlay for position/world data  ║
# ╚══════════════════════════════════════════════════════╝
#
# Reads Minecraft's F3 debug overlay from a captured frame.
# Uses pytesseract OCR on a cropped region of the top-left corner.
#
# Extracts:
#   x, y, z         — full XYZ position (floats)
#   y_level          — integer Y (alias for z slot)
#   biome            — biome name (minecraft: prefix stripped)
#   facing           — cardinal facing direction (north/south/east/west)
#   fps              — current FPS (int)
#   chunk_x/z        — chunk coordinates
#   f3_active        — True if F3 overlay is confirmed open

import difflib
import re

import cv2
import numpy as np

try:
    import pytesseract
    TESSERACT_OK = True
except ImportError:
    TESSERACT_OK = False

# F3 data appears in the top-left quadrant (full-res 1920x1080).
# Wide crop to catch all lines regardless of GUI scale.
F3_CROP = (0, 0, 960, 650)  # x1, y1, x2, y2

# ── Regex patterns for each F3 line ──────────────────────────
# XYZ: 45.465 / 69.00000 / -10.093
XYZ_RE    = re.compile(
    r"XYZ:\s*([-\d.]+)\s*/\s*([-\d.]+)\s*/\s*([-\d.]+)", re.IGNORECASE)
# Block: 45 69 -11
BLOCK_RE  = re.compile(r"Block:\s*([-\d]+)\s+([-\d]+)\s+([-\d]+)", re.IGNORECASE)
# Biome: minecraft:plains  or  Biome: plains
# OCR often mangles 'Biome:' — tolerate i→l/1, :→;/.
BIOME_RE  = re.compile(r"[Bb][il1oO0]me\s*[:\;\.]\s*([\w:]+)", re.IGNORECASE)
# Fallback: match minecraft:xxx directly (biome IDs always carry this prefix)
BIOME_MC_RE = re.compile(r"minecraft:(\w+)")
# Facing: north (Towards -Z) ...
# OCR often mangles F→P/E, so tolerate common substitutions.
FACING_RE = re.compile(r"[FPE][Aa][Cc][Ii1lL][Nn][Gg]\s*[:\;\.]\s*(north|south|east|west)", re.IGNORECASE)
# Broader prefix capture for fuzzy direction matching when exact direction word is garbled.
FACE_PREFIX_RE = re.compile(r"[FPE][Aa][Cc][Ii1lL][Nn][Gg]\s*[:\;\.]\s*(\w+)", re.IGNORECASE)


def _fuzzy_direction(word: str) -> str | None:
    """Fuzzy-match an OCR'd word to a cardinal direction (e.g. 'Beuth' → 'south')."""
    matches = difflib.get_close_matches(word.lower(), ["north", "south", "east", "west"], n=1, cutoff=0.4)
    return matches[0] if matches else None
# 40 fps  or  T: 40 vsync
FPS_RE    = re.compile(r"(\d+)\s*fps", re.IGNORECASE)
# Chunk: 2 4 -1 in r:0 -1   → first three numbers are chunk xz+section
CHUNK_RE  = re.compile(r"Chunk:\s*([-\d]+)\s+([-\d]+)\s+([-\d]+)", re.IGNORECASE)


def _preprocess(frame_bgr: np.ndarray) -> np.ndarray:
    """Crop + upscale + threshold for better OCR on MC debug text."""
    h, w = frame_bgr.shape[:2]
    x1, y1, x2, y2 = F3_CROP
    x2, y2 = min(x2, w), min(y2, h)
    crop = frame_bgr[y1:y2, x1:x2]
    # Upscale 3x for small text legibility
    crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    # Threshold: MC F3 text is white/yellow on dark semi-transparent background
    # THRESH_BINARY_INV → dark text on white background (Tesseract-preferred)
    _, thresh = cv2.threshold(gray, 130, 255, cv2.THRESH_BINARY_INV)
    return thresh


def read_f3(frame_bgr: np.ndarray) -> dict:
    """
    Returns dict:
      f3_active : bool   — True if XYZ line was found (overlay is open)
      x, y, z   : float  — full position (None if not found)
      y_level   : int    — integer Y alias
      biome     : str    — biome name (None if not found)
      facing    : str    — 'north'/'south'/'east'/'west' (None if not found)
      fps       : int    — current game FPS (None if not found)
      chunk_x   : int    — chunk X (None if not found)
      chunk_z   : int    — chunk Z (None if not found)
    """
    result = {
        "f3_active": False,
        "x": None, "y": None, "z": None,
        "y_level": None,
        "biome": None,
        "facing": None,
        "fps": None,
        "chunk_x": None, "chunk_z": None,
    }
    if not TESSERACT_OK or frame_bgr is None:
        return result

    img = _preprocess(frame_bgr)
    try:
        text = pytesseract.image_to_string(img, config="--psm 6 --oem 3")
    except Exception:
        return result

    # XYZ — primary detection signal
    m = XYZ_RE.search(text)
    if m:
        result["f3_active"] = True
        try:
            result["x"] = float(m.group(1))
            result["y"] = float(m.group(2))
            result["z"] = float(m.group(3))
            result["y_level"] = int(result["y"])
        except ValueError:
            pass

    # Block integer coords as fallback for X/Y/Z when XYZ line OCR fails
    if result["y_level"] is None or result["x"] is None:
        m = BLOCK_RE.search(text)
        if m:
            try:
                bx, by, bz = int(m.group(1)), int(m.group(2)), int(m.group(3))
                result["f3_active"] = True
                if result["y_level"] is None:
                    result["y_level"] = by
                if result["y"] is None:
                    result["y"] = float(by)
                if result["x"] is None:
                    result["x"] = float(bx)
                if result["z"] is None:
                    result["z"] = float(bz)
            except ValueError:
                pass

    # Biome
    m = BIOME_RE.search(text)
    if m:
        raw = m.group(1).strip()
        result["biome"] = raw.replace("minecraft:", "").split()[0]  # first word
    else:
        # Fallback: biome IDs always appear as minecraft:name in F3 text
        m = BIOME_MC_RE.search(text)
        if m:
            result["biome"] = m.group(1)

    # Facing direction — exact match first, fuzzy fallback for garbled OCR words
    m = FACING_RE.search(text)
    if m:
        result["facing"] = m.group(1).lower()
    if result["facing"] is None:
        m = FACE_PREFIX_RE.search(text)
        if m:
            result["facing"] = _fuzzy_direction(m.group(1))

    # FPS
    m = FPS_RE.search(text)
    if m:
        try:
            result["fps"] = int(m.group(1))
        except ValueError:
            pass

    # Chunk
    m = CHUNK_RE.search(text)
    if m:
        try:
            result["chunk_x"] = int(m.group(1))
            result["chunk_z"] = int(m.group(3))
        except ValueError:
            pass

    if result["facing"] is None or result["biome"] is None:
        snippet = text[:300].replace('\n', ' | ')
        print(f"[F3-DEBUG] facing={result['facing']} biome={result['biome']} raw_ocr={snippet!r}")

    return result
