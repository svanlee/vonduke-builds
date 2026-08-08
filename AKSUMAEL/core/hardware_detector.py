# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Hardware Detector                         ║
# ║  Camera / gamepad / KB2040-UART discovery at startup  ║
# ╚══════════════════════════════════════════════════════╝
#
# Everything here is best-effort and never raises: AKSUMAEL is expected to
# boot and keep running with any subset of its hardware missing (print-mode
# HID, webcam-instead-of-capture-card vision, no controller). The point of
# this module is that the *log* says exactly which subset it got, and that
# the rest of the code auto-binds to whatever node the kernel actually
# handed the device rather than a hardcoded path.

import glob
import os
import subprocess

import config


# ── Camera ────────────────────────────────────────────────────

def detect_camera():
    """Try capture devices 0-5, return first working index."""
    import cv2
    for i in range(6):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ret, _ = cap.read()
            cap.release()
            if ret:
                print(f'[HW] camera found at index {i}')
                return i
    print('[HW] no camera found, defaulting to 0')
    return 0


def detect_mic():
    """Return first input device index via pyaudio."""
    try:
        import pyaudio
        pa = pyaudio.PyAudio()
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info['maxInputChannels'] > 0:
                print(f"[HW] mic found: {info['name']} (index {i})")
                pa.terminate()
                return i
        pa.terminate()
    except Exception as e:
        print(f'[HW] mic detection failed: {e}')
    return None


def list_video_devices():
    """List /dev/video* devices on Linux."""
    try:
        result = subprocess.run(['v4l2-ctl', '--list-devices'],
                                capture_output=True, text=True)
        print('[HW] video devices:\n', result.stdout)
    except FileNotFoundError:
        print('[HW] v4l2-ctl not found (install v4l-utils)')


def scan_cameras() -> list:
    """Enumerate /dev/video* with their sysfs product names, no v4l2-ctl needed.

    Returns [{'path', 'index', 'name'}], sorted by index. Does NOT open the
    devices — core/capture.py owns that (it also checks the frames aren't
    black, see config.CAMERA_MIN_FRAME_MEAN), and opening a node the capture
    thread is already streaming from can knock it offline.
    """
    found = []
    for path in sorted(glob.glob('/dev/video*'),
                       key=lambda p: int(''.join(c for c in p if c.isdigit()) or 0)):
        idx = int(''.join(c for c in path if c.isdigit()) or 0)
        found.append({'path': path, 'index': idx, 'name': _v4l_name(idx)})
    return found


def _v4l_name(index: int) -> str:
    for attr in (f'/sys/class/video4linux/video{index}/name',):
        try:
            with open(attr) as fh:
                return fh.read().strip()
        except OSError:
            pass
    return 'unknown'


# ── Gamepad / controller ──────────────────────────────────────
#
# Why not just evdev.list_devices(): it silently drops every node the
# current user can't open, so on a box where $USER isn't in the `input`
# group it returns an empty list that is indistinguishable from "no
# controller plugged in". That is exactly the state this machine was in on
# 2026-08-08 — the operator would have kept plugging the pad in and seeing
# "no Xbox controller found". We glob /dev/input/event* ourselves and
# report unreadable nodes as a permissions problem, which is actionable.

_INPUT_GROUP_HINT = (
    "add the user to the 'input' group "
    "(sudo usermod -aG input $USER, then log out and back in)")


def _read_proc_input_devices() -> list:
    """Parse /proc/bus/input/devices — readable without the input group, so
    we can still name the devices we were denied an open() on."""
    entries, cur = [], {}
    try:
        with open('/proc/bus/input/devices') as fh:
            for line in fh:
                line = line.rstrip('\n')
                if not line.strip():
                    if cur:
                        entries.append(cur)
                        cur = {}
                    continue
                if line.startswith('N: Name='):
                    cur['name'] = line.split('=', 1)[1].strip('"')
                elif line.startswith('H: Handlers='):
                    cur['handlers'] = line.split('=', 1)[1].split()
                elif line.startswith('B: KEY='):
                    cur['key_bits'] = line.split('=', 1)[1].strip()
        if cur:
            entries.append(cur)
    except OSError:
        pass
    return entries


