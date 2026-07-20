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
import textwrap
import cv2
import config


# ─────────────────────────────────────────────────────────────────────────────
# Inner-monologue strip — thread-safe queue + rolling buffer + typewriter.
#
# Monologue text is generated on background threads (cognitive.py's LLM
# calls, the overseer, ...) at unpredictable moments. push_monologue_line()
# is the single thread-safe entry point any of them call; poll_display()
# (main thread, every frame) drains the queue, keeps the last
# MONOLOGUE_MAX_LINES raw lines, and reveals the newest one character at a
# time so the strip reads as AKSUMAEL typing live instead of a caption that
# only ever shows the single latest snapshot of text.
MONOLOGUE_MAX_LINES       = 8
MONOLOGUE_WRAP_WIDTH      = 58
MONOLOGUE_CHARS_PER_FRAME = 2

_monologue_queue  = queue.Queue()
_monologue_buffer = []   # raw (unwrapped) lines, oldest first, len <= MONOLOGUE_MAX_LINES
_monologue_typed  = 0    # chars revealed so far of the newest (still-typing) line


def push_monologue_line(text: str):
    """Push a new inner-monologue line onto the display strip.

    Thread-safe — call from any thread that generates AKSUMAEL's internal
    monologue (overseer directives, cognitive/LLM thought generation, FSM
    reasoning, ...):

        from core.capture import push_monologue_line
        push_monologue_line('heading toward the diamond ore')
    """
    text = (text or '').strip()
    if text:
        _monologue_queue.put(text)


def _drain_monologue_queue():
    """Move newly-pushed lines from the queue into the rolling buffer.
    Main-thread only — called from poll_display() once per frame."""
    global _monologue_typed
    appended = False
    while True:
        try:
            line = _monologue_queue.get_nowait()
        except queue.Empty:
            break
        _monologue_buffer.append(line)
        del _monologue_buffer[:-MONOLOGUE_MAX_LINES]
        appended = True
    if appended:
        _monologue_typed = 0   # a new line arrived — it starts typing from scratch


