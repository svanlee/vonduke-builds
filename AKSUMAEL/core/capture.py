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
import numpy as np
import config


# ─────────────────────────────────────────────────────────────────────────────
# Camera probing — find *a* working device, or report that there is none.
#
# AKSUMAEL's vision source used to be assumed present: CaptureThread opened
# config.CAMERA_INDEX and, if that failed, retried the same index for five
# minutes and then gave up, leaving the decision loop blind forever with no
# way back. After the 2026-08-08 outage took /dev/video2 (the capture card)
# away entirely, that meant a bot that never ticked again.
#
# Now: try CAMERA_INDEX first, then each of config.CAMERA_FALLBACK_INDICES,
# and accept the first device that both opens AND hands back a real frame.
# If none do, the caller runs vision-less and re-probes on a timer.

def _candidate_indices() -> list:
    """CAMERA_INDEX first, then the configured fallbacks, de-duplicated."""
    order = [config.CAMERA_INDEX]
    order.extend(getattr(config, 'CAMERA_FALLBACK_INDICES', []) or [])
    seen, out = set(), []
    for idx in order:
        if idx is None or idx < 0 or idx in seen:
            continue
        seen.add(idx)
        out.append(idx)
    return out


def _frame_is_real(frame) -> bool:
    """True if a frame looks like actual video rather than a black/empty one.

    A device with no source attached (capture card, HDMI unplugged) still
    opens and still read()s successfully — it just returns black. Accepting
    it would look exactly like working vision to everything downstream, so
    require some actual luminance before calling the probe a success."""
    if frame is None or frame.size == 0:
        return False
    return float(frame.mean()) >= getattr(config, 'CAMERA_MIN_FRAME_MEAN', 8.0)


def probe_camera(index: int, warmup_frames: int = 8):
    """Try to open /dev/video<index> and pull a real frame off it.

    Returns the opened cv2.VideoCapture on success (caller owns it), or None.
    The first frames off a freshly-opened V4L2 device are routinely black
    while it settles, so read a few before judging."""
    cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap.release()
        return None
    for _ in range(warmup_frames):
        ret, frame = cap.read()
        if ret and _frame_is_real(frame):
            return cap
        time.sleep(0.05)
    cap.release()
    return None


def probe_cameras(quiet: bool = False):
    """Sweep every candidate index; return (index, cap) or (None, None).

    OpenCV logs two WARN lines per failed open straight from C++; silence
    them for the duration of the sweep so a routine 5-minute re-probe of a
    device that is simply gone doesn't fill the log."""
    try:
        _prev_log = cv2.utils.logging.getLogLevel()
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_SILENT)
    except Exception:
        _prev_log = None
    try:
        for idx in _candidate_indices():
            cap = probe_camera(idx)
            if cap is not None:
                if not quiet:
                    tag = 'configured' if idx == config.CAMERA_INDEX else 'fallback'
                    print(f'[CAMERA] using /dev/video{idx} ({tag})')
                return idx, cap
    finally:
        if _prev_log is not None:
            try:
                cv2.utils.logging.setLogLevel(_prev_log)
            except Exception:
                pass
    return None, None


# ─────────────────────────────────────────────────────────────────────────────
# Screen grab — the X display, shaped like a camera.
#
# Two distinct jobs, both served by ScreenshotSource:
#
#   1. Last resort in the camera chain. When neither the capture card nor any
#      webcam opens, "no camera" used to mean "no eyes", which is only the
#      right conclusion for a bot that is *only* a game player. AKSUMAEL is a
#      general agent first, so it should still be able to see the machine it
#      is running on.
#   2. Always-on desktop watch (DesktopCaptureThread below), which runs
#      *alongside* the capture card rather than instead of it — the card
#      carries the game, the screen grab carries AKSUMAEL's own desktop, and
#      the higher-level vision route wants both at once.
#
# What this is NOT: the game. Minecraft runs on a separate PC and reaches
# AKSUMAEL only through the HDMI capture card, so a grab of :0 shows the
# Linux desktop — terminals, logs, browser — and never the world. That is
# why CaptureThread tracks `source_kind` separately from `vision_available`:
# YOLO should keep running on these frames, but every Minecraft heuristic in
# core/runtime.py is gated on `game_vision` so a desktop frame can never be
# mistaken for a game frame.