def _proc_name_for(event_node: str) -> str:
    """Map 'event7' back to its device name via /proc, permission-free."""
    for entry in _read_proc_input_devices():
        if event_node in entry.get('handlers', []):
            return entry.get('name', 'unknown')
    return 'unknown'


def _classify(dev) -> str:
    """Classify an opened evdev device as gamepad / touchpad / mouse /
    keyboard / other.

    The old heuristic in core/human_assist.py was `EV_ABS in caps and
    EV_KEY in caps`, which is also true of every I2C laptop touchpad
    (they report ABS_X/ABS_Y plus BTN_LEFT). On this machine that matched
    "ELAN07FB:00 04F3:321A Touchpad" — so as soon as the user gained
    /dev/input access, HumanAssist would have bound the touchpad as the
    controller and fed trackpad coordinates into the game as stick input.
    A real pad is identified by its buttons, not by having absolute axes.
    """
    from evdev import ecodes
    caps = dev.capabilities()
    keys = set(caps.get(ecodes.EV_KEY, []))
    abs_axes = {code for code, _ in caps.get(ecodes.EV_ABS, [])}

    # BTN_GAMEPAD == BTN_SOUTH == BTN_A (0x130) — the kernel's own marker
    # for "this is a gamepad", set by xpad/hid-sony/hid-nintendo/etc.
    gamepad_btns = {ecodes.BTN_GAMEPAD, ecodes.BTN_SOUTH, ecodes.BTN_EAST,
                    ecodes.BTN_NORTH, ecodes.BTN_WEST,
                    ecodes.BTN_TL, ecodes.BTN_TR, ecodes.BTN_START}
    touch_btns = {ecodes.BTN_TOOL_FINGER, ecodes.BTN_TOUCH,
                  ecodes.BTN_TOOL_DOUBLETAP}

    if keys & touch_btns and ecodes.BTN_GAMEPAD not in keys:
        return 'touchpad'
    if keys & gamepad_btns and abs_axes:
        return 'gamepad'
    if ecodes.BTN_LEFT in keys and ecodes.REL_X in set(caps.get(ecodes.EV_REL, [])):
        return 'mouse'
    if ecodes.KEY_A in keys:
        return 'keyboard'
    return 'other'


def scan_input_devices() -> list:
    """Enumerate every /dev/input/event* node.

    Returns [{'path', 'node', 'name', 'kind', 'accessible', 'error'}].
    Nodes we can't open still appear, named via /proc, with accessible=False
    — that distinguishes "nothing plugged in" from "no permission".
    """
    results = []
    paths = sorted(glob.glob('/dev/input/event*'),
                   key=lambda p: int(''.join(c for c in p if c.isdigit()) or 0))
    for path in paths:
        node = os.path.basename(path)
        rec = {'path': path, 'node': node, 'name': _proc_name_for(node),
               'kind': 'unknown', 'accessible': False, 'error': None}
        try:
            from evdev import InputDevice
            dev = InputDevice(path)
            rec['accessible'] = True
            rec['name'] = dev.name
            rec['kind'] = _classify(dev)
            dev.close()
        except ImportError:
            rec['error'] = 'python-evdev not installed'
        except PermissionError:
            rec['error'] = 'permission denied'
        except Exception as e:
            rec['error'] = str(e)
        results.append(rec)
    return results


def scan_joystick_nodes() -> list:
    """Legacy joydev nodes (/dev/input/js*, /dev/js*).

    AKSUMAEL reads controllers through evdev, not joydev, so these are
    reported for diagnosis only: a pad that shows up here but has no
    matching evdev gamepad node means joydev is loaded and evdev isn't
    seeing it (or we lack permission on the event node).
    """
    return sorted(glob.glob('/dev/input/js*') + glob.glob('/dev/js*'))


