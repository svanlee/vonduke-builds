"""
Hardware manager — monitors all hardware adapters and provides
a summary string for LLM context.

Also owns **data/hardware_manifest.json**, the live registry of what is
physically attached to this box. It replaces data/cognitive/belief_state.json,
which nothing has written since core/cognitive.py dropped BeliefState —
core/claude_bridge.py still serves that file but only alongside an `age_s`
measured in weeks, which is a diagnostic, not a hardware inventory.

The manifest is a *sweep*, not a guess: /dev/video*, /dev/ttyUSB*,
/dev/ttyACM* and /proc/bus/input/devices are re-read on every pass, and when
a KB2040 running the bridge firmware is present its I2C bus and ADC channels
are read through it too. Written every MANIFEST_INTERVAL seconds so a reader
(the bridge's /state, an operator, a training script) never has to ask the
bot whether a device is still there.

Wiring: main.py calls `start_manifest_writer()` once at boot. It is a daemon
thread and never raises — a manifest that fails to build is an empty manifest
with an "error" field, not a dead bot. `HardwareManager()` as constructed in
core/runtime.py is unaffected: the adapter monitor loop and the manifest
writer are independent, and only the module-level singleton writes to disk.
"""
import json
import os
import re
import threading
import time
from hardware.capture_card import CaptureCardAdapter
from hardware.ftdi import FTDIAdapter
from hardware.gpu import GPUAdapter
from hardware.cpu import CPUAdapter

MANIFEST_PATH     = 'data/hardware_manifest.json'
MANIFEST_INTERVAL = 60          # seconds between manifest rewrites
MANIFEST_VERSION  = '1.0.0'

# ADC channels read on each sweep when a KB2040 answers. All four of the
# RP2040's ADC pins — a bench rig's sensor could be on any of them, and a
# read of an unconnected pin is a floating value, not an error.
ANALOG_PINS = ('A0', 'A1', 'A2', 'A3')

# Device-node globs, as (manifest key, directory, filename prefix). Globbed
# by listdir rather than glob.glob so a permission error on one directory
# doesn't abort the whole sweep.
_DEV_SCANS = (
    ('video',   '/dev', 'video'),
    ('ttyUSB',  '/dev', 'ttyUSB'),
    ('ttyACM',  '/dev', 'ttyACM'),
)

_INPUT_DEVICES_PATH = '/proc/bus/input/devices'

class HardwareManager:
    def __init__(self):
        self._adapters = {
            "capture_card": CaptureCardAdapter(),
            "ftdi": FTDIAdapter(),
            "gpu": GPUAdapter(),
            "cpu": CPUAdapter(),
        }
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._check_interval = 30  # seconds between hardware checks

    def start(self):
        """Start background hardware monitoring."""
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        print("[Hardware] Manager started.")

    def stop(self):
        self._running = False

    def _monitor_loop(self):
        while self._running:
            with self._lock:
                for name, adapter in self._adapters.items():
                    try:
                        adapter.check()
                    except Exception as e:
                        print(f"[Hardware] Error checking {name}: {e}")
            time.sleep(self._check_interval)

    def check_all(self) -> dict:
        """Run a check on all adapters and return status dict."""
        results = {}
        with self._lock:
            for name, adapter in self._adapters.items():
                try:
                    results[name] = {
                        "available": adapter.check(),
                        "metrics": adapter.get_metrics()
                    }
                except Exception as e:
                    results[name] = {"available": False, "error": str(e)}
        return results

    def summary(self) -> str:
        """Return a hardware summary string for LLM context."""
        lines = []
        with self._lock:
            for adapter in self._adapters.values():
                try:
                    lines.append(f"  {adapter.get_summary()}")
                except Exception as e:
                    lines.append(f"  {adapter.state.name}: ERROR ({e})")
        return "Hardware:\n" + "\n".join(lines)

    def get(self, device_id: str):
        """Get a specific adapter by device_id."""
        return self._adapters.get(device_id)


