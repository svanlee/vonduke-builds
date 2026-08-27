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

import os
import threading
import queue
import time
import textwrap
import cv2
import numpy as np
import config

# ── Display backend probe ──────────────────────────────────────────────────────
# cv2.imshow calls C++ terminate() (not a catchable Python exception) when built
# without a GUI backend. Check getBuildInformation() at import time — safe,
# requires no display. If GTK/Qt is absent, disable all imshow calls for the
# session rather than crashing on the first tick.
try:
    _build = cv2.getBuildInformation()
    _CV2_GUI_OK = any(
        pat in _build for pat in ('GTK', 'QT', 'Qt', 'Carbon', 'Cocoa', 'WIN32UI')
    )
    if not _CV2_GUI_OK:
        print('[DISPLAY] cv2 built without GUI backend (GTK/Qt absent) — '
              'imshow disabled for this session')
except Exception:
    _CV2_GUI_OK = False  # assume broken if we can't even check


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
    _NN_NODES = None       # neural network node positions (built once)
    _NN_EDGES = None       # neural network edge pairs
    _NN_PULSES = []        # active pulses traversing edges
    _NN_HEAT   = {}        # per-node heat value 0.0-1.0
    _NN_ADJ    = None      # adjacency list built once from edges
    # ── Expandable panel system ──────────────────────────────────────────
    # Each panel: scale=current lerp (0=mini,1=full), target=animation target,
    # exp_scale=how much of screen when expanded (0.3-0.96, scroll to adjust),
    # mini_rect set each frame during draw.
    _PANELS = {
        'camera':  {'scale': 0.0, 'target': 0.0, 'exp_scale': 0.62, 'mini_rect': (0,0,1,1)},
        'sysstat': {'scale': 0.0, 'target': 0.0, 'exp_scale': 0.72, 'mini_rect': (0,0,1,1)},
        'goals':   {'scale': 0.0, 'target': 0.0, 'exp_scale': 0.68, 'mini_rect': (0,0,1,1)},
        'thought': {'scale': 0.0, 'target': 0.0, 'exp_scale': 0.82, 'mini_rect': (0,0,1,1)},
    }
    _MOUSE_CB_SET = False  # mouse callback registered on window?
    _FULLSCREEN   = False  # toggle with 'f' key
    # Sysstat cached — refresh every 2 s to avoid per-frame nvidia-smi calls
    _SYSSTAT = {'cpu': 0.0, 'gpu': 0.0, 'gpu_mem': 0.0, 'ram': 0.0}
    _SYSSTAT_NEXT = 0.0
    # Text input / conversation log
    _TEXT_INPUT: str = ''
    _TEXT_ACTIVE: bool = False   # True while user is typing
    _CONVO_LOG: list = []        # [(speaker, text), ...]  last N exchanges
    _WEBCAM = None               # cv2.VideoCapture for laptop webcam (porthole)
    _WEBCAM_FRAME = None         # latest frame from webcam
    _WEBCAM_NEXT = 0.0           # next refresh timestamp

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

        import math as _math

        # ── Colours (BGR) ──────────────────────────────────────────────
        BG     = (10,   6,   2)       # near-black navy
        CYAN   = (255, 212,  0)       # #00d4ff — BGR for cyan
        CYAN2  = (180, 150,  0)       # mid cyan
        DCYAN  = (55,   40,  0)       # dim cyan
        VCYAN  = (25,   18,  0)       # very dim
        PANEL  = (18,  12,  3)        # sidebar bg
        GREEN  = (60,  230, 40)       # health green
        DGREEN = (15,   60, 10)       # dim green (bar bg)
        ORANGE = (10,  140, 255)      # hunger / voice
        DORANGE= (5,   40,  80)       # dim orange
        WHITE  = (190, 195, 185)      # text
        RED    = (40,   30, 220)      # warn
        FONT   = cv2.FONT_HERSHEY_SIMPLEX

        # ── Glow helpers ───────────────────────────────────────────────
        def _gline(img, p1, p2, col, dim, thick=1):
            """Line with glow halo (dim wide stroke + bright thin stroke)."""
            cv2.line(img, p1, p2, dim, thick + 4, cv2.LINE_AA)
            cv2.line(img, p1, p2, col, thick,     cv2.LINE_AA)

        def _gcircle(img, ctr, r, col, dim, thick=1):
            cv2.circle(img, ctr, r + 3, dim, thick + 4, cv2.LINE_AA)
            cv2.circle(img, ctr, r,     col, thick,     cv2.LINE_AA)

        def _garc(img, ctr, r, start_deg, end_deg, col, dim, thick=2):
            cv2.ellipse(img, ctr, (r, r), 0, start_deg, end_deg, dim, thick + 4, cv2.LINE_AA)
            cv2.ellipse(img, ctr, (r, r), 0, start_deg, end_deg, col, thick,     cv2.LINE_AA)

        def _gtext(img, txt, pos, scale, col, dim, thick=1):
            cv2.putText(img, txt, pos, FONT, scale, dim, thick + 2, cv2.LINE_AA)
            cv2.putText(img, txt, pos, FONT, scale, col, thick,     cv2.LINE_AA)

        def _corner_bracket(img, x, y, dx, dy, size, col, dim):
            """Draw L-shaped corner bracket with glow. dx/dy = ±1 direction."""
            ex, ey = x + dx * size, y + dy * size
            _gline(img, (x, y), (ex, y), col, dim, 2)
            _gline(img, (x, y), (x, ey), col, dim, 2)
            # small tick at tip
            cv2.line(img, (ex - dx * 4, y - 1), (ex - dx * 4, y + 1), col, 1)

        def _arc_gauge(img, cx, cy, r, pct, col_full, col_dim, col_bg,
                       start_deg=135, sweep=270):
            """270° arc gauge: start_deg → start_deg+sweep filled by pct."""
            # Background arc
            cv2.ellipse(img, (cx, cy), (r, r), 0, start_deg,
                        start_deg + sweep, col_bg, 4, cv2.LINE_AA)
            # Filled portion
            fill_end = start_deg + sweep * max(0.0, min(1.0, pct))
            if fill_end > start_deg + 1:
                _garc(img, (cx, cy), r, start_deg, fill_end, col_full, col_dim, 3)
            # Tick marks around ring
            for i in range(11):
                a_deg = start_deg + sweep * i / 10
                a_rad = _math.radians(a_deg)
                r1 = r + 5; r2 = r + (9 if i % 5 == 0 else 7)
                px1 = int(cx + r1 * _math.cos(a_rad))
                py1 = int(cy + r1 * _math.sin(a_rad))
                px2 = int(cx + r2 * _math.cos(a_rad))
                py2 = int(cy + r2 * _math.sin(a_rad))
                tcol = col_full if i % 5 == 0 else col_dim
                cv2.line(img, (px1, py1), (px2, py2), tcol, 1, cv2.LINE_AA)

        def _radar_sweep(img, cx, cy, r, angle_deg, col, dim):
            """Radar sweep wedge + leading line."""
            a1 = _math.radians(angle_deg)
            a2 = _math.radians(angle_deg - 40)
            # Dim wedge fill
            pts = []
            pts.append([cx, cy])
            for a in range(int(angle_deg) - 40, int(angle_deg) + 1, 2):
                ar = _math.radians(a)
                pts.append([int(cx + r * _math.cos(ar)),
                             int(cy + r * _math.sin(ar))])
            if len(pts) > 2:
                import numpy as _np2
                pts_arr = _np2.array(pts, dtype=_np2.int32)
                overlay = img.copy()
                cv2.fillPoly(overlay, [pts_arr], dim)
                cv2.addWeighted(overlay, 0.35, img, 0.65, 0, img)
            # Leading sweep line
            lx = int(cx + r * _math.cos(a1))
            ly = int(cy + r * _math.sin(a1))
            _gline(img, (cx, cy), (lx, ly), col, (col[0]//3, col[1]//3, col[2]//3), 1)
            # Outer ring
            cv2.ellipse(img, (cx, cy), (r, r), 0, 0, 360, dim, 1, cv2.LINE_AA)
            # Cross hairs
            cv2.line(img, (cx - r, cy), (cx + r, cy), (col[0]//8, col[1]//8, col[2]//8), 1)
            cv2.line(img, (cx, cy - r), (cx, cy + r), (col[0]//8, col[1]//8, col[2]//8), 1)

        # ── Canvas dimensions ──────────────────────────────────────────
        WIN_W  = 1280
        WIN_H  = 720
        HDR_H  = 48
        FOOT_H = 36
        SIDE_W = 300
        CAM_W  = WIN_W - SIDE_W
        CAM_H  = WIN_H - HDR_H - FOOT_H

        canvas = np.zeros((WIN_H, WIN_W, 3), dtype=np.uint8)
        canvas[:] = BG

        # ── HUD state ─────────────────────────────────────────────────
        goal       = 'idle'
        mode       = 'live'
        tick       = 0
        voice_text = ''
        thinking   = False
        hp_pct     = 1.0
        food_pct   = 1.0
        try:
            from core import frame_server as _fs
            with _fs._hud_lock:
                goal       = _fs._hud_state.get('goal', 'idle') or 'idle'
                mode       = _fs._hud_state.get('mode', 'live') or 'live'
                tick       = _fs._hud_state.get('tick', 0)
                voice_text = _fs._hud_state.get('voice_text', '')
                thinking   = bool(_fs._hud_state.get('thinking', False))
                hp_pct     = float(_fs._hud_state.get('health_pct', 100)) / 100.0
                food_pct   = float(_fs._hud_state.get('hunger_pct', 100)) / 100.0
        except Exception:
            pass

        uptime_s   = int(time.time() - VideoCapturePipeline._JARVIS_START)
        uptime_str = f'{uptime_s // 3600:02d}:{(uptime_s % 3600) // 60:02d}:{uptime_s % 60:02d}'

        # ── Sysstat cache refresh (used by both mini panels and sidebar) ──
        _now_ss = time.time()
        if _now_ss >= VideoCapturePipeline._SYSSTAT_NEXT:
            VideoCapturePipeline._SYSSTAT_NEXT = _now_ss + 2.0
            try:
                import psutil as _ps
                VideoCapturePipeline._SYSSTAT['cpu'] = _ps.cpu_percent(interval=None) / 100.0
                VideoCapturePipeline._SYSSTAT['ram'] = _ps.virtual_memory().percent / 100.0
            except Exception:
                pass
            try:
                import subprocess as _sp
                _smi = _sp.run(
                    ['nvidia-smi','--query-gpu=utilization.gpu,memory.used,memory.total',
                     '--format=csv,noheader,nounits'],
                    capture_output=True, text=True, timeout=1)
                _vals = _smi.stdout.strip().split(',')
                VideoCapturePipeline._SYSSTAT['gpu']     = float(_vals[0].strip()) / 100.0
                VideoCapturePipeline._SYSSTAT['gpu_mem'] = float(_vals[1].strip()) / float(_vals[2].strip())
            except Exception:
                pass
        cpu_pct  = VideoCapturePipeline._SYSSTAT['cpu']
        gpu_util = VideoCapturePipeline._SYSSTAT['gpu']
        gpu_mem  = VideoCapturePipeline._SYSSTAT['gpu_mem']
        ram_pct  = VideoCapturePipeline._SYSSTAT['ram']
        thoughts   = _monologue_render_lines()

        # ── Webcam porthole refresh (laptop built-in, 8 fps) ─────────
        _wc_now = time.time()
        if _wc_now >= VideoCapturePipeline._WEBCAM_NEXT:
            VideoCapturePipeline._WEBCAM_NEXT = _wc_now + 0.125  # ~8 fps
            try:
                if VideoCapturePipeline._WEBCAM is None:
                    _wc = cv2.VideoCapture(0)   # /dev/video0 — built-in webcam
                    if not _wc.isOpened():
                        _wc = cv2.VideoCapture(1)  # fallback to video1
                    VideoCapturePipeline._WEBCAM = _wc if _wc.isOpened() else False
                if VideoCapturePipeline._WEBCAM:
                    _ok, _wf = VideoCapturePipeline._WEBCAM.read()
                    if _ok:
                        VideoCapturePipeline._WEBCAM_FRAME = _wf
            except Exception:
                pass

        # ══════════════════════════════════════════════════════════════
        # HEADER
        # ══════════════════════════════════════════════════════════════
        # Header — no fill, elements float on black
        # Thin arc sweep instead of a full-width solid bar
        cv2.ellipse(canvas, (WIN_W // 2, 0), (WIN_W // 2, HDR_H + 6),
                    0, 0, 180, (DCYAN[0]//2, DCYAN[1]//2, DCYAN[2]//2), 1, cv2.LINE_AA)

        # Logo with glow
        _gtext(canvas, 'A K S U M A E L', (12, 32), 0.72, CYAN, DCYAN, 2)

        # Info
        info = f'TICK {tick:07d}   {mode.upper():<8s}   UP {uptime_str}'
        cv2.putText(canvas, info, (280, 32), FONT, 0.38, CYAN2, 1, cv2.LINE_AA)

        # ── Thinking ring cluster (right of header) ────────────────────
        rc_x = WIN_W - 55
        rc_y = HDR_H // 2 + 2

        if thinking:
            ang = (fnum * 5) % 360
            ang2 = (fnum * 8 + 120) % 360
            # Outer spinning arc
            _garc(canvas, (rc_x, rc_y), 20, ang, ang + 200, CYAN, DCYAN, 2)
            # Inner counter-spinning arc
            _garc(canvas, (rc_x, rc_y), 13, -ang2, -ang2 + 130, CYAN2,
                  (DCYAN[0]//2, DCYAN[1]//2, DCYAN[2]//2), 1)
            # Core dot
            _gcircle(canvas, (rc_x, rc_y), 4, CYAN, DCYAN, -1)
            _gtext(canvas, 'PROCESSING', (WIN_W - 180, 32), 0.32, CYAN, DCYAN)
        else:
            # Idle: dim pulsing ring
            phase = abs(_math.sin(fnum * 0.04))
            bc = int(30 + 25 * phase)
            ic = (bc, bc // 2, 0)
            cv2.circle(canvas, (rc_x, rc_y), 20, ic, 1, cv2.LINE_AA)
            cv2.circle(canvas, (rc_x, rc_y), 13, ic, 1, cv2.LINE_AA)
            cv2.circle(canvas, (rc_x, rc_y), 4,
                       (bc // 2, bc // 2, 0), -1, cv2.LINE_AA)
            cv2.putText(canvas, 'STANDBY', (WIN_W - 140, 32),
                        FONT, 0.30, DCYAN, 1, cv2.LINE_AA)

        # Circuit trace deco: short dashes after logo
        for xi in range(220, 260, 8):
            cv2.rectangle(canvas, (xi, 18), (xi + 4, 20), VCYAN, -1)

        # ══════════════════════════════════════════════════════════════
        # NEURAL MIND — 3D sphere + arc reactor (depth-sorted)
        # ══════════════════════════════════════════════════════════════
        nn_x0, nn_y0 = 0, HDR_H
        nn_w, nn_h   = CAM_W, CAM_H
        ncx = nn_x0 + nn_w // 2
        ncy = nn_y0 + nn_h // 2

        # Dark background
        cv2.rectangle(canvas, (nn_x0, nn_y0), (nn_x0+nn_w, nn_y0+nn_h), (2, 6, 10), -1)

        # Dot-grid background
        for gy2 in range(nn_y0+14, nn_y0+nn_h, 28):
            for gx2 in range(nn_x0+14, nn_x0+nn_w, 28):
                cv2.circle(canvas, (gx2, gy2), 1, (0, 20, 30), -1)

        # ── Build 3D fibonacci-sphere node graph once (180 nodes) ────────
        _NN_TARGET = 180
        if VideoCapturePipeline._NN_NODES is None or len(VideoCapturePipeline._NN_NODES) != _NN_TARGET:
            VideoCapturePipeline._NN_ADJ = None  # force adjacency rebuild
            _phi3d = _math.pi * (3. - _math.sqrt(5.))
            import random as _rng_init
            _rng = _rng_init.Random(0xACE)   # fixed seed → stable layout, asymmetric
            nn_nodes3 = []
            for i in range(_NN_TARGET):
                _y3d = 1. - (i / (_NN_TARGET-1.)) * 2.
                _r3d = _math.sqrt(max(0., 1. - _y3d*_y3d))
                _th3d = _phi3d * i
                # Small position jitter breaks the perfect Fibonacci lattice
                _jx = _rng.uniform(-0.045, 0.045)
                _jy = _rng.uniform(-0.035, 0.035)
                _jz = _rng.uniform(-0.045, 0.045)
                nn_nodes3.append({
                    'x3': _r3d * _math.cos(_th3d) + _jx,
                    'y3': _y3d + _jy,
                    'z3': _r3d * _math.sin(_th3d) + _jz,
                    # Random phase — not linear, so no two nodes breathe in sync
                    'phase': _rng.uniform(0., _math.pi * 2.),
                    'spd':   _rng.uniform(0.15, 0.95),
                    'rb':    2 + (i % 3),
                    # Per-node unique oscillation frequencies and amplitudes
                    'df': (_rng.uniform(0.7, 2.1), _rng.uniform(0.3, 1.4)),
                    'da': (_rng.uniform(0.03, 0.10), _rng.uniform(0.02, 0.07)),
                })
            VideoCapturePipeline._NN_NODES = nn_nodes3
            nn_edges3 = []
            for i in range(len(nn_nodes3)):
                for j in range(i+1, len(nn_nodes3)):
                    _a3, _b3 = nn_nodes3[i], nn_nodes3[j]
                    _dx3=_a3['x3']-_b3['x3']; _dy3=_a3['y3']-_b3['y3']; _dz3=_a3['z3']-_b3['z3']
                    _d3 = _math.sqrt(_dx3*_dx3+_dy3*_dy3+_dz3*_dz3)
                    if _d3 < 0.48:
                        nn_edges3.append({'a':i,'b':j,'d':_d3,'tier':'short'})
                    elif _d3 < 0.85:
                        nn_edges3.append({'a':i,'b':j,'d':_d3,'tier':'long'})
            VideoCapturePipeline._NN_EDGES = nn_edges3

        nn_nodes = VideoCapturePipeline._NN_NODES
        nn_edges = VideoCapturePipeline._NN_EDGES

        # Gold palette — BGR: all warm amber/orange/gold regardless of goal
        # (goal tints the heat highlight color only)
        _GC_TINT = {
            # Jarvis states
            'standby':   ( 0, 185, 255),  # amber gold
            'listen':    (20, 220, 200),  # green-gold (active listening)
            'think':     ( 0, 155, 255),  # deep orange (processing)
            'assist':    ( 0, 210, 255),  # bright gold (responding)
            'alert':     ( 0,  80, 255),  # red-orange (urgent)
            'idle':      ( 0, 185, 255),  # amber gold
            # Legacy Minecraft labels kept for backward compatibility during transition
            'explore':        ( 0, 210, 255),
            'mine_diamonds':  ( 0, 155, 255),
            'find_food':      (20, 220, 200),
            'return_to_base': ( 0, 210, 255),
            'craft':          ( 0, 185, 255),
            'combat':         ( 0, 110, 255),
        }
        gc  = _GC_TINT.get(goal, (0, 185, 255))  # default amber gold (BGR)
        gcd = (gc[0]//6, gc[1]//6, gc[2]//6)
        # Secondary fire color for hottest nodes
        gc_hot = (80, 240, 255)  # near-white gold

        # ── 3D → 2D perspective projection ───────────────────────────
        _rot_y = (fnum * 0.006) % (_math.pi*2)
        _rot_xa = _math.sin(fnum * 0.003) * 0.25
        _cy3, _sy3 = _math.cos(_rot_y), _math.sin(_rot_y)
        _cx3, _sx3 = _math.cos(_rot_xa), _math.sin(_rot_xa)
        _sscale = min(nn_w, nn_h) * 0.36
        _fov    = 3.5

        def _proj3(x3, y3, z3):
            xr = x3*_cy3 + z3*_sy3
            yr = y3
            zr = -x3*_sy3 + z3*_cy3
            yr2 = yr*_cx3 - zr*_sx3
            zr2 = yr*_sx3 + zr*_cx3
            s = _sscale * _fov / (_fov + zr2)
            return int(ncx + xr*s), int(ncy + yr2*s), zr2

        # ── Holosphere state (gold, armillary, dense) ─────────────────
        import numpy as _np, random as _rand
        heat = VideoCapturePipeline._NN_HEAT

        # Organic deformation: each node breathes outward/inward along its own
        # normal vector. Heat pulls nodes inward — hot nodes cluster near the
        # core (inner shell r≈0.45), cold nodes sit on the outer shell (r≈1.0).
        # Three natural layers emerge: core (active), mid (warming), outer (idle).
        _t_slow = fnum * 0.008
        def _deformed(n, idx):
            _phase_n = n['phase']           # fully random per node, not idx-linear
            _fa, _fb = n['df']              # unique frequencies per node
            _aa, _ab = n['da']              # unique amplitudes per node
            _bulge = _aa * _math.sin(_t_slow * _fa + _phase_n) \
                   + _ab * _math.cos(_t_slow * _fb + _phase_n * 1.3)
            _h = heat.get(idx, 0.0)
            _layer_r = 1.0 - _h * 0.55
            _r = _layer_r + _bulge
            px, py, pz = _proj3(n['x3'] * _r, n['y3'] * _r, n['z3'] * _r)
            # Brain-ellipse: wider than tall, slowly drifting asymmetry
            _drift_x = _math.sin(fnum * 0.0009) * 9
            _drift_y = _math.cos(fnum * 0.0007) * 6
            px2 = int(ncx + (px - ncx) * 1.42 + _drift_x)
            py2 = int(ncy + (py - ncy) * 0.68 + _drift_y)
            return px2, py2, pz

        pnodes = [_deformed(n, i) for i, n in enumerate(nn_nodes)]

        if VideoCapturePipeline._NN_ADJ is None:
            adj = {i: [] for i in range(len(nn_nodes))}
            for _e in nn_edges:
                adj[_e['a']].append(_e['b'])
                adj[_e['b']].append(_e['a'])
            VideoCapturePipeline._NN_ADJ = adj
        adj = VideoCapturePipeline._NN_ADJ

        # When speaking: fire nodes much more aggressively (living, reactive look)
        try:
            import core.voice as _vm2
            _spk = getattr(_vm2, 'JARVIS_SPEAKING', False)
        except Exception:
            _spk = False
        _fire_rate = 3 if _spk else 15
        if fnum % _fire_rate == 0:
            heat[_rand.randint(0, len(nn_nodes)-1)] = 1.0
            if _spk:  # fire multiple nodes when speaking
                heat[_rand.randint(0, len(nn_nodes)-1)] = 0.8
                heat[_rand.randint(0, len(nn_nodes)-1)] = 0.6

        _new_heat = {}
        for i in range(len(nn_nodes)):
            h = heat.get(i, 0.0)
            if h > 0.72 and _rand.random() < 0.10:
                for nb in adj.get(i, []):
                    if heat.get(nb, 0.0) < 0.2:
                        _new_heat[nb] = min(1.0, heat.get(nb, 0.0) + 0.65)
            _new_heat[i] = max(0.0, min(1.0, _new_heat.get(i, h) - 0.015))
        VideoCapturePipeline._NN_HEAT = _new_heat
        heat = _new_heat

        breath = 0.88 + 0.12 * _math.sin(fnum * 0.020)

        def _hcol_gold(h):
            if h < 0.5:
                f = h * 2.0
                return (int(gc[0]*(0.08+0.92*f)), int(gc[1]*(0.08+0.92*f)), int(gc[2]*(0.08+0.92*f)))
            else:
                f = (h-0.5)*2.0
                return (min(255,int(gc[0]+(gc_hot[0]-gc[0])*f)),
                        min(255,int(gc[1]+(gc_hot[1]-gc[1])*f)),
                        min(255,int(gc[2]+(gc_hot[2]-gc[2])*f)))

        _glow = _np.zeros((nn_h, nn_w, 3), dtype=_np.float32)
        _lcx, _lcy = ncx - nn_x0, ncy - nn_y0

        # Ambient halo (very subtle — just a faint sphere edge, no fill)
        cv2.circle(_glow, (_lcx,_lcy), int(255*breath),
                   (gc[0]/255.*0.03, gc[1]/255.*0.03, gc[2]/255.*0.03), 3)

        # Separate short/long edge tiers
        _short_edges = [e for e in nn_edges if e.get('tier','short')=='short']
        _long_edges  = [e for e in nn_edges if e.get('tier','long')=='long']

        # Long-range connections — ghost threads, barely there
        for e2 in _long_edges:
            ax2,ay2,az2=pnodes[e2['a']]; bx2,by2,bz2=pnodes[e2['b']]
            if not(nn_x0<=ax2<nn_x0+nn_w and nn_y0<=ay2<nn_y0+nn_h): continue
            if not(nn_x0<=bx2<nn_x0+nn_w and nn_y0<=by2<nn_y0+nn_h): continue
            eh=max(heat.get(e2['a'],0.),heat.get(e2['b'],0.))
            if eh < 0.1: continue  # skip cold long edges entirely
            dim=int(max(8, eh*50))
            cv2.line(canvas,(ax2,ay2),(bx2,by2),(0,dim//3,dim),1,cv2.LINE_AA)

        # Short connections — thin 1px threads, only front-visible ones
        for e2 in sorted(_short_edges, key=lambda e:(pnodes[e['a']][2]+pnodes[e['b']][2])/2):
            ax2,ay2,az2=pnodes[e2['a']]; bx2,by2,bz2=pnodes[e2['b']]
            if not(nn_x0<=ax2<nn_x0+nn_w and nn_y0<=ay2<nn_y0+nn_h): continue
            if not(nn_x0<=bx2<nn_x0+nn_w and nn_y0<=by2<nn_y0+nn_h): continue
            df=max(0.,0.5+(az2+bz2)*0.25)
            eh=max(heat.get(e2['a'],0.),heat.get(e2['b'],0.))
            # base: 25% brightness, boost only on heat
            raw=_hcol_gold(max(eh,df*0.25))
            scale = 0.28 + eh*0.45   # 28% cold, up to 73% when hot
            ec=(int(raw[0]*scale),int(raw[1]*scale),int(raw[2]*scale))
            cv2.line(canvas,(ax2,ay2),(bx2,by2),ec,1,cv2.LINE_AA)  # always 1px
            lx1,ly1=ax2-nn_x0,ay2-nn_y0
            lx2v,ly2v=bx2-nn_x0,by2-nn_y0
            egf=df*0.08+eh*0.22
            if egf>0.05:
                cv2.line(_glow,(lx1,ly1),(lx2v,ly2v),
                         (gc[0]/255.*egf,gc[1]/255.*egf,gc[2]/255.*egf),1,cv2.LINE_AA)

        # Armillary rings — DOMINANT visual element, brightest thing on screen
        _arms = [
            (0.0,    0.010,  1.0,  2),   # equatorial  — brightest
            (0.524, -0.007,  0.85, 2),   # 30° tilt
            (1.047,  0.005,  0.70, 1),   # 60° tilt
            (1.396, -0.004,  0.60, 1),   # 80° tilt
            (0.262,  0.009,  0.65, 1),   # 15° tilt
        ]
        for (inc_b, spin_r, rbr, rth) in _arms:
            inc = inc_b + fnum * spin_r
            _ci, _si = _math.cos(inc), _math.sin(inc)
            prev_px, prev_py, prev_pz = None, None, None
            for _tdeg in range(0, 362, 2):  # 2° steps = smooth
                _t = _math.radians(_tdeg)
                _rx = _math.cos(_t)
                _ry = _math.sin(_t)*_ci
                _rz = _math.sin(_t)*_si
                _px,_py,_pz = _proj3(_rx,_ry,_rz)
                if nn_x0<=_px<nn_x0+nn_w and nn_y0<=_py<nn_y0+nn_h:
                    if prev_px is not None and _pz > -0.2:
                        vf = max(0., 0.4+_pz*0.6)
                        rc_b=min(255,int(gc[0]*rbr*vf))
                        rc_g=min(255,int(gc[1]*rbr*vf))
                        rc_r=min(255,int(gc[2]*rbr*vf))
                        cv2.line(canvas,(prev_px,prev_py),(_px,_py),
                                 (rc_b,rc_g,rc_r),rth,cv2.LINE_AA)
                        lrx1,lry1=prev_px-nn_x0,prev_py-nn_y0
                        lrx2,lry2=_px-nn_x0,_py-nn_y0
                        gfr=rbr*0.65*max(0.,vf)  # strong glow on rings
                        cv2.line(_glow,(lrx1,lry1),(lrx2,lry2),
                                 (gc[0]/255.*gfr,gc[1]/255.*gfr,gc[2]/255.*gfr),3,cv2.LINE_AA)
                    prev_px,prev_py,prev_pz=_px,_py,_pz
                else:
                    prev_px=None

        # Synaptic pulses
        if fnum%2==0 and _short_edges and len(VideoCapturePipeline._NN_PULSES)<50:
            hot_e=[e for e in _short_edges if heat.get(e['a'],0.)>0.4 or heat.get(e['b'],0.)>0.4]
            src=hot_e if hot_e else _short_edges
            _pe=src[_rand.randint(0,len(src)-1)]
            VideoCapturePipeline._NN_PULSES.append(
                {'a':_pe['a'],'b':_pe['b'],'t':0.,'spd':0.06+_rand.random()*0.08,'dir':1})
        _alive2=[]
        for p2 in VideoCapturePipeline._NN_PULSES:
            p2['t']+=p2['spd']
            if p2['t']<=1.0:
                pax2,pay2,paz2=pnodes[p2['a']]; pbx2,pby2,pbz2=pnodes[p2['b']]
                ppx=int(pax2+(pbx2-pax2)*p2['t']); ppy=int(pay2+(pby2-pay2)*p2['t'])
                pdp=paz2+(pbz2-paz2)*p2['t']; pr=max(2,int(5*(0.5+pdp*0.5)))
                if nn_x0<=ppx<nn_x0+nn_w and nn_y0<=ppy<nn_y0+nn_h:
                    pc=_hcol_gold(0.9+pdp*0.1)
                    cv2.circle(canvas,(ppx,ppy),pr+5,gcd,-1,cv2.LINE_AA)
                    cv2.circle(canvas,(ppx,ppy),pr,pc,-1,cv2.LINE_AA)
                    lx3,ly3=ppx-nn_x0,ppy-nn_y0
                    cv2.circle(_glow,(lx3,ly3),pr+12,
                               (gc[0]/255.*1.2,gc[1]/255.*1.2,gc[2]/255.*1.2),-1)
                    if p2['t']>0.88:
                        heat[p2['b']]=min(1.0,heat.get(p2['b'],0.)+0.5)
                _alive2.append(p2)
        VideoCapturePipeline._NN_PULSES=_alive2

        # Nodes — tiny bright dots, Iron Man style (no halos on cold nodes)
        for ni in sorted(range(len(nn_nodes)), key=lambda i: pnodes[i][2]):
            n2=nn_nodes[ni]; nx2,ny2,nz2=pnodes[ni]
            if not(nn_x0<=nx2<nn_x0+nn_w and nn_y0<=ny2<nn_y0+nn_h): continue
            df2=max(0.,0.5+nz2*0.5)
            nh=heat.get(ni,0.)
            bp=_math.sin(fnum*0.035*n2['spd']+n2['phase'])*0.5+0.5
            eff_h=max(nh,df2*0.25+bp*0.08)
            nr2=max(1,int((1+nh*3)*(0.4+df2*0.6)))  # 1-4px, tiny
            nc=_hcol_gold(eff_h)
            # only a small halo when hot
            if nh>0.5:
                cv2.circle(canvas,(nx2,ny2),nr2+4,(nc[0]//5,nc[1]//5,nc[2]//5),-1,cv2.LINE_AA)
            cv2.circle(canvas,(nx2,ny2),nr2,nc,-1,cv2.LINE_AA)
            # glow only on hot/front nodes
            if nh>0.3 or df2>0.6:
                lx4,ly4=nx2-nn_x0,ny2-nn_y0
                gf3=(df2*0.15+nh*0.55)*breath
                cv2.circle(_glow,(lx4,ly4),nr2+8,
                           (gc[0]/255.*gf3,gc[1]/255.*gf3,gc[2]/255.*gf3),-1)

        # Sun core
        core_r=int(28*breath)
        cv2.circle(_glow,(_lcx,_lcy),core_r+38,
                   (gc[0]/255.*2.0,gc[1]/255.*2.0,gc[2]/255.*2.0),-1)
        for gr in [core_r,core_r-7,core_r-14,core_r-19,core_r-23,3]:
            if gr<1: continue
            ga4=min(255,int(155+(core_r-gr)*5))
            cv2.circle(canvas,(ncx,ncy),gr,
                       (min(255,gc_hot[0]*ga4//220),
                        min(255,gc_hot[1]*ga4//220),
                        min(255,gc_hot[2]*ga4//220)),-1,cv2.LINE_AA)

        # Voice-reactive bloom: when Jarvis is speaking, spike the glow intensity
        try:
            import core.voice as _vm
            _speaking_now = getattr(_vm, 'JARVIS_SPEAKING', False)
        except Exception:
            _speaking_now = False
        _bloom_base = 150.
        if _speaking_now:
            # Pulse between 200-350 in sync with a fast sine for "alive" feel
            _bloom_base = 250. + 100. * abs(_math.sin(fnum * 0.18))

        # Bloom — lower multiplier keeps background visible
        _glow_blur=cv2.GaussianBlur(_glow,(0,0),14)
        _region=canvas[nn_y0:nn_y0+nn_h,nn_x0:nn_x0+nn_w].astype(_np.float32)
        _region=_np.clip(_region+_glow_blur*_bloom_base,0,255)
        canvas[nn_y0:nn_y0+nn_h,nn_x0:nn_x0+nn_w]=_region.astype(_np.uint8)

        # ── Corner brackets ───────────────────────────────────────────
        BLEN=30
        _corner_bracket(canvas,nn_x0,       nn_y0,        1, 1,BLEN,CYAN,DCYAN)
        _corner_bracket(canvas,nn_x0+nn_w,  nn_y0,       -1, 1,BLEN,CYAN,DCYAN)
        _corner_bracket(canvas,nn_x0,        nn_y0+nn_h,   1,-1,BLEN,CYAN,DCYAN)
        _corner_bracket(canvas,nn_x0+nn_w,   nn_y0+nn_h,  -1,-1,BLEN,CYAN,DCYAN)

        # Labels
        cv2.putText(canvas,'JARVIS',(nn_x0+8,nn_y0+15),FONT,0.33,CYAN2,1,cv2.LINE_AA)
        cv2.putText(canvas,f'{goal.upper().replace("_"," ")}',
                    (nn_x0+8,nn_y0+30),FONT,0.30,gc,1,cv2.LINE_AA)

        # ══════════════════════════════════════════════════════════════
        # EXPANDABLE MINI PANELS
        # Four panels sit in the corners of the sphere area. Click any
        # mini panel to expand it to a variable-scale overlay.  Scroll
        # wheel adjusts the expansion fraction (0.3-0.96). Click
        # anywhere outside a panel's expanded view to collapse it.
        # Only one panel can be expanded at a time.
        # ══════════════════════════════════════════════════════════════

        # ── Animate panel scales ──────────────────────────────────────
        LERP = 0.18
        for _p in VideoCapturePipeline._PANELS.values():
            _p['scale'] += (_p['target'] - _p['scale']) * LERP
            if _p['scale'] < 0.003:
                _p['scale'] = 0.0

        # ── Set mini_rect positions (top-left, bottom-left, bottom-mid, top-right)
        _PM = VideoCapturePipeline._PANELS
        _PM['sysstat']['mini_rect'] = (nn_x0 + 8, nn_y0 + 8,               170, 100)
        _PM['goals'  ]['mini_rect'] = (nn_x0 + 8, nn_y0 + nn_h - 85 - 8,  200, 80 )
        _PM['thought']['mini_rect'] = (nn_x0 + nn_w//2 - 120, nn_y0 + nn_h - 75 - 8, 240, 70)
        _PM['camera' ]['mini_rect'] = (nn_x0 + nn_w - 224 - 8, nn_y0 + 8, 224, 126)

        # ── Register mouse/scroll callback once ──────────────────────
        if not VideoCapturePipeline._MOUSE_CB_SET:
            def _on_mouse(event, mx, my, flags, param):
                panels = VideoCapturePipeline._PANELS
                # Scroll wheel — adjust exp_scale of the open panel
                if event == cv2.EVENT_MOUSEWHEEL:
                    for _pp in panels.values():
                        if _pp['target'] > 0.05:
                            _delta = 0.05 if flags > 0 else -0.05
                            _pp['exp_scale'] = max(0.30, min(0.96, _pp['exp_scale'] + _delta))
                            _pp['target'] = _pp['exp_scale']
                    return
                if event != cv2.EVENT_LBUTTONDOWN:
                    return
                # If any panel is expanded, a click collapses it
                _any_open = any(_pp['target'] > 0.05 for _pp in panels.values())
                if _any_open:
                    for _pp in panels.values():
                        _pp['target'] = 0.0
                    return
                # Click on a mini rect → expand it, collapse others
                for _pid, _pp in panels.items():
                    rx, ry, rw, rh = _pp['mini_rect']
                    if rx <= mx < rx + rw and ry <= my < ry + rh:
                        for _qp in panels.values():
                            _qp['target'] = 0.0
                        _pp['target'] = _pp['exp_scale']
                        return
            try:
                cv2.setMouseCallback(window_name, _on_mouse)
                VideoCapturePipeline._MOUSE_CB_SET = True
            except Exception:
                pass

        # ── Draw mini panels — fully organic, no rectangles ──────────
        # All elements use arcs, ellipses, and circles only.

        # ── Camera mini — circular porthole ──────────────────────────
        _cam_rx, _cam_ry, _cam_rw, _cam_rh = _PM['camera']['mini_rect']
        _cam_cx = _cam_rx + _cam_rw // 2
        _cam_cy = _cam_ry + _cam_rh // 2
        _cam_r  = min(_cam_rw, _cam_rh) // 2 - 6
        # Outer decoration rings
        cv2.circle(canvas, (_cam_cx, _cam_cy), _cam_r + 9,
                   (DCYAN[0]//3, DCYAN[1]//3, DCYAN[2]//3), 1, cv2.LINE_AA)
        cv2.circle(canvas, (_cam_cx, _cam_cy), _cam_r + 5, DCYAN, 1, cv2.LINE_AA)
        # Tick marks
        for _ti in range(0, 360, 20):
            _ta = _math.radians(_ti)
            _is_maj = _ti % 60 == 0
            _tir = _cam_r + 6; _tor = _cam_r + (11 if _is_maj else 8)
            cv2.line(canvas,
                     (int(_cam_cx + _tir*_math.cos(_ta)), int(_cam_cy + _tir*_math.sin(_ta))),
                     (int(_cam_cx + _tor*_math.cos(_ta)), int(_cam_cy + _tor*_math.sin(_ta))),
                     CYAN if _is_maj else DCYAN, 1, cv2.LINE_AA)
        # Circular-masked webcam frame (laptop camera, not screen capture)
        _wc_frame = VideoCapturePipeline._WEBCAM_FRAME
        if _wc_frame is not None:
            _mf = cv2.resize(_wc_frame, (_cam_rw, _cam_rh))
            _cmask = _np.zeros((_cam_rh, _cam_rw), dtype=_np.uint8)
            cv2.circle(_cmask, (_cam_rw // 2, _cam_rh // 2), max(1, _cam_r - 1), 255, -1)
            _croi = canvas[_cam_ry:_cam_ry+_cam_rh, _cam_rx:_cam_rx+_cam_rw]
            _np.copyto(_croi, _mf, where=(_cmask[..., _np.newaxis] > 0))
        else:
            cv2.circle(canvas, (_cam_cx, _cam_cy), max(1, _cam_r - 1), (4, 3, 2), -1)
        cv2.circle(canvas, (_cam_cx, _cam_cy), _cam_r, CYAN, 1, cv2.LINE_AA)
        cv2.putText(canvas, 'VISION', (_cam_cx - 17, _cam_ry + _cam_rh + 10),
                    FONT, 0.25, DCYAN, 1, cv2.LINE_AA)

        # Sysstat mini removed — large gauges in sidebar replace it
        _PM['sysstat']['mini_rect'] = (nn_x0 + 16, nn_y0 + 12, 4, 4)  # zero-area, click disabled

        # ── Goals mini — arc node cluster ─────────────────────────────
        _gl_rx, _gl_ry, _gl_rw, _gl_rh = _PM['goals']['mini_rect']
        _gl_cx = _gl_rx + _gl_rw // 2;  _gl_cy = _gl_ry + 28
        # Arc bracket curving above goal text
        cv2.ellipse(canvas, (_gl_cx, _gl_cy), (_gl_rw // 2 - 6, 22),
                    0, 195, 345, DCYAN, 1, cv2.LINE_AA)
        # Central node dot
        cv2.circle(canvas, (_gl_cx, _gl_cy), 5, CYAN, -1, cv2.LINE_AA)
        cv2.circle(canvas, (_gl_cx, _gl_cy), 8, DCYAN, 1, cv2.LINE_AA)
        _goal_disp = goal[:26].replace('_', ' ').upper()
        (_gtw2, _gth2), _ = cv2.getTextSize(_goal_disp, FONT, 0.27, 1)
        cv2.putText(canvas, _goal_disp, (_gl_cx - _gtw2 // 2, _gl_cy + 20),
                    FONT, 0.27, CYAN, 1, cv2.LINE_AA)
        # Queued goals as satellite dots
        try:
            import json as _js
            with open('data/state.json') as _sf:
                _stk = _js.load(_sf).get('goal_stack', []) or []
            for _gi3, _gg3 in enumerate(_stk[:3]):
                _dot_x = _gl_rx + 14 + _gi3 * 56
                _dot_y = _gl_ry + _gl_rh - 10
                cv2.line(canvas, (_gl_cx, _gl_cy + 10), (_dot_x, _dot_y),
                         (DCYAN[0]//2, DCYAN[1]//2, DCYAN[2]//2), 1, cv2.LINE_AA)
                cv2.circle(canvas, (_dot_x, _dot_y), 3, DCYAN, -1, cv2.LINE_AA)
                _gtxt3 = str(_gg3)[:10].replace('_', ' ')
                cv2.putText(canvas, _gtxt3, (_dot_x - 14, _dot_y + 11),
                            FONT, 0.20, DCYAN, 1, cv2.LINE_AA)
        except Exception:
            pass

        # ── Thought mini — arc text strip ─────────────────────────────
        _th_rx, _th_ry, _th_rw, _th_rh = _PM['thought']['mini_rect']
        _th_cx = _th_rx + _th_rw // 2;  _th_cy = _th_ry + 8
        # Arc above the text
        cv2.ellipse(canvas, (_th_cx, _th_cy + 18), (_th_rw // 2 - 6, 16),
                    0, 202, 338, DCYAN, 1, cv2.LINE_AA)
        cv2.circle(canvas, (_th_cx, _th_cy + 4), 3, CYAN, -1, cv2.LINE_AA)
        _th_last = (thoughts or ['...'])[-1][:38]
        (_ttw, _tth), _ = cv2.getTextSize(_th_last, FONT, 0.27, 1)
        cv2.putText(canvas, _th_last, (_th_cx - _ttw // 2, _th_cy + 38),
                    FONT, 0.27, WHITE, 1, cv2.LINE_AA)
        if len(thoughts or []) > 1:
            _th_prev = (thoughts)[-2][:38]
            (_tp2w, _), _ = cv2.getTextSize(_th_prev, FONT, 0.22, 1)
            cv2.putText(canvas, _th_prev, (_th_cx - _tp2w // 2, _th_cy + 52),
                        FONT, 0.22, DCYAN, 1, cv2.LINE_AA)

        # ── Draw expanded panel overlay ───────────────────────────────
        for _eid, _ep in VideoCapturePipeline._PANELS.items():
            _es = _ep['scale']
            if _es < 0.02:
                continue
            _ew = int(WIN_W * _ep['exp_scale'] * 0.92)
            _eh = int(WIN_H * _ep['exp_scale'] * 0.92)
            _ex = (WIN_W - _ew) // 2
            _ey = (WIN_H - _eh) // 2
            # Dim everything behind the panel
            _overlay = canvas.copy()
            cv2.rectangle(_overlay, (0, 0), (WIN_W, WIN_H), (0, 0, 0), -1)
            cv2.addWeighted(_overlay, 0.55 * _es, canvas, 1.0, 0, canvas)
            # Panel background
            _pw = int(_ew * _es); _ph = int(_eh * _es)
            _px2 = (WIN_W - _pw) // 2; _py2 = (WIN_H - _ph) // 2
            cv2.rectangle(canvas, (_px2, _py2), (_px2+_pw, _py2+_ph), (14, 9, 2), -1)
            # Elliptical frame — no corner brackets
            _epx = _px2 + _pw // 2;  _epy = _py2 + _ph // 2
            cv2.ellipse(canvas, (_epx, _epy), (_pw // 2, _ph // 2),
                        0, 0, 360, DCYAN, 1, cv2.LINE_AA)
            cv2.ellipse(canvas, (_epx, _epy), (_pw // 2 + 3, _ph // 2 + 3),
                        0, 0, 360, (DCYAN[0]//2, DCYAN[1]//2, DCYAN[2]//2), 1, cv2.LINE_AA)
            cv2.ellipse(canvas, (_epx, _epy), (_pw // 2 - 2, _ph // 2 - 2),
                        0, 0, 360, CYAN, 1, cv2.LINE_AA)
            # Arc tick marks at cardinal points
            for _adeg in range(0, 360, 30):
                _ar = _math.radians(_adeg)
                _is_card = _adeg % 90 == 0
                _r1x = int(_epx + (_pw//2 + 4) * _math.cos(_ar))
                _r1y = int(_epy + (_ph//2 + 4) * _math.sin(_ar))
                _r2x = int(_epx + (_pw//2 + (10 if _is_card else 6)) * _math.cos(_ar))
                _r2y = int(_epy + (_ph//2 + (10 if _is_card else 6)) * _math.sin(_ar))
                cv2.line(canvas, (_r1x, _r1y), (_r2x, _r2y),
                         CYAN if _is_card else DCYAN, 1, cv2.LINE_AA)
            # Title bar
            _etitle = {'camera':'VISION', 'sysstat':'SYSTEM STATUS',
                       'goals':'GOAL STACK', 'thought':'INNER MONOLOGUE'}.get(_eid, _eid.upper())
            _gtext(canvas, _etitle, (_px2+12, _py2+20), 0.55, CYAN, DCYAN, 1)
            cv2.putText(canvas, '[click anywhere to close]  [scroll to resize]',
                        (_px2+12, _py2+34), FONT, 0.26, DCYAN, 1, cv2.LINE_AA)
            cv2.ellipse(canvas, (_px2+_pw//2, _py2+38), (_pw//2-8, 6),
                        0, 0, 180, DCYAN, 1, cv2.LINE_AA)
            _cy_e = _py2 + 52
            _cx_e = _px2 + 16
            _cw_e = _pw - 32

            if _eid == 'camera':
                if frame is not None:
                    _vw = _cw_e; _vh = int(_cw_e * 9 / 16)
                    if _vh > _ph - 56: _vh = _ph - 56; _vw = int(_vh * 16 / 9)
                    _ef = cv2.resize(frame, (_vw, _vh))
                    _vx = _px2 + (_pw - _vw) // 2
                    canvas[_cy_e:_cy_e+_vh, _vx:_vx+_vw] = _ef
                    # Elliptical frame around video instead of rectangle
                    _vfcx = _vx + _vw // 2;  _vfcy = _cy_e + _vh // 2
                    cv2.ellipse(canvas, (_vfcx, _vfcy), (_vw//2, _vh//2),
                                0, 0, 360, DCYAN, 1, cv2.LINE_AA)

            elif _eid == 'sysstat':
                _gr = min(38, (_ph - 56) // 3)
                _row1_cy = _cy_e + _gr + 10
                _row2_cy = _cy_e + 3*_gr + 30
                _g1x = _px2 + _pw//4;  _g2x = _px2 + 3*_pw//4
                for _gcx, _gpct, _glbl, _gcol in [
                    (_g1x, cpu_pct,  'CPU',  GREEN if cpu_pct < 0.65 else (ORANGE if cpu_pct < 0.85 else RED)),
                    (_g2x, gpu_util, 'GPU',  CYAN  if gpu_util < 0.65 else (ORANGE if gpu_util < 0.85 else RED)),
                ]:
                    _gc = (_gcol[0]//4, _gcol[1]//4, _gcol[2]//4)
                    _arc_gauge(canvas, _gcx, _row1_cy, _gr, _gpct, _gcol, _gc, VCYAN)
                    _gs = f'{int(_gpct*100)}%'
                    (_gtw,_gth),_ = cv2.getTextSize(_gs, FONT, 0.45, 1)
                    cv2.putText(canvas, _gs, (_gcx-_gtw//2, _row1_cy+_gth//2), FONT, 0.45, _gcol, 1, cv2.LINE_AA)
                    cv2.putText(canvas, _glbl, (_gcx-12, _row1_cy+_gth//2+16), FONT, 0.32, DCYAN, 1, cv2.LINE_AA)
                _g3x = _px2 + _pw//4;  _g4x = _px2 + 3*_pw//4
                for _gcx, _gpct, _glbl, _gcol in [
                    (_g3x, ram_pct, 'RAM',  GREEN if ram_pct < 0.75 else (ORANGE if ram_pct < 0.90 else RED)),
                    (_g4x, gpu_mem, 'VRAM', CYAN  if gpu_mem  < 0.75 else (ORANGE if gpu_mem  < 0.90 else RED)),
                ]:
                    _gc = (_gcol[0]//4, _gcol[1]//4, _gcol[2]//4)
                    _arc_gauge(canvas, _gcx, _row2_cy, _gr, _gpct, _gcol, _gc, VCYAN)
                    _gs = f'{int(_gpct*100)}%'
                    (_gtw,_gth),_ = cv2.getTextSize(_gs, FONT, 0.45, 1)
                    cv2.putText(canvas, _gs, (_gcx-_gtw//2, _row2_cy+_gth//2), FONT, 0.45, _gcol, 1, cv2.LINE_AA)
                    cv2.putText(canvas, _glbl, (_gcx-14, _row2_cy+_gth//2+16), FONT, 0.32, DCYAN, 1, cv2.LINE_AA)

            elif _eid == 'goals':
                cv2.putText(canvas, f'ACTIVE: {goal}', (_cx_e, _cy_e+14), FONT, 0.55, CYAN, 1, cv2.LINE_AA)
                _cy_e += 30
                try:
                    import json as _js2
                    with open('data/state.json') as _sf2:
                        _st2 = _js2.load(_sf2)
                    _stk2 = _st2.get('goal_stack', []) or []
                    for _gi2, _gs2 in enumerate(_stk2[:12]):
                        cv2.putText(canvas, f'  {_gi2+1}. {str(_gs2)[:60]}',
                                    (_cx_e, _cy_e + 20 + _gi2*22), FONT, 0.40, WHITE, 1, cv2.LINE_AA)
                except Exception:
                    pass

            elif _eid == 'thought':
                _lh_e = max(14, (_ph - 56) // max(1, min(20, len(thoughts or ['']))))
                _max_e = max(1, (_ph - 56) // _lh_e)
                _chars_e = _cw_e * 2 // 7
                for _ti, _tl in enumerate((thoughts or [])[-_max_e:]):
                    _is_last = _ti == min(_max_e, len(thoughts)) - 1
                    _tc = CYAN if _is_last else (WHITE if _ti >= _max_e - 4 else DCYAN)
                    _td = (_tl[:_chars_e] + '..') if len(_tl) > _chars_e else _tl
                    cv2.putText(canvas, ('> ' if _is_last else '  ') + _td,
                                (_cx_e, _cy_e + _ti*_lh_e), FONT, 0.32, _tc, 1, cv2.LINE_AA)
            break  # only one expanded at a time

        # ══════════════════════════════════════════════════════════════
        # RIGHT SIDEBAR
        # ══════════════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════════════
        # RIGHT SIDE — Rainmeter-style ring widgets, floating on black
        # ══════════════════════════════════════════════════════════════
        sx = CAM_W
        px = sx + 14

        # ── Ring widget helper ────────────────────────────────────────
        def _ring_widget(cx, cy, r, lines, label='', col=CYAN, dim=DCYAN, tick_step=20):
            """Circular ring widget: concentric rings + tick marks + centered text."""
            # Three concentric rings
            cv2.circle(canvas, (cx, cy), r + 6, (dim[0]//3, dim[1]//3, dim[2]//3), 1, cv2.LINE_AA)
            cv2.circle(canvas, (cx, cy), r + 2, dim, 1, cv2.LINE_AA)
            cv2.circle(canvas, (cx, cy), r - 4, (col[0]//3, col[1]//3, col[2]//3), 1, cv2.LINE_AA)
            cv2.circle(canvas, (cx, cy), r,     col, 1, cv2.LINE_AA)
            # Tick marks around outer ring
            for _td in range(0, 360, tick_step):
                _ta = _math.radians(_td)
                _is_maj = _td % 90 == 0
                _tr1 = r + 8;  _tr2 = r + (15 if _is_maj else 10)
                cv2.line(canvas,
                         (int(cx + _tr1*_math.cos(_ta)), int(cy + _tr1*_math.sin(_ta))),
                         (int(cx + _tr2*_math.cos(_ta)), int(cy + _tr2*_math.sin(_ta))),
                         col if _is_maj else dim, 1, cv2.LINE_AA)
            # Centered text inside
            _total_h = len(lines) * 20
            _start_y = cy - _total_h // 2 + 10
            for _li, (_txt, _sz, _tc) in enumerate(lines):
                (_tw, _th), _ = cv2.getTextSize(_txt, FONT, _sz, 2 if _sz >= 0.55 else 1)
                cv2.putText(canvas, _txt,
                            (cx - _tw // 2, _start_y + _li * 22),
                            FONT, _sz, _tc, 2 if _sz >= 0.55 else 1, cv2.LINE_AA)
            # Label below ring
            if label:
                (_lw, _), _ = cv2.getTextSize(label, FONT, 0.26, 1)
                cv2.putText(canvas, label, (cx - _lw // 2, cy + r + 18),
                            FONT, 0.26, dim, 1, cv2.LINE_AA)

        # ── Clock ring ────────────────────────────────────────────────
        import datetime as _dt
        _now_dt = _dt.datetime.now()
        _clk_cx = sx + SIDE_W // 2
        _clk_cy = HDR_H + 72
        _ring_widget(_clk_cx, _clk_cy, 58, [
            (_now_dt.strftime('%H:%M'), 0.70, CYAN),
            (_now_dt.strftime('%S'), 0.38, DCYAN),
        ], _now_dt.strftime('%a  %d %b').upper(), CYAN, DCYAN, 15)

        # ── Cognitive state — compact text below clock ────────────────
        _cog_y = _clk_cy + 58 + 28
        chars  = (SIDE_W - 20) * 2 // 7
        avail_h = WIN_H - FOOT_H - _cog_y - 260  # leave space for gauge rings
        max_lines = max(1, avail_h // 14)
        recent = thoughts[-max_lines:] if len(thoughts) > max_lines else thoughts
        for _i, _line in enumerate(recent):
            _is_last = (_i == len(recent) - 1)
            _col = CYAN if _is_last else (WHITE if _i >= len(recent) - 3 else DCYAN)
            _pref = '> ' if _is_last else '  '
            _disp = (_line[:chars] + '..') if len(_line) > chars else _line
            cv2.putText(canvas, f'{_pref}{_disp}',
                        (px, _cog_y + _i * 14), FONT, 0.28, _col, 1, cv2.LINE_AA)

        # ── CPU / GPU ring widgets ────────────────────────────────────
        cpu_col  = RED if cpu_pct  > 0.85 else (ORANGE if cpu_pct  > 0.65 else GREEN)
        gpu_col  = RED if gpu_util > 0.85 else (ORANGE if gpu_util > 0.65 else CYAN)
        ram_col  = RED if ram_pct  > 0.90 else (ORANGE if ram_pct  > 0.75 else GREEN)
        vram_col = RED if gpu_mem  > 0.90 else (ORANGE if gpu_mem  > 0.75 else CYAN)
        _g_r1 = 48;  _g_r2 = 34
        _g1cx = sx + SIDE_W // 4;   _g2cx = sx + 3 * SIDE_W // 4
        _g_row1 = WIN_H - FOOT_H - 185
        _g_row2 = WIN_H - FOOT_H - 90
        _ring_widget(_g1cx, _g_row1, _g_r1, [
            ('CPU', 0.26, DCYAN), (f'{int(cpu_pct*100)}%', 0.58, cpu_col)],
            '', cpu_col, (cpu_col[0]//4, cpu_col[1]//4, cpu_col[2]//4), 30)
        _ring_widget(_g2cx, _g_row1, _g_r1, [
            ('GPU', 0.26, DCYAN), (f'{int(gpu_util*100)}%', 0.58, gpu_col)],
            '', gpu_col, (gpu_col[0]//4, gpu_col[1]//4, gpu_col[2]//4), 30)
        _ring_widget(_g1cx, _g_row2, _g_r2, [
            ('RAM', 0.24, DCYAN), (f'{int(ram_pct*100)}%', 0.42, ram_col)],
            '', ram_col, (ram_col[0]//4, ram_col[1]//4, ram_col[2]//4), 45)
        _ring_widget(_g2cx, _g_row2, _g_r2, [
            ('VRAM', 0.22, DCYAN), (f'{int(gpu_mem*100)}%', 0.42, vram_col)],
            '', vram_col, (vram_col[0]//4, vram_col[1]//4, vram_col[2]//4), 45)

        # ── Detection dots (compact, no bars) ────────────────────────
        if objs:
            _det_y = _cog_y + max_lines * 14 + 8
            for _di, _obj in enumerate((objs or [])[:4]):
                _lbl = str(_obj.get('label', _obj.get('class_name', '?')))[:12]
                _conf = float(_obj.get('confidence', _obj.get('conf', 0.0)))
                _det_col = GREEN if _conf > 0.7 else (ORANGE if _conf > 0.4 else DCYAN)
                cv2.circle(canvas, (px + 4, _det_y + _di * 14), 3, _det_col, -1, cv2.LINE_AA)
                cv2.putText(canvas, f'{_lbl}  {_conf:.0%}',
                            (px + 12, _det_y + _di * 14 + 4),
                            FONT, 0.26, _det_col, 1, cv2.LINE_AA)

        # ══════════════════════════════════════════════════════════════
        # TEXT INPUT / CONVERSATION — bottom strip of camera area
        # ══════════════════════════════════════════════════════════════
        _tc_y0 = WIN_H - FOOT_H - 110
        _tc_x0 = 14
        _tc_w  = CAM_W - 28
        _tc_active = VideoCapturePipeline._TEXT_ACTIVE
        _tc_input  = VideoCapturePipeline._TEXT_INPUT

        # Section arc header
        cv2.ellipse(canvas, (_tc_x0 + _tc_w // 2, _tc_y0 - 4),
                    (_tc_w // 2, 6), 0, 180, 360, DCYAN, 1, cv2.LINE_AA)
        _gtext(canvas, 'COMM LINK', (_tc_x0, _tc_y0 + 2), 0.28, CYAN2, DCYAN)

        # Conversation log — last 4 lines
        _log = VideoCapturePipeline._CONVO_LOG[-4:]
        for _li2, (_spk, _txt) in enumerate(_log):
            _lc = CYAN if _spk == 'JARVIS' else WHITE
            _disp2 = (_txt[:(_tc_w * 2 // 7)] + '..') if len(_txt) > _tc_w * 2 // 7 else _txt
            cv2.putText(canvas, f'[{_spk}] {_disp2}',
                        (_tc_x0, _tc_y0 + 18 + _li2 * 16),
                        FONT, 0.28, _lc, 1, cv2.LINE_AA)

        # Input line
        _cursor_blink = '|' if int(fnum / 12) % 2 == 0 and _tc_active else ''
        _ic = CYAN if _tc_active else DCYAN
        cv2.putText(canvas, f'> {_tc_input}{_cursor_blink}',
                    (_tc_x0, _tc_y0 + 90),
                    FONT, 0.36, _ic, 1, cv2.LINE_AA)
        # Arc underline for input
        cv2.ellipse(canvas, (_tc_x0 + _tc_w // 2, _tc_y0 + 96),
                    (_tc_w // 2, 4), 0, 0, 180, _ic, 1, cv2.LINE_AA)
        if not _tc_active:
            cv2.putText(canvas, '[press T to type]',
                        (_tc_x0 + _tc_w - 110, _tc_y0 + 90),
                        FONT, 0.24, DCYAN, 1, cv2.LINE_AA)

        # ══════════════════════════════════════════════════════════════
        # FOOTER
        # ══════════════════════════════════════════════════════════════
        # Footer — no fill, floating on black
        fy = WIN_H - FOOT_H
        # Arc sweep instead of a solid bar
        cv2.ellipse(canvas, (WIN_W // 2, WIN_H),
                    (WIN_W // 2, FOOT_H + 6), 0, 180, 360,
                    (DCYAN[0]//2, DCYAN[1]//2, DCYAN[2]//2), 1, cv2.LINE_AA)
        fmy = fy + 22

        # Voice dot + text
        if voice_text:
            pulse_r2 = 5 + int(3 * abs(_math.sin(fnum * 0.1)))
            _gcircle(canvas, (18, fmy - 5), pulse_r2, ORANGE, DORANGE, -1)
            vt = (voice_text[:80] + '…') if len(voice_text) > 80 else voice_text
            cv2.putText(canvas, vt, (32, fmy), FONT, 0.38, ORANGE, 1, cv2.LINE_AA)
        else:
            cv2.circle(canvas, (18, fmy - 5), 5, DCYAN, 1, cv2.LINE_AA)
            cv2.putText(canvas, 'VOICE READY  F9=PTT',
                        (32, fmy), FONT, 0.35, DCYAN, 1, cv2.LINE_AA)

        # Objective right-aligned
        obj_label = f'>> {goal[:55]}'
        (tw3, _), _ = cv2.getTextSize(obj_label, FONT, 0.38, 1)
        _gtext(canvas, obj_label, (WIN_W - tw3 - 10, fmy), 0.38, CYAN, DCYAN)

        # Dot constellation centre-footer (no rectangles)
        for _fi, xi in enumerate(range(WIN_W // 2 - 60, WIN_W // 2 + 61, 12)):
            _fr = 2 if _fi % 3 == 0 else 1
            cv2.circle(canvas, (xi, fy + 10), _fr, VCYAN, -1, cv2.LINE_AA)

        if _CV2_GUI_OK:
            # Make window resizable on first render; strip OS decoration
            if not getattr(VideoCapturePipeline, '_WINDOW_CREATED', False):
                cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
                cv2.resizeWindow(window_name, WIN_W, WIN_H)
                VideoCapturePipeline._WINDOW_CREATED = True
                # Strip title bar — try multiple methods
                import subprocess as _sp2, time as _t2
                _t2.sleep(0.5)  # give WM time to register
                # Method 1: wmctrl fullscreen (works on most GNOME/X11)
                try:
                    _sp2.Popen(['wmctrl', '-r', window_name,
                                '-b', 'add,fullscreen'])
                except FileNotFoundError:
                    pass
                # Method 2: xprop remove decorations
                try:
                    _wid = _sp2.check_output(
                        ['xdotool', 'search', '--name', window_name],
                        timeout=2).decode().split()[0]
                    _sp2.Popen(['xprop', '-id', _wid,
                                '-f', '_MOTIF_WM_HINTS', '32c',
                                '-set', '_MOTIF_WM_HINTS', '2, 0, 0, 0, 0'])
                except Exception:
                    pass
                # Method 3: cv2 fullscreen flag
                try:
                    cv2.setWindowProperty(window_name,
                                          cv2.WND_PROP_FULLSCREEN,
                                          cv2.WINDOW_FULLSCREEN)
                except Exception:
                    pass
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
        elif config.ENABLE_DISPLAY_UI and _CV2_GUI_OK:
            # Jarvis/Ultron HUD window (replaces old plain imshow).
            # Gated on _CV2_GUI_OK (probed at module load via getBuildInformation):
            # cv2 built without GTK/Qt calls C++ terminate() in imshow, which
            # bypasses Python exception handling and crashes the whole process.
            self._jarvis_imshow(window_name, frame, objs)
            key = self._safe_wait_key()
        else:
            key = self._safe_wait_key()

        # ── Text input mode ──────────────────────────────────────────
        _active = VideoCapturePipeline._TEXT_ACTIVE
        if _active:
            if key == 27:  # Escape — cancel input
                VideoCapturePipeline._TEXT_ACTIVE = False
                VideoCapturePipeline._TEXT_INPUT  = ''
            elif key in (13, 10):  # Enter — submit
                _msg = VideoCapturePipeline._TEXT_INPUT.strip()
                VideoCapturePipeline._TEXT_ACTIVE = False
                VideoCapturePipeline._TEXT_INPUT  = ''
                if _msg:
                    VideoCapturePipeline._CONVO_LOG.append(('SCOTT', _msg))
                    # Keep log bounded
                    if len(VideoCapturePipeline._CONVO_LOG) > 20:
                        VideoCapturePipeline._CONVO_LOG = VideoCapturePipeline._CONVO_LOG[-20:]
                    # Route to Jarvis brain in background thread
                    def _ask_jarvis(_m=_msg):
                        try:
                            from jarvis.brain import get_brain as _gb
                            _reply = _gb().respond(_m)
                            VideoCapturePipeline._CONVO_LOG.append(('JARVIS', _reply))
                            if len(VideoCapturePipeline._CONVO_LOG) > 20:
                                VideoCapturePipeline._CONVO_LOG = VideoCapturePipeline._CONVO_LOG[-20:]
                            # Also speak it
                            try:
                                from core import voice as _vc
                                _vc.speak(_reply)
                            except Exception:
                                pass
                        except Exception as _be:
                            VideoCapturePipeline._CONVO_LOG.append(('JARVIS', f'[error: {_be}]'))
                    import threading as _thr
                    _thr.Thread(target=_ask_jarvis, daemon=True).start()
            elif key == 8 or key == 127:  # Backspace
                VideoCapturePipeline._TEXT_INPUT = VideoCapturePipeline._TEXT_INPUT[:-1]
            elif 32 <= key < 127:  # Printable
                if len(VideoCapturePipeline._TEXT_INPUT) < 120:
                    VideoCapturePipeline._TEXT_INPUT += chr(key)
            return True  # consume all keys while typing

        # ── Normal key handling ───────────────────────────────────────
        if key == ord('q'):
            self.display.quit = True
            return False
        if key == ord('t'):  # T — activate text input
            VideoCapturePipeline._TEXT_ACTIVE = True
            VideoCapturePipeline._TEXT_INPUT  = ''
        if key == ord('f') and _CV2_GUI_OK:
            VideoCapturePipeline._FULLSCREEN = not VideoCapturePipeline._FULLSCREEN
            prop = cv2.WINDOW_FULLSCREEN if VideoCapturePipeline._FULLSCREEN else cv2.WINDOW_NORMAL
            cv2.setWindowProperty(window_name, cv2.WND_PROP_FULLSCREEN, prop)
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