def _monologue_render_lines() -> list:
    """Advance the typewriter animation by one frame and return the wrapped
    display lines (oldest first; the newest may still be mid-typing)."""
    global _monologue_typed
    if not _monologue_buffer:
        return []
    *done, newest = _monologue_buffer
    _monologue_typed = min(_monologue_typed + MONOLOGUE_CHARS_PER_FRAME, len(newest))

    lines = []
    for text in done:
        lines.extend(textwrap.wrap(text, width=MONOLOGUE_WRAP_WIDTH) or [''])
    lines.extend(textwrap.wrap(newest[:_monologue_typed], width=MONOLOGUE_WRAP_WIDTH) or [''])
    return lines


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

    # How long to keep retrying the initial device open before giving up.
    # The capture card may not be plugged in yet (or still enumerating) when
    # AKSUMAEL starts — waiting here means an unattended restart recovers on
    # its own instead of running blind for the rest of the session.
    OPEN_RETRY_INTERVAL_SEC = 30
    OPEN_RETRY_TIMEOUT_SEC  = 300

    def _open(self):
        """Try to open the capture device, retrying every OPEN_RETRY_INTERVAL_SEC
        for up to OPEN_RETRY_TIMEOUT_SEC. Returns an opened cv2.VideoCapture, or
        None if it never appeared and the caller should give up."""
        waited = 0
        while not self._stop.is_set():
            cap = cv2.VideoCapture(self._dev, cv2.CAP_V4L2)
            if cap.isOpened():
                return cap
            cap.release()
            if waited >= self.OPEN_RETRY_TIMEOUT_SEC:
                print(f'[CAPTURE] ⚠ CaptureThread: /dev/video{self._dev} still not '
                      f'available after {waited}s — giving up (will retry on next restart)')
                return None
            print(f'[CAPTURE] /dev/video{self._dev} not available yet — '
                  f'retrying in {self.OPEN_RETRY_INTERVAL_SEC}s ({waited}s / {self.OPEN_RETRY_TIMEOUT_SEC}s)')
            self._stop.wait(self.OPEN_RETRY_INTERVAL_SEC)
            waited += self.OPEN_RETRY_INTERVAL_SEC
        return None

    def run(self):
        cap = self._open()
        if cap is None:
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
        # Plain bool, not lock-guarded — same convention as pipeline's
        # _ctrl_connected/_human_mode flags. Set by set_track_mode() from
        # the main decision loop when the FSM enters/leaves HUNT (see
        # core/fsm.py + vision/target_lock.py); read here every inference
        # cycle to pick predict() vs track(persist=True).
        self._track_mode = False

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

            objects = self._yolo.detect(frame, track=self._track_mode)

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
        self._dq         = display_queue
        self._ui         = labeling_ui
        self._stop       = threading.Event()
        self._lock       = threading.Lock()
        self._last_frame = None
        self._last_objs  = []
        self.quit        = False   # set by poll_display() when user presses 'q'

    def stop(self):
        self._stop.set()

    def run(self):
        # DisplayThread no longer calls cv2.imshow() — Qt requires imshow to
        # run on the main thread.  This thread now only drains the display queue
        # and caches the latest (frame, objects) pair for poll_display() to use.
        print('[DISPLAY] DisplayThread started (frame buffer only — imshow on main thread)')
        while not self._stop.is_set():
            try:
                frame, objs = self._dq.get(timeout=0.05)
                with self._lock:
                    self._last_frame = frame
                    self._last_objs  = objs
            except queue.Empty:
                pass
        print('[DISPLAY] DisplayThread stopped')

    def get_display_frame(self):
        """Return (frame, objects) of the most recent YOLO result, or (None, [])."""
        with self._lock:
            return self._last_frame, list(self._last_objs)


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
        self._overlay_text = ''
        self._fsm_text = ''
        self._ctrl_connected = False
        self._human_mode = False

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

    def set_overlay_text(self, text: str):
        """Set the inner-monologue caption. When a LabelingUI is attached,
        forward straight to it — it draws the caption in its own black
        letterbox strip below the video (ui/labeling.py::_draw_monologue).
        Only the plain cv2.imshow fallback (no LabelingUI) burns it onto
        the raw frame itself via _draw_overlay, since that path has no
        separate canvas area to put it in."""
        if self.display._ui is not None:
            self.display._ui.set_overlay_text(text)
            self._overlay_text = ''
        else:
            self._overlay_text = text or ''

    def set_fsm_state(self, state_name: str):
        """Set the FSM state label drawn top-left by poll_display(). Called
        from the main decision loop — plain attribute write, no lock needed
        since poll_display() only ever runs on that same main thread."""
        self._fsm_text = state_name or ''

    def set_track_mode(self, enabled: bool):
        """Enable/disable Ultralytics .track(persist=True) mode on the YOLO
        inference thread — HUNT turns this on so ByteTrack assigns
        persistent track_ids for vision/target_lock.py's TargetLock; every
        other state leaves it off and gets plain predict(). Called from the
        main decision loop right after each fsm.tick()."""
        self.yolo_t._track_mode = bool(enabled)

    def set_controller_status(self, connected: bool, human_mode: bool):
        """Set the controller/mode indicator drawn top-right by
        poll_display(). Called from the main decision loop."""
        self._ctrl_connected = bool(connected)
        self._human_mode = bool(human_mode)

    def _draw_hud(self, frame):
        """Burn the FSM-state label (top-left) and controller/mode indicator
        (top-right) onto frame. Pure drawing over an in-memory array — no
        I/O, safe to call every poll_display() tick on the main thread."""
        if frame is None:
            return
        h, w = frame.shape[:2]

        if self._fsm_text:
            cv2.putText(frame, self._fsm_text, (10, 34), cv2.FONT_HERSHEY_SIMPLEX,
                        1.0, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(frame, self._fsm_text, (10, 34), cv2.FONT_HERSHEY_SIMPLEX,
                        1.0, (255, 255, 255), 2, cv2.LINE_AA)

        if not self._ctrl_connected:
            color, label = (0, 0, 255), 'NO CTRL'      # red (BGR) — no controller
        elif self._human_mode:
            color, label = (0, 220, 255), 'HUMAN'      # yellow — Scott driving
        else:
            color, label = (0, 200, 0), 'AI'           # green — AI driving
        box_w, box_h = 110, 34
        x1, y1 = w - box_w - 10, 10
        x2, y2 = w - 10, 10 + box_h
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, -1)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 0), 2)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        tx = x1 + (box_w - tw) // 2
        ty = y1 + (box_h + th) // 2
        cv2.putText(frame, label, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (0, 0, 0), 2, cv2.LINE_AA)

    def _draw_overlay(self, frame):
        """Burn self._overlay_text onto frame as a wrapped, outlined caption.
        Text arrives pre-wrapped (one physical line per '\\n') from the
        monologue buffer — see push_monologue_line() / _monologue_render_lines()."""
        if not self._overlay_text or frame is None:
            return
        h, w = frame.shape[:2]
        lines = self._overlay_text.split('\n')
        lines = lines[-3:]   # keep it to the last 3 lines
        font        = cv2.FONT_HERSHEY_SIMPLEX
        scale       = 0.5
        thickness   = 1
        line_height = 18
        y = h - 10 - line_height * (len(lines) - 1)
        for line in lines:
            cv2.putText(frame, line, (10, y), font, scale, (0, 0, 0), thickness + 2, cv2.LINE_AA)
            cv2.putText(frame, line, (10, y), font, scale, (255, 255, 255), thickness, cv2.LINE_AA)
            y += line_height

    def poll_display(self, window_name: str = 'AKSUMAEL') -> bool:
        """
        Call this from the MAIN THREAD each tick to update the display window.

        Pulls the latest (frame, objects) from DisplayThread, calls cv2.imshow()
        on the main thread (required by Qt/OpenCV), and checks for 'q' keypress.

        Returns False when the user presses 'q' (signal to exit), True otherwise.
        """
        frame, objs = self.display.get_display_frame()
        _drain_monologue_queue()
        self.set_overlay_text('\n'.join(_monologue_render_lines()))
        self._draw_overlay(frame)
        self._draw_hud(frame)
        if frame is None:
            key = self._safe_wait_key()
        elif self.display._ui is not None:
            # LabelingUI handles its own rendering; call update+render here.
            self.display._ui.update(frame, objs)
            if not self.display._ui.render():
                self.display.quit = True
                return False
            key = self._safe_wait_key()
        elif config.ENABLE_DISPLAY_UI:
            # Plain fallback window when no LabelingUI was given at all.
            # Gated the same way as LabelingUI — headless rigs (no monitor;
            # see config.ENABLE_DISPLAY_UI) have no GTK/Qt/Cocoa backend for
            # cv2.imshow to open a window against.
            cv2.imshow(window_name, frame)
            key = self._safe_wait_key()
        else:
            key = self._safe_wait_key()

        if key == ord('q'):
            self.display.quit = True
            return False
        return True

    @staticmethod
    def _safe_wait_key() -> int:
        """cv2.waitKey requires a GUI backend (GTK/Qt/Cocoa); on a headless
        rig (see config.ENABLE_DISPLAY_UI) there's no window to poll for a
        keypress at all, so skip the call rather than attempt-then-catch it
        every single tick. The try/except stays as a defensive fallback for
        the case a display IS enabled but the backend still isn't there."""
        if not config.ENABLE_DISPLAY_UI:
            return 0xFF   # 'no key pressed'
        try:
            return cv2.waitKey(1) & 0xFF
        except cv2.error:
            return 0xFF

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