# ── Device sweep ───────────────────────────────────────────────
def _dev_nodes(directory: str, prefix: str) -> list[dict]:
    """Every /dev node under `directory` whose name starts with `prefix`.

    Sorted numerically by trailing index so video10 lands after video2
    rather than between video1 and video2 — the boot log gets read by
    humans looking for "which /dev/videoN is the capture card".
    """
    try:
        names = [n for n in os.listdir(directory) if n.startswith(prefix)]
    except OSError:
        return []

    def _index(name):
        m = re.search(r'(\d+)$', name)
        return int(m.group(1)) if m else -1

    out = []
    for name in sorted(names, key=lambda n: (_index(n), n)):
        path = os.path.join(directory, name)
        entry = {'path': path, 'index': _index(name)}
        try:
            st = os.stat(path)
            entry['mode'] = oct(st.st_mode & 0o777)
            # Readable *and* writable is what actually matters: the `input`
            # group blocker (evdev seeing nothing) and a capture card owned
            # by another process both show up here rather than as a
            # mysterious silent failure downstream.
            entry['readable'] = os.access(path, os.R_OK)
            entry['writable'] = os.access(path, os.W_OK)
        except OSError as e:
            entry['error'] = str(e)
        out.append(entry)
    return out


def _input_devices() -> list[dict]:
    """Parse /proc/bus/input/devices into {name, handlers, phys} records.

    This file is readable without the `input` group, unlike the
    /dev/input/event* nodes it describes — so it answers "is a controller
    plugged in?" even on a box where evdev itself can see nothing (see the
    `ros`-not-in-`input`-group blocker). `accessible` reports whether the
    event node behind it can actually be opened, which is the distinction
    that matters when controller detection reports "no controller".
    """
    try:
        with open(_INPUT_DEVICES_PATH) as f:
            raw = f.read()
    except OSError:
        return []

    devices = []
    for block in raw.split('\n\n'):
        block = block.strip()
        if not block:
            continue
        entry = {}
        for line in block.splitlines():
            if line.startswith('N: Name='):
                entry['name'] = line.partition('=')[2].strip().strip('"')
            elif line.startswith('P: Phys='):
                entry['phys'] = line.partition('=')[2].strip()
            elif line.startswith('H: Handlers='):
                entry['handlers'] = line.partition('=')[2].split()
        if not entry:
            continue
        events = [h for h in entry.get('handlers', []) if h.startswith('event')]
        entry['event_nodes'] = [f'/dev/input/{e}' for e in events]
        entry['accessible'] = all(os.access(p, os.R_OK)
                                  for p in entry['event_nodes']) if events else False
        devices.append(entry)
    return devices


