"""
vision/desktop_vision.py — Desktop self-awareness vision for AKSUMAEL.

Used in 'desktop' mode (no capture card / KB2040). Captures the local
machine's screen and optionally the laptop webcam, converts to text
context, and feeds it to the Jarvis brain so AKSUMAEL knows what's
happening on the machine it's running on.

Pipeline:
    screen grab → OCR (tesseract/easyocr) → text summary
    webcam frame → face/gesture detection (optional, no VLM needed)

The output is a plain-text context block injected into the Jarvis
system prompt via aurora_memory / brain._build_system_prompt().
"""

import os
import pathlib
import threading
import time

BASE_DIR = pathlib.Path(__file__).parent.parent

# ── Screen capture ─────────────────────────────────────────────────────────────

def capture_screen(region=None) -> "np.ndarray | None":
    """Grab a screenshot of the local display. Returns BGR numpy array or None."""
    try:
        import mss
        import numpy as np
        with mss.mss() as sct:
            monitor = sct.monitors[1]   # primary monitor
            if region:
                monitor = {**monitor, **region}
            shot = sct.grab(monitor)
            img  = np.array(shot)
            # mss returns BGRA — drop alpha
            return img[:, :, :3]
    except ImportError:
        pass

    # fallback: scrot → temp file → cv2.imread
    try:
        import subprocess, tempfile, cv2
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        subprocess.run(["scrot", "-z", tmp], timeout=3, check=True,
                       stderr=subprocess.DEVNULL)
        img = cv2.imread(tmp)
        os.unlink(tmp)
        return img
    except Exception:
        pass

    return None


def screen_to_text(frame=None) -> str:
    """
    Grab screen (or use supplied frame) and return OCR text.
    Uses easyocr if available, falls back to pytesseract, then returns
    '[screen capture unavailable]' if neither is installed.
    """
    if frame is None:
        frame = capture_screen()
    if frame is None:
        return "[screen capture unavailable]"

    # ── easyocr ──────────────────────────────────────────────────────────────
    try:
        import easyocr
        if not hasattr(screen_to_text, "_reader"):
            screen_to_text._reader = easyocr.Reader(["en"], gpu=True, verbose=False)
        results = screen_to_text._reader.readtext(frame, detail=0, paragraph=True)
        return "\n".join(results).strip() or "[screen: no text detected]"
    except ImportError:
        pass

    # ── pytesseract ───────────────────────────────────────────────────────────
    try:
        import pytesseract
        from PIL import Image
        import cv2
        gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        pil   = Image.fromarray(gray)
        text  = pytesseract.image_to_string(pil).strip()
        return text or "[screen: no text detected]"
    except ImportError:
        pass

    return "[screen: OCR not installed — pip install easyocr or pytesseract]"


# ── Webcam ─────────────────────────────────────────────────────────────────────

def webcam_snapshot(device_path: str = None) -> "np.ndarray | None":
    """Grab one frame from the laptop webcam. Returns BGR array or None."""
    if device_path is None:
        # auto-detect: prefer non-capture-card device
        from core.mode import _video_devices, _CAPTURE_CARD_NAMES
        for dev in _video_devices():
            if not any(k in dev["name"] for k in _CAPTURE_CARD_NAMES):
                device_path = dev["path"]
                break
    if device_path is None:
        return None

    try:
        import cv2
        cap = cv2.VideoCapture(device_path)
        if not cap.isOpened():
            return None
        ret, frame = cap.read()
        cap.release()
        return frame if ret else None
    except Exception:
        return None


# ── Unified desktop context block ─────────────────────────────────────────────

def desktop_context(include_webcam: bool = False, max_chars: int = 800) -> str:
    """
    Return a text block describing the current state of the machine.
    Suitable for injection into the Jarvis system prompt.

    Example output:
        ## Desktop Context (2026-08-14T09:00:00)
        ### Screen (partial OCR)
        Firefox - AKSUMAEL Dashboard
        CPU: 34%  RAM: 6.1GB  ...
        ### Webcam
        [1 face detected]
    """
    ts     = time.strftime("%Y-%m-%dT%H:%M:%S")
    parts  = [f"## Desktop Context ({ts})"]

    # Screen
    screen_text = screen_to_text()
    if len(screen_text) > max_chars:
        screen_text = screen_text[:max_chars] + "…"
    parts.append(f"### Screen\n{screen_text}")

    # Webcam (optional — face count only, no image sent to LLM)
    if include_webcam:
        frame = webcam_snapshot()
        if frame is not None:
            face_info = _count_faces(frame)
            parts.append(f"### Webcam\n{face_info}")

    return "\n".join(parts)


def _count_faces(frame) -> str:
    """Detect faces in a webcam frame, return plain-text summary."""
    try:
        import cv2
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        clf   = cv2.CascadeClassifier(cascade_path)
        gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = clf.detectMultiScale(gray, 1.1, 4)
        n = len(faces) if hasattr(faces, "__len__") else 0
        return f"{n} face(s) detected"
    except Exception as e:
        return f"[face detection error: {e}]"


# ── Background watcher ────────────────────────────────────────────────────────

class DesktopWatcher:
    """
    Background thread that periodically captures screen context and
    writes it to data/desktop_context.txt for the Jarvis brain to read.
    Interval: 30 seconds by default.
    """
    def __init__(self, interval: float = 30.0, output_path: pathlib.Path = None):
        self.interval    = interval
        self.output_path = output_path or (BASE_DIR / "data" / "desktop_context.txt")
        self._stop       = threading.Event()
        self._thread     = threading.Thread(target=self._run, daemon=True)

    def start(self):
        print(f"[DESKTOP_VIS] watcher started (interval={self.interval}s)")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                ctx = desktop_context()
                self.output_path.parent.mkdir(parents=True, exist_ok=True)
                self.output_path.write_text(ctx)
            except Exception as e:
                print(f"[DESKTOP_VIS] error: {e}")


if __name__ == "__main__":
    print(desktop_context(include_webcam=True))
