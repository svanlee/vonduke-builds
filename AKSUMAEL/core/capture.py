# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Threaded Capture / YOLO / Display  ║
# ║                                                        ║
# ║  Three daemon threads decouple frame acquisition,      ║
# ║  YOLO inference, and display so the viz window stays   ║
# ║  live at ~30 fps regardless of the LLM tick rate.      ║
# ║                                                        ║
# ║  CaptureThread — V4L2/MJPEG reader; latest raw+small   ║
# ║  YOLOThread    — GPU inference at full speed; feeds dq ║
# ║  DisplayThread — cv2.imshow via LabelingUI at ~30 fps  ║
# ╚══════════════════════════════════════════════════════╝

import threading
import queue
import time
import cv2


# ─────────────────────────────────────────────────────────────────────────────
class CaptureThread(threading.Thread):
    """
    Continuously reads frames from the capture card using the V4L2 backend
    with MJPEG codec for fastest decode.  Only the latest frame is kept —
    old frames are discarded immediately so consumers always get the freshest
    image.

    Key settings applied to the capture device:
      • CAP_PROP_BUFFERSIZE = 1    → minimise kernel buffer lag
      • FOURCC = MJPG              → hardware JPEG decode, much faster than YUYV
      • 1920×1080                  → full HDMI resolution from the HP machine
    """

    def __init__(self, device_index: int = 2):
        super().__init__(name='CaptureThread', daemon=True)
        self._dev   = device_index
        self._lock  = threading.Lock()
        self._raw   = None    # latest full-res (1920×1080) BGR frame
        self._small = None    # latest 640-wide BGR frame (for YOLO / LLM)
        self._stop  = threading.Event()

    # ── Public API ────────────────────────────────────────────────────────

    def get_latest_raw(self):
        """Full-resolution frame, or None before the first frame arrives."""
        with self._lock:
            return self._raw

    def get_latest_small(self):
        """640-wide downscaled frame, or None."""
        with self._lock:
            return self._small

    def stop(self):
        self._stop.set()

    # ── Thread body ───────────────────────────────────────────────────────

    def run(self):
        cap = cv2.VideoCapture(self._dev, cv2.CAP_V4L2)
        if not cap.isOpened():
            print(f'[CAPTURE] ⚠ CaptureThread: cannot open /dev/video{self._dev}')
            return

        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)                              # min lag
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))   # fast decode
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)

        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f'[CAPTURE] CaptureThread @ {w}×{h} MJPG /dev/video{self._dev}')

        while not self._stop.is_set():
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.005)
                continue

            # Pre-compute the small frame here so YOLO thread pays no resize cost
            fh, fw = frame.shape[:2]
            scale  = 640 / fw
            small  = cv2.resize(frame, (640, int(fh * scale)),
                                interpolation=cv2.INTER_AREA)

            with self._lock:
                self._raw   = frame
                self._small = small

        cap.release()
        print('[CAPTURE] CaptureThread stopped')


# ─────────────────────────────────────────────────────────────────────────────
class YOLOThread(threading.Thread):
    """
    Reads the latest 640-wide frame from CaptureThread and runs YOLO inference
    at full GPU speed — no artificial sleep or tick gating.

    Results are:
      • stored in self._frame / self._objects for the decision loop to poll
      • pushed to display_queue for DisplayThread (maxsize=1; stale frames
        are dropped so the display always shows the most recent inference)
    """

    def __init__(self, yolo_detector, capture: CaptureThread,
                 display_queue: queue.Queue):
        super().__init__(name='YOLOThread', daemon=True)
        self._yolo    = yolo_detector
        self._cap     = capture
        self._dq      = display_queue
        self._lock    = threading.Lock()
        self._frame   = None
        self._objects = []
        self._stop    = threading.Event()

    # ── Public API ────────────────────────────────────────────────────────

    def get_latest(self):
        """
        Returns (small_frame, objects) — safe to call from any thread.
        Returns (None, []) before the first inference completes.
        """
        with self._lock:
            return self._frame, list(self._objects)

    def stop(self):
        self._stop.set()

    # ── Thread body ───────────────────────────────────────────────────────

    def run(self):
        print('[YOLO] YOLOThread started (GPU, no throttle)')
        while not self._stop.is_set():
            frame = self._cap.get_latest_small()
            if frame is None:
                time.sleep(0.01)
                continue

            objects = self._yolo.detect(frame)

            with self._lock:
                self._frame   = frame
                self._objects = objects

            # Push to display queue; discard the stale entry if consumer is behind
            item = (frame, objects)
            if self._dq.full():
                try:
                    self._dq.get_nowait()
                except queue.Empty:
                    pass
            try:
                self._dq.put_nowait(item)
            except queue.Full:
                pass   # benign — next inference will overwrite

        print('[YOLO] YOLOThread stopped')