# ── Audio ──────────────────────────────────────────────────────
def _run(cmd: list[str], timeout: float = 5.0) -> str | None:
    """Run a probe command, returning stdout or None. Never raises — a
    missing binary is a normal answer here, not an error."""
    try:
        import subprocess
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout)
    except Exception:
        # FileNotFoundError (binary absent) and TimeoutExpired alike — both
        # mean "this probe has no answer", which the caller handles.
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _audio_devices() -> dict:
    """Audio sinks (outputs) and sources (inputs).

    Preferred probe is `pactl list short sinks|sources`, which reports what
    the sound server will actually route to. pactl ships in pulseaudio-utils
    and is *not* installed on every box that runs PipeWire — this host is one
    of them — so a failed pactl falls back to `aplay -l` / `arecord -l`,
    which read ALSA directly and need no server at all.

    The distinction is recorded in `source` and it matters: ALSA cards mean
    "the kernel sees this hardware", not "the bot can play through it". A
    reader that conflates the two would claim working audio on a box with no
    sound server running.

    Absence is reported as an empty list plus a `note`, never as a missing
    key — a hardware block that vanishes when a probe fails reads exactly
    like a box with no audio hardware, which is how Day 1 produced an answer
    asserting no sound cards exist on a laptop with four of them.
    """
    audio: dict = {'source': None, 'sinks': [], 'sources': [], 'note': None}

    def _short(kind):
        # pactl short format: index \t name \t driver \t sample_spec \t state
        raw = _run(['pactl', 'list', 'short', kind])
        if raw is None:
            return None
        rows = []
        for line in raw.splitlines():
            parts = line.split('\t')
            if len(parts) < 2:
                continue
            rows.append({'name': parts[1],
                         'state': parts[4] if len(parts) > 4 else None})
        return rows

    sinks, sources = _short('sinks'), _short('sources')
    if sinks is not None or sources is not None:
        audio['source'] = 'pactl'
        audio['sinks'] = sinks or []
        audio['sources'] = sources or []
        return audio

    # ── ALSA fallback ──
    def _alsa(cmd):
        raw = _run([cmd, '-l'])
        if raw is None:
            return None
        rows = []
        for line in raw.splitlines():
            m = re.match(r'card (\d+): (\S+) \[([^\]]+)\], '
                         r'device (\d+): (.+?) \[', line)
            if m:
                rows.append({'name': f'hw:{m.group(1)},{m.group(4)} '
                                     f'{m.group(3)} — {m.group(5)}',
                             'state': None})
        return rows

    a_sinks, a_sources = _alsa('aplay'), _alsa('arecord')
    if a_sinks is not None or a_sources is not None:
        audio['source'] = 'alsa'
        audio['sinks'] = a_sinks or []
        audio['sources'] = a_sources or []
        audio['note'] = ('pactl unavailable — these are ALSA cards seen by '
                         'the kernel, not sound-server routing targets')
        return audio

    audio['source'] = 'none'
    audio['note'] = 'no audio probe available (pactl, aplay and arecord all failed)'
    return audio


def _storage() -> dict:
    """Root filesystem usage, from `df -h /`.

    `findmnt -o SOURCE,SIZE` (what the training prompt used through Day 2)
    reports capacity only. Asked how much free space it had, the bot filled
    the gap from priors and answered "approximately 89.5 gigabytes" against a
    real 769G — and credited the live readings for it. Free space has to be
    *in* the sweep; an absent field is not read as unknown.
    """
    storage: dict = {'source': None, 'root': None, 'note': None}
    raw = _run(['df', '-h', '/'])
    if raw is None:
        storage['source'] = 'none'
        storage['note'] = 'df unavailable or failed'
        return storage
    lines = raw.splitlines()
    if len(lines) < 2:
        storage['source'] = 'none'
        storage['note'] = 'df returned no data row'
        return storage
    parts = lines[1].split()
    if len(parts) < 6:
        storage['source'] = 'none'
        storage['note'] = f'unparsed df output: {lines[1]}'
        return storage
    storage['source'] = 'df'
    storage['root'] = {
        'filesystem': parts[0], 'size': parts[1], 'used': parts[2],
        'available': parts[3], 'use_pct': parts[4], 'mounted_on': parts[5],
    }
    return storage


# ── KB2040 bridge ──────────────────────────────────────────────
_bridge_client = None
_bridge_lock = threading.Lock()
_bridge_unavailable_logged = False


def get_bridge_client():
    """The shared uart.bridge_client.BridgeClient, or None if unavailable.

    One instance per process, because it owns a serial port and a second
    one would fight the first for it — the manifest sweep below and the
    `hw` skill steps in skills/skill_system.py both come through here.

    Imported lazily and never fatally: the host-side client ships with the
    KB2040 bridge firmware work, and everything that calls this must
    degrade to "no hardware bridge" rather than refuse to run without it.
    """
    global _bridge_client, _bridge_unavailable_logged
    with _bridge_lock:
        if _bridge_client is not None:
            return _bridge_client
        try:
            from uart.bridge_client import BridgeClient
        except ImportError as e:
            if not _bridge_unavailable_logged:
                print(f'[Hardware] KB2040 bridge client unavailable ({e}) — '
                      f'hw actions and I2C/ADC sweeps disabled')
                _bridge_unavailable_logged = True
            return None
        try:
            client = BridgeClient()
        except Exception as e:
            if not _bridge_unavailable_logged:
                print(f'[Hardware] KB2040 bridge client init failed: {e}')
                _bridge_unavailable_logged = True
            return None
        # `is_connected` is how the other UART drivers here report a device
        # that isn't there (see uart/kb2040_packer.py); tolerate a client
        # that doesn't define it rather than assuming it's absent.
        if not getattr(client, 'is_connected', True):
            return None
        _bridge_client = client
        return _bridge_client