def find_gamepad(verbose: bool = True):
    """Return (path, name) of the best gamepad candidate, or (None, reason).

    Prefers a device whose name looks like an Xbox pad, then any device
    classified as a gamepad.
    """
    devices = scan_input_devices()
    if not devices:
        return None, 'no /dev/input/event* nodes exist'

    gamepads = [d for d in devices if d['kind'] == 'gamepad']
    if gamepads:
        def _rank(d):
            n = d['name'].lower()
            return (0 if ('xbox' in n or 'x-box' in n or 'microsoft' in n) else 1,
                    d['node'])
        best = sorted(gamepads, key=_rank)[0]
        if verbose and len(gamepads) > 1:
            others = ', '.join(f"{d['name']} ({d['node']})" for d in gamepads[1:])
            print(f'[HW] multiple gamepads present, also saw: {others}')
        return best['path'], best['name']

    denied = [d for d in devices if not d['accessible']
              and d['error'] == 'permission denied']
    if denied and len(denied) == len(devices):
        # Every node was unreadable — we genuinely cannot tell whether a
        # pad is attached, so don't report "no controller".
        return None, (f'cannot read any of {len(denied)} /dev/input/event* nodes '
                      f'(permission denied) — {_INPUT_GROUP_HINT}')
    if denied:
        return None, (f'no gamepad among {len(devices) - len(denied)} readable '
                      f'nodes; {len(denied)} more unreadable (permission denied) '
                      f'— {_INPUT_GROUP_HINT}')
    if any(d['error'] == 'python-evdev not installed' for d in devices):
        return None, 'python-evdev not installed'
    return None, f'no gamepad among {len(devices)} input devices'


# ── KB2040 / USB-serial ───────────────────────────────────────
#
# The link is one-way: host → FTDI FT232RL → KB2040 UART RX. rp2040/code.py
# never writes back (there is no uart.write() anywhere in the firmware), so
# a request/response handshake is not possible over this cable. The probe
# below is therefore a *write* probe: open at the configured baud and push a
# release-all frame (the same frame KB2040Serial._connect sends to clear
# stale HID state). A port that opens and accepts that write without an
# OSError is as far as verification can go without a firmware change.

# USB vendor IDs worth preferring when several serial ports are present.
_PREFERRED_VIDS = {
    '0403': 'FTDI',            # FT232RL USB-TTL adapter — AKSUMAEL's wiring
    '10c4': 'Silicon Labs',    # CP210x
    '1a86': 'CH340/CH9102',
    '239a': 'Adafruit',        # KB2040 plugged in directly (USB CDC)
    '2e8a': 'Raspberry Pi',    # RP2040 native USB CDC
}


def _sysfs_usb_ids(port: str) -> tuple:
    """Walk sysfs up from a tty node to its USB device, return (vid, pid, product)."""
    node = os.path.basename(port)
    dev_link = f'/sys/class/tty/{node}/device'
    try:
        path = os.path.realpath(dev_link)
    except OSError:
        return None, None, None
    for _ in range(6):        # tty -> usb interface -> usb device
        vid_f = os.path.join(path, 'idVendor')
        pid_f = os.path.join(path, 'idProduct')
        if os.path.exists(vid_f) and os.path.exists(pid_f):
            def _read(p):
                try:
                    with open(p) as fh:
                        return fh.read().strip()
                except OSError:
                    return None
            return _read(vid_f), _read(pid_f), _read(os.path.join(path, 'product'))
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return None, None, None


