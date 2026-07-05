# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Screen Capture                     ║
# ║  Rybozen HDMI capture card via V4L2                 ║
# ╚══════════════════════════════════════════════════════╝

import cv2
import config


class ScreenCapture:
    def __init__(self):
        self.cap = None
        self._init()

    def _init(self):
        idx = config.CAMERA_INDEX
        if idx < 0:
            idx = self._find_capture_card()
        self.cap = cv2.VideoCapture(idx)
        if not self.cap.isOpened():
            print(f'[CAPTURE] ⚠ could not open /dev/video{idx}')
            print('  Checklist:')
            print('  • Capture card USB plugged into the Pi?')
            print('  • HDMI source connected and powered on?')
            print('  • Run: v4l2-ctl --list-devices')
            print('  • Permissions: sudo usermod -aG video $USER (re-login)')
            return
        # Request 1080p — card falls back to its max if unsupported
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1920)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        # YUY2 is the Rybozen's native format
        self.cap.set(cv2.CAP_PROP_FOURCC,
                     cv2.VideoWriter_fourcc(*'YUYV'))
        w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f'[CAPTURE] capture card @ {w}×{h} (/dev/video{idx})')

    def _find_capture_card(self) -> int:
        """Probe /dev/video0-3, return first device that delivers a frame."""
        for idx in range(4):
            cap = cv2.VideoCapture(idx)
            if cap.isOpened():
                ret, _ = cap.read()
                cap.release()
                if ret:
                    print(f'[CAPTURE] auto-detected card at /dev/video{idx}')
                    return idx
        print('[CAPTURE] auto-detect failed — defaulting to /dev/video0')
        return 0

    def capture(self):
        """Return a raw BGR frame or None."""
        if self.cap is None or not self.cap.isOpened():
            return None
        ret, frame = self.cap.read()
        return frame if ret else None

    def capture_small(self, width: int = 640):
        """Capture and downscale for faster YOLO/LLM processing."""
        frame = self.capture()
        if frame is None:
            return None
        h, w = frame.shape[:2]
        if w <= width:
            return frame
        scale = width / w
        return cv2.resize(frame, (width, int(h * scale)),
                          interpolation=cv2.INTER_AREA)

    def release(self):
        if self.cap:
            self.cap.release()