def _kb2040_probe(acm_nodes: list[dict]) -> dict:
    """I2C addresses and ADC readings from the KB2040, if one answers.

    `present` is about the device node; `responding` is about the firmware.
    A KB2040 flashed with the HID firmware rather than the bridge firmware
    shows up as present-but-not-responding, which is exactly the state that
    otherwise gets misread as "board is dead".
    """
    probe = {'present': bool(acm_nodes), 'responding': False}
    if not acm_nodes:
        return probe

    client = get_bridge_client()
    if client is None:
        probe['note'] = 'no host-side BridgeClient — device node present but unprobed'
        return probe

    try:
        addresses = client.i2c_scan()
    except Exception as e:
        probe['error'] = f'i2c_scan: {type(e).__name__}: {e}'
        return probe

    probe['responding'] = True
    # i2c_scan() may hand back the raw response dict or just the address
    # list depending on how the client wraps it — accept either.
    if isinstance(addresses, dict):
        addresses = addresses.get('addresses', [])
    addresses = list(addresses or [])
    probe['i2c'] = {
        'addresses': addresses,
        'hex': [f'0x{a:02x}' for a in addresses if isinstance(a, int)],
        'count': len(addresses),
    }

    analog = {}
    for pin in ANALOG_PINS:
        try:
            resp = client.send({'cmd': 'analog_in', 'pin': pin})
        except Exception as e:
            analog[pin] = {'error': f'{type(e).__name__}: {e}'}
            continue
        if isinstance(resp, dict) and resp.get('ok'):
            analog[pin] = {'value': resp.get('value'),
                           'raw': resp.get('raw'),
                           'volts': resp.get('volts')}
        else:
            analog[pin] = {'error': (resp or {}).get('error', 'no response')
                           if isinstance(resp, dict) else 'no response'}
    probe['analog'] = analog

    try:
        info = client.send({'cmd': 'info'})
        if isinstance(info, dict) and info.get('ok'):
            probe['info'] = {k: info[k] for k in
                             ('node', 'version', 'board', 'cpu_temp_c',
                              'cmd_channel', 'uptime_s') if k in info}
    except Exception:
        # Optional detail — a board that answered i2c_scan is present
        # regardless of whether it answered info.
        pass
    return probe


