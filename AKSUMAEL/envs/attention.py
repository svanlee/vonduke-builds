# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Multi-Environment Attention Manager       ║
# ║  Tracks which envs/ adapter is "focused" and keeps    ║
# ║  the rest alive in the background.                    ║
# ╚══════════════════════════════════════════════════════╝
#
# core/runtime.py's tick loop is still Minecraft-only (see its module
# docstring / core/environment.py's comments) — this manager is the seam
# a caller uses to hold several envs/*.py adapters at once and decide
# which one gets this tick's frames/actions, without touching any
# existing single-env code path.
#
# Cross-process focus requests: axon/hub.py runs as its own OS process
# (see axon/hub.py's module docstring), so it can never call .focus() on
# the same in-memory AttentionManager instance core/runtime.py holds.
# Axon instead builds its own AttentionManager with no real adapters and
# calls .focus() on that — focus() always persists the requested name to
# FOCUS_STATE_PATH — and the runtime's real instance picks up the change
# via sync_external_focus(), same shared-JSON-file handoff
# memory/goals.py's injected_goals.json queue already uses for
# cross-process goal injection.

import json
import pathlib
import threading
import time

FOCUS_STATE_PATH = pathlib.Path('data/attention_focus.json')

IDLE_TICK_INTERVAL_SEC = 5.0


class AttentionManager:
    """Holds `{env_name: BaseEnvironment}` adapters and tracks which one is
    focused. Thread-safe — `focus()`/`get_active()` are called both from a
    caller's main tick and from the background idle-tick thread started by
    `start()`."""

    def __init__(self, envs: dict, default: str = None,
                 idle_tick_interval: float = IDLE_TICK_INTERVAL_SEC):
        self._envs = dict(envs or {})
        self._lock = threading.RLock()
        if default is None or (self._envs and default not in self._envs):
            default = next(iter(self._envs), default)
        self._active_name = default
        self._idle_tick_interval = idle_tick_interval
        self._stop_event = threading.Event()
        self._thread = None

    def focus(self, env_name: str) -> bool:
        """Switch the active env. Returns False (and leaves focus
        unchanged) if `env_name` isn't one of the adapters this instance
        was constructed with — unless it holds no adapters at all (the
        cross-process "just persist the request" case above), in which
        case any name is accepted."""
        with self._lock:
            if self._envs and env_name not in self._envs:
                print(f'[ATTENTION] unknown env "{env_name}" — ignoring '
                      f'(available: {sorted(self._envs)})')
                return False
            changed = env_name != self._active_name
            self._active_name = env_name
            self._persist_focus()
            if changed:
                print(f'[ATTENTION] focus -> "{env_name}"')
            return True

    def get_active(self):
        """The currently focused BaseEnvironment adapter, or None if this
        instance holds no adapters (cross-process request-only mode) or
        the active name doesn't match any held adapter."""
        with self._lock:
            return self._envs.get(self._active_name)

    def get_active_name(self) -> str:
        with self._lock:
            return self._active_name

    def available_envs(self) -> list:
        with self._lock:
            return sorted(self._envs)

    def _persist_focus(self):
        try:
            FOCUS_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            FOCUS_STATE_PATH.write_text(
                json.dumps({'active': self._active_name, 'ts': time.time()}))
        except OSError as e:
            print(f'[ATTENTION] could not persist focus state: {e}')

    def sync_external_focus(self):
        """Pick up a focus change requested by another process (e.g. Axon)
        via FOCUS_STATE_PATH. No-op if the file is missing/unreadable, or
        if it just reflects the focus we already have — so this never
        fights with our own most recent .focus() call."""
        try:
            requested = json.loads(FOCUS_STATE_PATH.read_text()).get('active')
        except (OSError, json.JSONDecodeError):
            return
        if requested and requested != self.get_active_name():
            self.focus(requested)

    def _idle_tick_loop(self):
        """Every `idle_tick_interval` seconds: pick up any external focus
        request, then poke the (possibly unfocused) active adapter with a
        cheap get_frame() call so its underlying connection (ZeroMQ
        socket, ROS2 subscription, ...) doesn't go stale while nothing
        else is driving it."""
        while not self._stop_event.wait(self._idle_tick_interval):
            self.sync_external_focus()
            active = self.get_active()
            if active is None:
                continue
            try:
                active.get_frame()
            except Exception as e:
                print(f'[ATTENTION] idle tick on "{self.get_active_name()}" failed: {e}')

    def start(self):
        """Start the background idle-tick thread. No-op if already running."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._idle_tick_loop, daemon=True, name='AttentionIdleTick')
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