class ScreenshotSource:
    """An X display exposed through the slice of the cv2.VideoCapture API
    that CaptureThread actually uses — read(), release(), isOpened(), get().

    Shaping it like a camera keeps the streaming loop in run() identical
    whether it holds a capture card or a screen grab, so there is exactly one
    frame path to reason about instead of two.

    read() is rate-limited: a 1920×1080 grab costs ~13ms, so an ungated loop
    would spin at ~75fps and burn a core to re-photograph a mostly-static
    desktop. It blocks until the next frame is due instead.
    """

    def __init__(self, display: str = ':0', fps: float = 20.0):
        self.display  = display
        self.label    = f'screenshot fallback ({display})'
        self._grab    = None
        self._interval = 1.0 / max(float(fps), 0.1)
        self._next_due = 0.0
        self._w = self._h = 0
        self._closed = False

    # ── cv2.VideoCapture-shaped surface ───────────────────────────────────

    def isOpened(self) -> bool:
        return self._grab is not None and not self._closed

    def get(self, prop):
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._w)
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._h)
        return 0.0

    def set(self, prop, value):
        return False   # a screen has no V4L2 properties to negotiate

    def release(self):
        self._closed = True
        self._grab   = None

    def read(self):
        """(True, BGR frame) or (False, None) — same contract as cv2.read()."""
        if not self.isOpened():
            return False, None
        now = time.monotonic()
        if now < self._next_due:
            time.sleep(self._next_due - now)
        self._next_due = time.monotonic() + self._interval
        frame = self._grab_bgr()
        return (frame is not None), frame

    # ── Internals ─────────────────────────────────────────────────────────

    def open(self) -> bool:
        """Bind to the display and prove it hands back a real frame.

        Pillow is the only screen-grab backend present on this rig (no scrot,
        no mss, no python-xlib), and ImageGrab needs a Pillow built with X11
        support — hence the import and the trial grab rather than trusting
        either to work."""
        try:
            from PIL import ImageGrab
        except Exception:
            return False
        self._closed = False
        self._grab   = ImageGrab.grab
        frame = self._grab_bgr()
        if frame is None:
            self.release()
            return False
        self._h, self._w = frame.shape[:2]
        return True

    def _grab_bgr(self):
        """One frame as a BGR numpy array — the format every camera in this
        pipeline delivers and everything downstream (YOLO, the HUD reader,
        the color detector) assumes. PIL hands back RGB, so the channel swap
        is mandatory, not cosmetic."""
        try:
            img = self._grab(xdisplay=self.display)
        except Exception:
            return None
        if img is None:
            return None
        arr = np.asarray(img.convert('RGB'))
        if arr.size == 0:
            return None
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def probe_screenshot(quiet: bool = False, fps: float = None):
    """Return an opened ScreenshotSource, or None if the screen is unusable.

    Applies the same "is this a real frame" floor as a camera probe: a
    blanked or DPMS-off display grabs as solid black, which carries no more
    information than a capture card with nothing plugged into it."""
    if not getattr(config, 'SCREENSHOT_FALLBACK_ENABLED', True):
        return None
    display = getattr(config, 'SCREENSHOT_DISPLAY', ':0')
    if fps is None:
        fps = getattr(config, 'SCREENSHOT_FPS', 20.0)
    src = ScreenshotSource(display, fps)
    if not src.open():
        if not quiet:
            print(f'[CAMERA] screen grab of {display} unavailable')
        return None
    ok, frame = src.read()
    if not ok or not _frame_is_real(frame):
        src.release()
        if not quiet:
            print(f'[CAMERA] screen grab of {display} is blank — ignoring')
        return None
    return src