# ── Manifest ───────────────────────────────────────────────────
def build_manifest() -> dict:
    """One full hardware sweep. Never raises."""
    manifest = {
        'version': MANIFEST_VERSION,
        'generated_at': time.time(),
        'interval_s': MANIFEST_INTERVAL,
        'pid': os.getpid(),
    }
    try:
        import config
        manifest['node_name'] = getattr(config, 'NODE_NAME', 'unknown')
    except Exception:
        manifest['node_name'] = 'unknown'

    devices = {}
    for key, directory, prefix in _DEV_SCANS:
        try:
            devices[key] = _dev_nodes(directory, prefix)
        except Exception as e:
            devices[key] = []
            manifest.setdefault('errors', {})[key] = f'{type(e).__name__}: {e}'
    try:
        devices['input'] = _input_devices()
    except Exception as e:
        devices['input'] = []
        manifest.setdefault('errors', {})['input'] = f'{type(e).__name__}: {e}'
    manifest['devices'] = devices

    try:
        manifest['audio'] = _audio_devices()
    except Exception as e:
        manifest['audio'] = {'source': 'error', 'sinks': [], 'sources': [],
                             'note': f'{type(e).__name__}: {e}'}

    try:
        manifest['storage'] = _storage()
    except Exception as e:
        manifest['storage'] = {'source': 'error', 'root': None,
                               'note': f'{type(e).__name__}: {e}'}

    try:
        manifest['kb2040'] = _kb2040_probe(devices.get('ttyACM', []))
    except Exception as e:
        manifest['kb2040'] = {'present': False, 'responding': False,
                              'error': f'{type(e).__name__}: {e}'}

    manifest['counts'] = {
        'video':  len(devices.get('video', [])),
        'ttyUSB': len(devices.get('ttyUSB', [])),
        'ttyACM': len(devices.get('ttyACM', [])),
        'input':  len(devices.get('input', [])),
        'i2c':    manifest.get('kb2040', {}).get('i2c', {}).get('count', 0),
        'audio_sinks':   len(manifest.get('audio', {}).get('sinks', [])),
        'audio_sources': len(manifest.get('audio', {}).get('sources', [])),
    }
    return manifest


_manifest: dict = {}
_manifest_lock = threading.Lock()
_writer_thread = None


def get_manifest() -> dict:
    """The current manifest.

    Builds one on the spot if the writer thread hasn't produced its first
    pass yet, so an early caller gets real data rather than an empty dict.
    """
    with _manifest_lock:
        if _manifest:
            return dict(_manifest)
    return _refresh_manifest()


def _refresh_manifest() -> dict:
    """Sweep, cache, and write to disk. Returns the new manifest."""
    global _manifest
    manifest = build_manifest()
    with _manifest_lock:
        _manifest = manifest
    try:
        os.makedirs(os.path.dirname(MANIFEST_PATH) or '.', exist_ok=True)
        # Write-then-rename: core/claude_bridge.py reads this file from
        # another thread and a torn read there would look like a missing
        # manifest rather than a partial one.
        tmp = MANIFEST_PATH + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(manifest, f, indent=2)
        os.replace(tmp, MANIFEST_PATH)
    except OSError as e:
        print(f'[Hardware] manifest write failed: {e}')
    return manifest


def _manifest_loop():
    while True:
        try:
            _refresh_manifest()
        except Exception as e:
            print(f'[Hardware] manifest sweep error: {e}')
        time.sleep(MANIFEST_INTERVAL)


def start_manifest_writer() -> bool:
    """Start the manifest sweep on a daemon thread. Idempotent.

    Called once from main.py. Returns False if it was already running.
    """
    global _writer_thread
    with _manifest_lock:
        if _writer_thread is not None and _writer_thread.is_alive():
            return False
        _writer_thread = threading.Thread(target=_manifest_loop,
                                          name='hw-manifest', daemon=True)
        _writer_thread.start()
    print(f'[Hardware] manifest writer → {MANIFEST_PATH} '
          f'(every {MANIFEST_INTERVAL}s)')
    return True


def manifest_summary() -> dict:
    """Compact view for core/claude_bridge.py's /state — counts and
    identities, not the full per-node detail the file itself carries."""
    m = get_manifest()
    devices = m.get('devices', {})
    kb = m.get('kb2040', {})
    generated = m.get('generated_at')
    return {
        'counts': m.get('counts', {}),
        'video':  [d.get('path') for d in devices.get('video', [])],
        'ttyUSB': [d.get('path') for d in devices.get('ttyUSB', [])],
        'ttyACM': [d.get('path') for d in devices.get('ttyACM', [])],
        'input':  [d.get('name') for d in devices.get('input', [])],
        'kb2040': {
            'present': kb.get('present', False),
            'responding': kb.get('responding', False),
            'i2c_addresses': kb.get('i2c', {}).get('hex', []),
        },
        'age_s': round(time.time() - generated, 1) if generated else None,
    }