def scan_serial_ports() -> list:
    """Enumerate /dev/ttyUSB* and /dev/ttyACM* with USB VID/PID/product.

    Returns [{'path', 'vid', 'pid', 'product', 'vendor', 'writable'}],
    preferred-vendor ports first.
    """
    ports = []
    for path in sorted(glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')):
        vid, pid, product = _sysfs_usb_ids(path)
        ports.append({
            'path': path, 'vid': vid, 'pid': pid,
            'product': product or 'unknown',
            'vendor': _PREFERRED_VIDS.get(vid or '', None),
            'writable': os.access(path, os.W_OK),
        })
    ports.sort(key=lambda p: (0 if p['vendor'] else 1, p['path']))
    return ports


def probe_kb2040(port: str, baud: int = None, quiet: bool = True) -> bool:
    """Write-probe a serial port: open it and send a release-all frame.

    Returns True if the port opened and accepted the write. This is not a
    handshake — see the module note above; the firmware is receive-only, so
    a True here means "a serial device is there and writable", not "a
    KB2040 acknowledged us".
    """
    baud = baud or config.UART_BAUD
    ser = None
    try:
        import serial as pyserial
        from uart.kb2040_packer import pack_release_all
        ser = pyserial.Serial(port, baud, timeout=0.1, write_timeout=0.5)
        ser.write(pack_release_all())
        ser.flush()
        return True
    except Exception as e:
        if not quiet:
            print(f'[HW] probe failed on {port}: {e}')
        return False
    finally:
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass


def find_kb2040_port(preferred: str = None, verbose: bool = True):
    """Return the path of the first serial port that write-probes OK.

    Order: `preferred` (default config.UART_PORT) first so a working
    configured port is never traded for a different one, then remaining
    ports with a known USB-serial vendor ID, then everything else.
    Returns None when no port accepts a write.
    """
    preferred = preferred or config.UART_PORT
    ports = scan_serial_ports()
    if not ports:
        if verbose:
            print('[HW] no USB serial ports present (/dev/ttyUSB*, /dev/ttyACM*)')
        return None

    ordered = ([p for p in ports if p['path'] == preferred]
               + [p for p in ports if p['path'] != preferred])
    for p in ordered:
        if not p['writable']:
            if verbose:
                print(f"[HW] {p['path']} not writable by this user "
                      f"(add the user to the 'dialout' group) — skipping")
            continue
        if probe_kb2040(p['path']):
            if verbose:
                tag = f" [{p['vendor']}]" if p['vendor'] else ''
                extra = '' if p['path'] == preferred else \
                        f" (config.UART_PORT={preferred} did not respond)"
                print(f"[HW] KB2040 UART → {p['path']}{tag} "
                      f"{p['product']}{extra}")
            return p['path']
    if verbose:
        print(f"[HW] {len(ports)} serial port(s) present but none accepted a "
              f"write probe: {', '.join(p['path'] for p in ports)}")
    return None


# ── Startup report ────────────────────────────────────────────

def report(verbose: bool = True) -> dict:
    """Full hardware sweep for the boot log. Returns a summary dict.

    Called once from core/runtime.py before the executor/HumanAssist are
    constructed, so the log shows what was available before anything tries
    to bind to it.
    """
    summary = {}

    cams = scan_cameras()
    summary['cameras'] = cams
    if verbose:
        print('[HW] ── hardware inventory ──────────────────────────')
        if cams:
            for c in cams:
                mark = ' ← config.CAMERA_INDEX' if c['index'] == config.CAMERA_INDEX else ''
                print(f"[HW]   video: {c['path']}  {c['name']}{mark}")
        else:
            print('[HW]   video: none (/dev/video* empty)')

    inputs = scan_input_devices()
    summary['input_devices'] = inputs
    gp_path, gp_info = find_gamepad(verbose=verbose)
    summary['gamepad'] = gp_path
    summary['gamepad_note'] = None if gp_path else gp_info
    if verbose:
        kinds = {}
        for d in inputs:
            kinds[d['kind']] = kinds.get(d['kind'], 0) + 1
        readable = sum(1 for d in inputs if d['accessible'])
        print(f"[HW]   input: {len(inputs)} event node(s), {readable} readable "
              f"({', '.join(f'{v}×{k}' for k, v in sorted(kinds.items()))})")
        js = scan_joystick_nodes()
        if js:
            print(f"[HW]   joydev: {', '.join(js)}")
        if gp_path:
            print(f'[HW]   gamepad: {gp_info} @ {gp_path}')
        else:
            print(f'[HW]   gamepad: none — {gp_info}')

    ports = scan_serial_ports()
    summary['serial_ports'] = ports
    if verbose:
        if ports:
            for p in ports:
                tag = f" [{p['vendor']}]" if p['vendor'] else ''
                wr = '' if p['writable'] else '  (NOT WRITABLE)'
                print(f"[HW]   serial: {p['path']}{tag} {p['product']} "
                      f"vid:pid={p['vid']}:{p['pid']}{wr}")
        else:
            print('[HW]   serial: none (/dev/ttyUSB*, /dev/ttyACM* empty) '
                  '— KB2040 not attached, HID output will be print-mode')

    uart = find_kb2040_port(verbose=verbose) if ports else None
    summary['uart_port'] = uart
    if verbose:
        print('[HW] ────────────────────────────────────────────────')
    return summary


if __name__ == '__main__':
    list_video_devices()
    report()