# ─────────────────────────────────────────────────────────────────────────────
class DisplayThread(threading.Thread):
    """
    Consumes (frame, objects) from the display queue and renders at ~30 fps.

    If a LabelingUI instance is provided the thread delegates to
    ``ui.update()`` + ``ui.render()``, preserving the full overlay (bbox
    drawing, HUD, sidebar, mouse/key handling).  The main decision loop can
    still read ``ui.paused``, ``ui.quit``, and ``ui.consume_reward()``
    directly — they are plain Python attributes protected by the GIL.

    If no UI is given the thread falls back to a plain ``cv2.imshow()``
    window named ``'AKSUMAEL_LIVE'``.
    """

    WINDOW     = 'AKSUMAEL_LIVE'
    TARGET_FPS = 30

    def __init__(self, display_queue: queue.Queue, labeling_ui=None):
        super().__init__(name='DisplayThread', daemon=True)
        self._dq   = display_queue
        self._ui   = labeling_ui
        self._stop = threading.Event()
        self.quit  = False   # True when user presses 'q' (or ui.quit is set)

    def stop(self):
        self._stop.set()

    def run(self):
        interval   = 1.0 / self.TARGET_FPS
        last_frame = None
        last_objs  = []

        # Fallback window (only created if no LabelingUI)
        if self._ui is None:
            cv2.namedWindow(self.WINDOW, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(self.WINDOW, 960, 540)

        print(f'[DISPLAY] DisplayThread started @ {self.TARGET_FPS} fps')

        while not self._stop.is_set():
            t0 = time.time()

            # Pull latest frame/objects; if nothing new, keep showing last frame
            try:
                last_frame, last_objs = self._dq.get(timeout=0.05)
            except queue.Empty:
                pass

            if last_frame is not None:
                if self._ui is not None:
                    self._ui.update(last_frame, last_objs)
                    if not self._ui.render():
                        self.quit = True
                        break
                else:
                    cv2.imshow(self.WINDOW, last_frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord('q'):
                        self.quit = True
                        break

            sleep = interval - (time.time() - t0)
            if sleep > 0:
                time.sleep(sleep)

        if self._ui is None:
            try:
                cv2.destroyWindow(self.WINDOW)
            except Exception:
                pass

        print('[DISPLAY] DisplayThread stopped')


# ─────────────────────────────────────────────────────────────────────────────
class VideoCapturePipeline:
    """
    Wires together CaptureThread, YOLOThread, and DisplayThread into a single
    object that the main decision loop interacts with.

    Example usage in runtime.py::

        pipeline = VideoCapturePipeline(yolo, ui, device_index=config.CAMERA_INDEX)
        pipeline.start()          # starts all three daemon threads
        ...
        frame   = pipeline.latest_small_frame   # 640-wide BGR for LLM / survey
        raw     = pipeline.latest_raw_frame      # full-res for F3 OCR
        objects = pipeline.latest_objects        # latest YOLO detections
        if pipeline.quit:
            break
        ...
        pipeline.stop()           # signals threads to exit (they are daemons anyway)

    ``release()`` is provided as a drop-in replacement for ScreenCapture.release().
    """

    def __init__(self, yolo_detector, labeling_ui=None, device_index: int = 2):
        self._dq     = queue.Queue(maxsize=1)
        self.capture = CaptureThread(device_index)
        self.yolo_t  = YOLOThread(yolo_detector, self.capture, self._dq)
        self.display = DisplayThread(self._dq, labeling_ui)

    # ── Forwarded properties ──────────────────────────────────────────────

    @property
    def latest_small_frame(self):
        """640-wide BGR frame from latest YOLO inference cycle."""
        f, _ = self.yolo_t.get_latest()
        return f

    @property
    def latest_raw_frame(self):
        """Full-resolution BGR frame (useful for F3 OCR after key press)."""
        return self.capture.get_latest_raw()

    @property
    def latest_objects(self):
        """List of YOLO detection dicts from the most recent inference."""
        _, o = self.yolo_t.get_latest()
        return o

    @property
    def quit(self):
        """True when the user has pressed 'q' in the display window."""
        return self.display.quit

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self):
        """Start all three threads (non-blocking)."""
        self.capture.start()
        self.yolo_t.start()
        self.display.start()

    def stop(self):
        """Signal all three threads to exit."""
        self.display.stop()
        self.yolo_t.stop()
        self.capture.stop()

    def release(self):
        """Alias for ScreenCapture.release() compatibility."""
        self.stop()