# ─────────────────────────────────────────────────────────────────────────────
class DesktopCaptureThread(threading.Thread):
    """Always-on grab of AKSUMAEL's own screen, running *concurrently* with
    whatever CaptureThread is doing.

    The capture card and the desktop are not alternatives — the card carries
    the game and this carries the machine AKSUMAEL is running on, and the
    higher-level vision route (core/vision_router.py's LLM calls) wants both
    available at once. Restricting the bot to one or the other is what made
    "capture card unplugged" collapse into "no eyes at all".

    Deliberately NOT fed to YOLO. The detector's weights are Minecraft
    classes; run against window chrome and terminal text they emit
    low-confidence ore/mob boxes on desktop furniture, which is noise
    entering the same channel the game detections use. Desktop frames are
    for the LLM path, which can actually describe what it is looking at.

    Runs at its own (much slower) cadence — config.DESKTOP_WATCH_FPS — since
    its consumer ticks in seconds, not milliseconds.
    """

    def __init__(self):
        super().__init__(name='DesktopCaptureThread', daemon=True)
        self._lock = threading.Lock()
        self._raw   = None
        self._small = None
        self._stop_evt = threading.Event()
        self.available = False

    def get_latest_raw(self):
        with self._lock:
            return self._raw

    def get_latest_small(self):
        with self._lock:
            return self._small

    def stop(self):
        self._stop_evt.set()

    def run(self):
        fps = getattr(config, 'DESKTOP_WATCH_FPS', 4.0)
        retry_sec = getattr(config, 'CAMERA_REPROBE_SEC', 300)
        while not self._stop_evt.is_set():
            src = probe_screenshot(quiet=True, fps=fps)
            if src is None:
                # No X display, no Pillow, or the screen is blank. Retry on
                # the same cadence as the camera sweep rather than spinning.
                self.available = False
                self._stop_evt.wait(retry_sec)
                continue

            print(f'[DESKTOP] watching {src.display} @ {fps:g}fps '
                  f'(LLM vision route only — not fed to YOLO)')
            self.available = True
            while not self._stop_evt.is_set():
                ok, frame = src.read()
                if not ok:
                    break
                fh, fw = frame.shape[:2]
                scale  = 640 / fw
                small  = cv2.resize(frame, (640, int(fh * scale)),
                                    interpolation=cv2.INTER_AREA)
                with self._lock:
                    self._raw   = frame
                    self._small = small
            src.release()
            self.available = False
            with self._lock:
                self._raw = self._small = None

        print('[DESKTOP] DesktopCaptureThread stopped')


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
    Continuously reads frames from whichever camera probe_cameras() found,
    using the V4L2 backend with MJPEG codec for fastest decode.  Only the
    latest frame is kept — old frames are discarded immediately so consumers
    always get the freshest image.

    Key settings applied to the capture device:
      • CAP_PROP_BUFFERSIZE = 1    → minimise kernel buffer lag
      • FOURCC = MJPG              → hardware JPEG decode, much faster than YUYV
      • 1920×1080                  → full HDMI resolution from the HP machine

    The thread never exits on a missing camera. If no device can be opened it
    sets ``vision_available = False`` and keeps sweeping every
    config.CAMERA_REPROBE_SEC; if the active device disappears mid-session it
    drops back to the same sweep. Either way vision comes back on its own the
    moment a camera does, with no restart — and the decision loop keeps
    ticking blind in the meantime (see core/runtime.py's `vision_ok`).
    """

    def __init__(self, device_index: int = 2):
        super().__init__(name='CaptureThread', daemon=True)
        self._dev   = device_index
        self._lock  = threading.Lock()
        self._raw   = None    # latest full-res (1920×1080) BGR frame
        self._small = None    # latest 640-wide BGR frame (for YOLO / LLM)
        self._stop_evt  = threading.Event()
        # Plain bools, GIL-protected — same convention as YOLOThread._track_mode
        # and the pipeline's _human_mode. Read every tick by the decision loop
        # (core/runtime.py) to decide whether to run the vision-derived
        # heuristics at all; written only here.
        self.vision_available = False
        self.active_index     = None   # index actually in use, or None
        # Which *kind* of source is feeding frames: 'camera' (capture card or
        # webcam) or 'screenshot' (a grab of the X display). vision_available
        # says frames are flowing; this says what they are of, and the two are
        # not interchangeable — see game_vision below.
        self.source_kind      = None
        self.source_label     = None   # human-readable, e.g. '/dev/video2'
        self._last_nocam_log  = 0.0    # monotonic ts of the last "no camera" line

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def game_vision(self) -> bool:
        """True only when frames are coming off a real capture device.

        The distinction that matters to core/runtime.py: `vision_available`
        answers "is there a frame at all" (YOLO, the LLM route), while this
        answers "is that frame the game" (the night/brightness push, the ore
        color detector, the HUD pixel read, death-and-respawn, torch placing,
        the launcher, the curiosity survey — every heuristic that reads
        Minecraft meaning into pixels). Feeding those a desktop screen grab
        would not blind them, which is recoverable; it would make them
        confidently wrong, which is not."""
        return self.vision_available and self.source_kind == 'camera'

    def get_latest_raw(self):
        """Full-resolution frame, or None before the first frame arrives."""
        with self._lock:
            return self._raw

    def get_latest_small(self):
        """640-wide downscaled frame, or None."""
        with self._lock:
            return self._small

    def stop(self):
        self._stop_evt.set()

    # ── Thread body ───────────────────────────────────────────────────────

    # Consecutive failed read()s tolerated before concluding the device went
    # away mid-session (unplugged, or the USB bus reset) and dropping back to
    # a full probe sweep. ~1s at the 5ms retry interval below.
    READ_FAIL_LIMIT = 200

    def _log_no_camera(self):
        """Report vision-less mode, at most once per CAMERA_LOG_THROTTLE_SEC.

        The probe loop wakes far more often than it should speak — without
        this throttle a missing camera produced a log line every retry (and,
        before that, a TTS 'no frame' announcement every single tick)."""
        now = time.monotonic()
        throttle = getattr(config, 'CAMERA_LOG_THROTTLE_SEC', 60)
        if now - self._last_nocam_log >= throttle:
            self._last_nocam_log = now
            print('[CAMERA] No camera available — running in vision-less mode')

    def _configure(self, cap, label: str, kind: str):
        """Apply the low-latency capture settings and announce the geometry.

        The 1920×1080 request is what the capture card delivers; a webcam
        fallback simply reports back whatever it actually supports (typically
        640×480), which is fine — everything downstream works off the 640-wide
        `small` frame computed in run(). A screen grab has no V4L2 properties
        to negotiate at all, so it only reports the geometry it found."""
        if kind == 'camera':
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)                              # min lag
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))   # fast decode
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1920)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
            codec = 'MJPG'
        else:
            codec = 'X11'
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f'[CAPTURE] CaptureThread @ {w}×{h} {codec} {label}')

    def _acquire(self):
        """Block until a working vision source is found or stop() is called.

        Order: CAMERA_INDEX, then each configured fallback index, then — only
        once every camera has failed — a grab of the X display itself.
        Returns (cap, label, kind, index), or four Nones if the thread was
        asked to stop. Between sweeps the thread sleeps in short slices so
        stop() is still responsive and so the throttled log stays on its own
        cadence rather than the (much slower) probe cadence."""
        first_sweep = True
        while not self._stop_evt.is_set():
            idx, cap = probe_cameras(quiet=False)
            if cap is not None:
                return cap, f'/dev/video{idx}', 'camera', idx

            # No camera anywhere. Before declaring vision-less, fall back to
            # the screen this process is running on — a desktop is not the
            # game, but it is real visual input, and the run() loop keeps
            # sweeping for the capture card so this never becomes permanent.
            shot = probe_screenshot(quiet=not first_sweep)
            if shot is not None:
                print(f'[CAMERA] no camera among {_candidate_indices()} — '
                      f'falling back to {shot.label}; still re-probing for the '
                      f'capture card every '
                      f'{getattr(config, "CAMERA_REPROBE_SEC", 300)}s')
                return shot, shot.label, 'screenshot', None

            if first_sweep:
                print(f'[CAMERA] no working device among '
                      f'{_candidate_indices()} and no usable screen grab — '
                      f'entering vision-less mode; re-probing every '
                      f'{getattr(config, "CAMERA_REPROBE_SEC", 300)}s')
                self._last_nocam_log = time.monotonic()
                first_sweep = False
            else:
                self._log_no_camera()

            deadline = time.monotonic() + getattr(config, 'CAMERA_REPROBE_SEC', 300)
            while not self._stop_evt.is_set() and time.monotonic() < deadline:
                self._stop_evt.wait(5)
                self._log_no_camera()
        return None, None, None, None

    def run(self):
        # Outer loop: acquire a camera, stream from it, and — if it ever goes
        # away — fall straight back to acquiring instead of dying. This is the
        # background re-probe: vision is restored automatically whenever a
        # device reappears, with no restart needed.
        while not self._stop_evt.is_set():
            cap, label, kind, index = self._acquire()
            if cap is None:
                break   # stop() was called while probing

            self._configure(cap, label, kind)
            self.active_index     = index
            self.source_kind      = kind
            self.source_label     = label
            self.vision_available = True

            reprobe_sec = getattr(config, 'CAMERA_REPROBE_SEC', 300)
            next_recheck = time.monotonic() + reprobe_sec
            read_fails = 0
            while not self._stop_evt.is_set():
                # A screen grab is a stand-in, not a destination. Unlike the
                # vision-less path — which re-probes precisely because it has
                # nothing — this source never fails on its own, so without an
                # explicit sweep the bot would sit on the desktop forever and
                # never notice the capture card come back.
                if kind == 'screenshot' and time.monotonic() >= next_recheck:
                    next_recheck = time.monotonic() + reprobe_sec
                    _idx, _cap = probe_cameras(quiet=True)
                    if _cap is not None:
                        # Hand it straight back and let _acquire() re-open it
                        # on the next pass, so there is one place that owns
                        # opening a device instead of two.
                        _cap.release()
                        print(f'[CAMERA] /dev/video{_idx} is back — leaving '
                              f'{label}')
                        break

                ret, frame = cap.read()
                if not ret:
                    read_fails += 1
                    if read_fails >= self.READ_FAIL_LIMIT:
                        print(f'[CAMERA] {label} stopped delivering '
                              f'frames — re-probing')
                        break
                    time.sleep(0.005)
                    continue
                read_fails = 0

                # Pre-compute the small frame here so YOLO thread pays no resize cost
                fh, fw = frame.shape[:2]
                scale  = 640 / fw
                small  = cv2.resize(frame, (640, int(fh * scale)),
                                    interpolation=cv2.INTER_AREA)

                with self._lock:
                    self._raw   = frame
                    self._small = small

            cap.release()
            # Drop the stale frames along with the device — a consumer that
            # keeps polling must see "no vision", not the last image from a
            # camera that has been gone for minutes.
            self.vision_available = False
            self.active_index     = None
            self.source_kind      = None
            self.source_label     = None
            with self._lock:
                self._raw   = None
                self._small = None

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
        self._stop_evt    = threading.Event()
        # Plain bool, not lock-guarded — same convention as pipeline's
        # _ctrl_connected/_human_mode flags. Set by set_track_mode() from
        # the main decision loop when the FSM enters/leaves HUNT (see
        # core/fsm.py + vision/target_lock.py); read here every inference
        # cycle to pick predict() vs track(persist=True).
        self._track_mode = False
        # Lazy GatedReIDBridge — instantiated on first tracked frame.
        # Only fires when track_mode is True (ByteTrack provides track_ids).
        # DINOv2 is heavy (~340 MB); we load it once and reuse.
        self._reid_bridge = None

    # ── Public API ────────────────────────────────────────────────────────

    def get_latest(self):
        """
        Returns (small_frame, objects) — safe to call from any thread.
        Returns (None, []) before the first inference completes.
        """
        with self._lock:
            return self._frame, list(self._objects)

    def stop(self):
        self._stop_evt.set()

    # ── Thread body ───────────────────────────────────────────────────────

    def run(self):
        print('[YOLO] YOLOThread started (GPU, no throttle)')
        _consecutive_errors = 0
        while not self._stop_evt.is_set():
            try:
                # No camera → no inference at all. Running YOLO on nothing
                # (or on a stale frame from a device that has since gone
                # away) would burn GPU and feed the decision loop detections
                # that no longer describe anything real.
                if not self._cap.vision_available:
                    with self._lock:
                        self._frame   = None
                        self._objects = []
                    time.sleep(0.5)
                    continue

                frame = self._cap.get_latest_small()
                if frame is None:
                    time.sleep(0.01)
                    continue

                objects = self._yolo.detect(frame, track=self._track_mode)
                _consecutive_errors = 0  # reset on success

                # ── Re-ID bridge (GatedReIDBridge) ────────────────────────
                # When track_mode is active, ByteTrack has assigned track_ids.
                # Pass objects through the bridge to add entity_id — a stable
                # cross-occlusion identity that lets episodic memory track the
                # *same* entity across different track_id assignments.
                # DINOv2 is only run for new/unconfirmed track_ids; confirmed
                # entities are dict lookups with zero extra compute.
                if self._track_mode and objects:
                    tracked = [o for o in objects if o.get('track_id') is not None]
                    if tracked:
                        try:
                            if self._reid_bridge is None:
                                from core.reid_bridge import GatedReIDBridge
                                print('[YOLO] loading GatedReIDBridge (DINOv2 lazy init)…')
                                self._reid_bridge = GatedReIDBridge()
                            reid_dets = [
                                {'track_id': o['track_id'], 'box': o['box'],
                                 'mask': o.get('mask')}
                                for o in tracked
                            ]
                            enriched = self._reid_bridge.process(frame, reid_dets)
                            # Merge entity_id back into objects by track_id
                            eid_map = {e['track_id']: e.get('entity_id') for e in enriched}
                            for o in objects:
                                tid = o.get('track_id')
                                if tid is not None:
                                    o['entity_id'] = eid_map.get(tid)
                        except Exception as _reid_err:
                            print(f'[YOLO] reid_bridge error: {_reid_err}')

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

            except Exception as e:
                _consecutive_errors += 1
                print(f'[YOLO] inference error (#{_consecutive_errors}): {e}')
                if _consecutive_errors >= 5:
                    print('[YOLO] too many consecutive errors — reloading model weights')
                    try:
                        self._yolo.reload_weights()
                        _consecutive_errors = 0
                    except Exception as reload_err:
                        print(f'[YOLO] reload failed: {reload_err}')
                        _consecutive_errors = 0
                time.sleep(0.5)

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
        self._stop_evt       = threading.Event()
        self._lock       = threading.Lock()
        self._last_frame = None
        self._last_objs  = []
        self.quit        = False   # set by poll_display() when user presses 'q'

    def stop(self):
        self._stop_evt.set()

    def run(self):
        # DisplayThread no longer calls cv2.imshow() — Qt requires imshow to
        # run on the main thread.  This thread now only drains the display queue
        # and caches the latest (frame, objects) pair for poll_display() to use.
        print('[DISPLAY] DisplayThread started (frame buffer only — imshow on main thread)')
        while not self._stop_evt.is_set():
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
        # Independent of self.capture on purpose: the capture card and the
        # desktop are concurrent inputs, not alternatives. The card carries
        # the game; this carries the machine AKSUMAEL runs on.
        self.desktop = (DesktopCaptureThread()
                        if getattr(config, 'DESKTOP_WATCH_ENABLED', True)
                        else None)
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
    def vision_available(self) -> bool:
        """False when no camera could be opened (or the active one vanished).

        The decision loop keeps ticking in that state — FSM, GoalStack and the
        inner monologue all run — but every heuristic that reads pixels or
        YOLO detections has to be skipped, because "black frame / zero
        detections" is indistinguishable from "night", "dead", "not in game"
        and "menu closed" to code that assumes vision works. CaptureThread
        re-probes in the background, so this can flip back to True mid-run."""
        return self.capture.vision_available

    @property
    def game_vision(self) -> bool:
        """True only when the frames flowing are of the game itself.

        `vision_available` above can be True on a desktop screen grab, which
        is genuinely useful to the LLM route but is not the world — see
        CaptureThread.game_vision. Minecraft heuristics gate on this."""
        return self.capture.game_vision

    @property
    def camera_index(self):
        """Index of the device actually in use, or None when the source is a
        screen grab or there is no source at all."""
        return self.capture.active_index

    @property
    def vision_source(self) -> str:
        """Human-readable name of the live source, for logs and the manifest:
        '/dev/video2', 'screenshot fallback (:0)', or 'NONE (vision-less)'."""
        return self.capture.source_label or 'NONE (vision-less)'

    @property
    def desktop_available(self) -> bool:
        """True when the always-on desktop watch is delivering frames."""
        return self.desktop is not None and self.desktop.available

    @property
    def latest_desktop_frame(self):
        """640-wide BGR grab of AKSUMAEL's own screen, or None.

        Available *alongside* the game feed, not instead of it. Intended for
        the LLM vision route; deliberately never fed to YOLO (see
        DesktopCaptureThread)."""
        return self.desktop.get_latest_small() if self.desktop else None

    @property
    def latest_desktop_raw(self):
        """Full-resolution desktop grab, or None."""
        return self.desktop.get_latest_raw() if self.desktop else None

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

    # Colour palette for YOLO detection boxes — one colour per class, cycling.
    _BOX_COLOURS = [
        (0, 255, 0), (255, 128, 0), (0, 128, 255), (255, 0, 255),
        (0, 255, 255), (255, 255, 0), (128, 0, 255), (0, 200, 128),
    ]

    def _draw_detections(self, frame, objects: list):
        """Draw YOLO detection boxes and labels onto frame.

        Each object dict has at minimum:
          bbox   — [x1, y1, x2, y2] in *small-frame* (640-wide) pixel space
          label  — class name string
          conf   — confidence float 0–1

        Boxes are colour-coded by class name (hash → palette index) so the
        same class is always the same colour. Suppressed when no objects or
        no bbox field (e.g. audio-sourced events have no spatial position)."""
        if not objects or frame is None:
            return
        fh, fw = frame.shape[:2]
        # The small frame is 640-wide; scale boxes if the display frame differs.
        sx = fw / 640.0
        sy = fh / 360.0
        for obj in objects:
            bbox = obj.get('bbox') or obj.get('box')
            if not bbox or len(bbox) < 4:
                continue
            label = obj.get('label', '?')
            conf  = obj.get('conf', obj.get('confidence', 0.0))
            colour = self._BOX_COLOURS[hash(label) % len(self._BOX_COLOURS)]
            x1, y1, x2, y2 = [int(v) for v in bbox[:4]]
            x1 = int(x1 * sx); y1 = int(y1 * sy)
            x2 = int(x2 * sx); y2 = int(y2 * sy)
            cv2.rectangle(frame, (x1, y1), (x2, y2), colour, 2)
            tag = f'{label} {conf:.2f}' if conf else label
            (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            ty = max(y1 - 4, th + 2)
            cv2.rectangle(frame, (x1, ty - th - 2), (x1 + tw + 4, ty + 2), colour, -1)
            cv2.putText(frame, tag, (x1 + 2, ty), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, (0, 0, 0), 1, cv2.LINE_AA)

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

    # ── Jarvis HUD renderer ───────────────────────────────────────────────
    _JARVIS_START = None   # set lazily on first render
    _JARVIS_FRAME = 0      # incremented every render call for animations

    def _jarvis_imshow(self, window_name: str, frame, objs):
        """Render a Jarvis-style HUD — camera feed as the main display,
        cognitive state / detections in a right sidebar, thinking pulse in header.

        Layout:
          ┌─────────── header: AKSUMAEL | TICK | MODE | UP | [THINK PULSE] ──┐
          │  CAMERA FEED (big, with YOLO boxes)    │  COGNITIVE STATE         │
          │                                        │  > thought lines ...     │
          │                                        ├──────────────────────────│
          │                                        │  DETECTIONS              │
          │                                        │  ore  91% ████           │
          ├────────────────────────────────────────┴──────────────────────────│
          │  VOICE ◉  [transcript]          OBJECTIVE: mine_diamonds           │
          └───────────────────────────────────────────────────────────────────┘
          ├──────────────────────────────────────┬──────────────────┤
          │  COGNITIVE STATE / INNER MONOLOGUE   │  ┌─ PiP cam ─┐  │
          │  (large scrolling thought stream)    │  │  camera   │  │
          │                                      │  └───────────┘  │
          │  > thought line 1                    │  DETECTIONS      │
          │  > thought line 2                    │  ore  91% ████  │
          │  > ...                               │  player 87% ██  │
          │                                      │                  │
          │  VOICE ◉  transcript text here       │  OBJECTIVE       │
          └──────────────────────────────────────┴──────────────────┘
        """
        if VideoCapturePipeline._JARVIS_START is None:
            VideoCapturePipeline._JARVIS_START = time.time()
        VideoCapturePipeline._JARVIS_FRAME += 1
        fnum = VideoCapturePipeline._JARVIS_FRAME

        # ── Colours (BGR) ──────────────────────────────────────────────
        BG     = (12,   8,   2)      # very dark navy
        CYAN   = (255, 212,  0)      # #00d4ff bright
        DCYAN  = (70,   50,  0)      # dim cyan
        PANEL  = (22,  14,  4)       # panel bg
        GREEN  = (80,  220, 60)      # ok / health
        WHITE  = (200, 200, 185)     # monologue body
        ORANGE = (20,  155, 255)     # voice active
        RED    = (50,   40, 220)     # warn
        FONT   = cv2.FONT_HERSHEY_SIMPLEX

        # ── Canvas dimensions ──────────────────────────────────────────
        WIN_W  = 1280
        WIN_H  = 720
        HDR_H  = 42
        FOOT_H = 40
        SIDE_W = 310
        CAM_W  = WIN_W - SIDE_W
        CAM_H  = WIN_H - HDR_H - FOOT_H

        canvas = np.zeros((WIN_H, WIN_W, 3), dtype=np.uint8)
        canvas[:] = BG

        # ── HUD state from frame_server ────────────────────────────────
        goal       = 'idle'
        mode       = 'live'
        tick       = 0
        voice_text = ''
        thinking   = False
        try:
            from core import frame_server as _fs
            with _fs._hud_lock:
                goal       = _fs._hud_state.get('goal', 'idle') or 'idle'
                mode       = _fs._hud_state.get('mode', 'live') or 'live'
                tick       = _fs._hud_state.get('tick', 0)
                voice_text = _fs._hud_state.get('voice_text', '')
                thinking   = bool(_fs._hud_state.get('thinking', False))
        except Exception:
            pass

        uptime_s   = int(time.time() - VideoCapturePipeline._JARVIS_START)
        uptime_str = (f'{uptime_s // 3600:02d}h '
                      f'{(uptime_s % 3600) // 60:02d}m '
                      f'{uptime_s % 60:02d}s')
        thoughts = _monologue_render_lines()

        # ── Header ─────────────────────────────────────────────────────
        cv2.rectangle(canvas, (0, 0), (WIN_W, HDR_H), PANEL, -1)
        cv2.line(canvas, (0, HDR_H), (WIN_W, HDR_H), CYAN, 1)
        # logo
        cv2.putText(canvas, 'A K S U M A E L', (12, 28),
                    FONT, 0.7, CYAN, 2, cv2.LINE_AA)
        # tick / mode / uptime
        cv2.putText(canvas,
                    f'TICK {tick:07d}   MODE {mode.upper():<8s}   UP {uptime_str}',
                    (270, 28), FONT, 0.40, DCYAN, 1, cv2.LINE_AA)

        # ── Thinking pulse (top-right of header) ──────────────────────
        # Spinning arc when active; dim steady ring when idle
        pulse_cx = WIN_W - 30
        pulse_cy = HDR_H // 2 + 1
        pulse_r  = 14
        if thinking:
            # Bright spinning arc (360° / 60 frames per rev ≈ 6°/frame)
            angle = int(fnum * 6) % 360
            arc_color = CYAN
            cv2.ellipse(canvas,
                        (pulse_cx, pulse_cy), (pulse_r, pulse_r),
                        0, angle, angle + 240, arc_color, 2, cv2.LINE_AA)
            # Label
            cv2.putText(canvas, 'PROCESSING', (WIN_W - 140, 28),
                        FONT, 0.32, CYAN, 1, cv2.LINE_AA)
        else:
            # Dim idle ring with slow pulse (brightness oscillates ~1Hz)
            phase = (fnum % 30) / 30.0          # 0..1 over 30 frames
            bright = int(40 + 20 * abs(phase - 0.5) * 2)
            idle_col = (bright, bright // 2, 0)
            cv2.circle(canvas, (pulse_cx, pulse_cy), pulse_r, idle_col, 1,
                       cv2.LINE_AA)
            cv2.putText(canvas, 'STANDBY', (WIN_W - 110, 28),
                        FONT, 0.32, DCYAN, 1, cv2.LINE_AA)

        # ── Camera feed (main, left+center) ───────────────────────────
        cam_x0, cam_y0 = 0, HDR_H
        if frame is not None:
            cam_frame = cv2.resize(frame, (CAM_W, CAM_H))
            canvas[cam_y0:cam_y0 + CAM_H, cam_x0:cam_x0 + CAM_W] = cam_frame
        else:
            # No signal — dark panel with text
            cv2.rectangle(canvas, (cam_x0, cam_y0),
                          (cam_x0 + CAM_W, cam_y0 + CAM_H), (6, 4, 2), -1)
            cv2.putText(canvas, 'NO CAMERA SIGNAL',
                        (CAM_W // 2 - 110, cam_y0 + CAM_H // 2),
                        FONT, 0.8, DCYAN, 2, cv2.LINE_AA)
        # Corner brackets on camera
        blen = 18
        for bx, by, dx, dy in [
            (cam_x0, cam_y0, 1, 1),
            (cam_x0 + CAM_W - blen, cam_y0, -1, 1),
            (cam_x0, cam_y0 + CAM_H - blen, 1, -1),
            (cam_x0 + CAM_W - blen, cam_y0 + CAM_H - blen, -1, -1),
        ]:
            cv2.line(canvas, (bx, by), (bx + dx * blen, by), CYAN, 2)
            cv2.line(canvas, (bx, by), (bx, by + dy * blen), CYAN, 2)
        # Camera label
        cv2.putText(canvas, 'LIVE · /dev/video2',
                    (cam_x0 + 6, cam_y0 + 16),
                    FONT, 0.35, (CYAN[0] // 2, CYAN[1] // 2, CYAN[2] // 2),
                    1, cv2.LINE_AA)

        # ── Right sidebar ──────────────────────────────────────────────
        sx = CAM_W  # sidebar starts here
        cv2.rectangle(canvas, (sx, HDR_H), (WIN_W, WIN_H - FOOT_H), PANEL, -1)
        cv2.line(canvas, (sx, HDR_H), (sx, WIN_H), CYAN, 1)
        px = sx + 8
        sy = HDR_H + 14

        # --- COGNITIVE STATE ---
        cv2.putText(canvas, 'COGNITIVE STATE', (px, sy),
                    FONT, 0.35, DCYAN, 1, cv2.LINE_AA)
        sy += 6
        cv2.line(canvas, (px, sy), (WIN_W - 4, sy), DCYAN, 1)
        sy += 14

        LINE_H   = 17
        SIDE_TXT = SIDE_W - 16
        avail_h  = (WIN_H - FOOT_H) - sy - 110   # leave room for detections
        max_lines = max(1, avail_h // LINE_H)
        recent_thoughts = thoughts[-max_lines:] if len(thoughts) > max_lines else thoughts
        for i, line in enumerate(recent_thoughts):
            is_latest = (i == len(recent_thoughts) - 1)
            col = CYAN if is_latest else (WHITE if i >= len(recent_thoughts) - 4 else DCYAN)
            prefix = '> ' if is_latest else '  '
            chars = (SIDE_TXT * 2) // 7   # approx chars that fit at scale 0.36
            disp  = (line[:chars] + '..') if len(line) > chars else line
            cv2.putText(canvas, f'{prefix}{disp}',
                        (px, sy + i * LINE_H),
                        FONT, 0.34, col, 1, cv2.LINE_AA)
        sy += max_lines * LINE_H + 8

        # --- DETECTIONS ---
        cv2.line(canvas, (px, sy), (WIN_W - 4, sy), DCYAN, 1)
        sy += 12
        cv2.putText(canvas, 'DETECTIONS', (px, sy),
                    FONT, 0.35, DCYAN, 1, cv2.LINE_AA)
        sy += 14
        bar_max = SIDE_W - 20
        for obj in (objs or [])[:7]:
            if sy >= WIN_H - FOOT_H - 8:
                break
            label  = str(obj.get('label', obj.get('class_name', '?')))[:14]
            conf   = float(obj.get('confidence', obj.get('conf', 0.0)))
            bar_w  = int(bar_max * conf)
            cv2.rectangle(canvas, (px, sy - 8), (px + bar_w, sy - 2),
                          (0, 40, 40), -1)
            cv2.putText(canvas, f'{label:<14s}{conf:3.0%}',
                        (px, sy - 1), FONT, 0.33, GREEN, 1, cv2.LINE_AA)
            sy += 13
        if not objs:
            cv2.putText(canvas, 'no detections', (px, sy),
                        FONT, 0.33, DCYAN, 1, cv2.LINE_AA)

        # ── Footer strip (full width) ──────────────────────────────────
        fy = WIN_H - FOOT_H
        cv2.rectangle(canvas, (0, fy), (WIN_W, WIN_H), PANEL, -1)
        cv2.line(canvas, (0, fy), (WIN_W, fy), CYAN, 1)
        fmy = fy + 14

        # Voice indicator
        if voice_text:
            cv2.circle(canvas, (18, fmy - 3), 6, ORANGE, -1)
            vt = (voice_text[:100] + '...') if len(voice_text) > 100 else voice_text
            cv2.putText(canvas, vt, (32, fmy), FONT, 0.42, ORANGE, 1, cv2.LINE_AA)
        else:
            cv2.circle(canvas, (18, fmy - 3), 6, DCYAN, 1)
            cv2.putText(canvas, 'voice ready  (F9 = PTT)',
                        (32, fmy), FONT, 0.38, DCYAN, 1, cv2.LINE_AA)

        # Objective (right-aligned in footer)
        obj_str = f'OBJECTIVE: {goal[:50]}'
        (tw, _), _ = cv2.getTextSize(obj_str, FONT, 0.40, 1)
        cv2.putText(canvas, obj_str, (WIN_W - tw - 10, fmy),
                    FONT, 0.40, GREEN, 1, cv2.LINE_AA)

        cv2.imshow(window_name, canvas)

    # ── Display pump (main-thread only) ──────────────────────────────────

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
        self._draw_detections(frame, objs)   # YOLO boxes before text overlay
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
            # Jarvis/Ultron HUD window (replaces old plain imshow).
            self._jarvis_imshow(window_name, frame, objs)
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
        """Start the capture/YOLO/display threads, plus the desktop watch
        when enabled (non-blocking)."""
        self.capture.start()
        self.yolo_t.start()
        self.display.start()
        if self.desktop is not None:
            self.desktop.start()

    def stop(self):
        """Signal every thread to exit."""
        self.display.stop()
        self.yolo_t.stop()
        self.capture.stop()
        if self.desktop is not None:
            self.desktop.stop()

    def release(self):
        """Alias for ScreenCapture.release() compatibility."""
        self.stop()
