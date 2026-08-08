# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — KB2040 Bridge Client (host side)          ║
# ║  Talks to uart/kb2040_bridge.py running on the board  ║
# ╚══════════════════════════════════════════════════════╝
#
# The counterpart to uart/kb2040_packer.py's KB2040Serial, for the weeks
# the KB2040 is a hardware bridge instead of a HID device. Same shape —
# lazy connect, thread-safe, rate-limited reconnect — but the link is now
# bidirectional, so this can do something kb2040_packer.py never could:
# actually verify the board is a KB2040 by asking it.
#
# Protocol: newline-delimited JSON, one command in / one response out.
# See uart/kb2040_bridge.py for the command set and the pin map.
#
#   from uart.bridge_client import BridgeClient
#   b = BridgeClient()
#   b.ping()                      # True
#   b.gpio_out(5, 1)
#   b.gpio_in(6)                  # 0
#   b.analog_in('A0')             # 0.7312
#   b.i2c_scan()                  # [0x40, 0x68]
#   b.uart_write(1, 'AT\r\n'); b.uart_read(1)   # b'OK\r\n'
#
# Errors: send() never raises — it returns the board's reply, or an
# {'error': ...} dict when the transport failed, so polling loops can stay
# simple. The typed helpers below DO raise BridgeError, because
# gpio_in() -> int has no sane way to signal "I don't know".

import json
import os
import threading
import time

import config

# USB vendor IDs that could plausibly be the board. Mirrors
# core/hardware_detector._PREFERRED_VIDS, but as ints, because pyserial's
# list_ports reports vid/pid as ints. Detection never trusts the VID
# alone — every candidate has to answer a ping (see _detect).
KNOWN_VIDS = {
    0x239A: 'Adafruit',        # KB2040 over its own USB CDC — the normal case
    0x2E8A: 'Raspberry Pi',    # RP2040 native USB CDC
    0x0403: 'FTDI',            # FT232RL USB-TTL, for the busio.UART fallback
    0x10C4: 'Silicon Labs',
    0x1A86: 'CH340/CH9102',
}

DEFAULT_TIMEOUT = 2.0


class BridgeError(RuntimeError):
    """The board answered with an error, or didn't answer at all."""


class BridgeClient:
    RECONNECT_INTERVAL = 5.0     # seconds between reconnect attempts

    def __init__(self, port=None, baud=115200, timeout=DEFAULT_TIMEOUT,
                 autodetect=True, verbose=True):
        self.port      = port or getattr(config, 'BRIDGE_PORT', None)
        self.baud      = baud
        self.timeout   = timeout
        self.autodetect = autodetect and port is None
        self.verbose   = verbose
        self._ser      = None
        self._lock     = threading.RLock()
        self._seq      = 0
        self._last_reconnect_attempt = 0.0
        self._connect()

    # ── Logging ───────────────────────────────────────────────
    def _log(self, msg, force=False):
        if self.verbose or force:
            print(f'[BRIDGE] {msg}')

    # ── Connection ────────────────────────────────────────────
    def _candidate_ports(self):
        """Serial ports worth trying, best-guess first.

        Ordering matters more here than for the HID packer: with
        usb_cdc.enable(console=True, data=True) a single KB2040 presents
        TWO ACM nodes — the REPL console and the data endpoint the bridge
        actually listens on. The data endpoint is the higher-numbered node
        of the pair, so among ports sharing a USB serial number we try the
        later one first. Nothing is assumed from that ordering, though: a
        wrong guess just fails its ping and we move on.
        """
        try:
            from serial.tools import list_ports
        except ImportError:
            return self._candidate_ports_sysfs()

        ports = []
        for p in list_ports.comports():
            # /dev/ttyS* are motherboard UARTs with no USB parent — they
            # open fine and swallow writes forever, which would make the
            # probe hang on every boot.
            if not ('ttyACM' in p.device or 'ttyUSB' in p.device):
                continue
            ports.append({
                'path': p.device,
                'vid': p.vid,
                'serial': p.serial_number,
                'product': p.product or 'unknown',
                'vendor': KNOWN_VIDS.get(p.vid or 0),
            })
        # Known vendor first, then the later node of a multi-node device,
        # then path order.
        ports.sort(key=lambda d: (0 if d['vendor'] else 1, d['path']))
        by_serial = {}
        for d in ports:
            by_serial.setdefault(d['serial'] or d['path'], []).append(d)
        ordered = []
        for group in by_serial.values():
            ordered.extend(sorted(group, key=lambda d: d['path'], reverse=True))
        ordered.sort(key=lambda d: 0 if d['vendor'] else 1)
        return ordered

    def _candidate_ports_sysfs(self):
        """pyserial-free fallback — reuses the sysfs walker the HID path
        already relies on (core/hardware_detector.scan_serial_ports)."""
        try:
            from core.hardware_detector import scan_serial_ports
        except Exception:
            return []
        out = []
        for p in scan_serial_ports():
            try:
                vid = int(p.get('vid') or '0', 16)
            except ValueError:
                vid = 0
            out.append({'path': p['path'], 'vid': vid, 'serial': None,
                        'product': p.get('product', 'unknown'),
                        'vendor': KNOWN_VIDS.get(vid)})
        out.sort(key=lambda d: (0 if d['vendor'] else 1, d['path']))
        return list(reversed(out)) if len(out) > 1 else out

    def _open(self, path):
        import serial as pyserial
        ser = pyserial.Serial(path, self.baud, timeout=0.05,
                              write_timeout=1.0)
        # CircuitPython's USB CDC needs a moment after DTR asserts before
        # it will see anything written to it.
        time.sleep(0.3)
        ser.reset_input_buffer()
        return ser

    def _connect(self, quiet=False):
        self._last_reconnect_attempt = time.time()
        try:
            import serial  # noqa: F401  (presence check)
        except ImportError:
            if not quiet:
                self._log('pyserial not installed — bridge unavailable')
            self._ser = None
            return

        tried = []

        # An explicitly configured port is used as-is, no ping gate: if
        # Scott named a port, honour it even if the firmware is mid-flash.
        if self.port and os.path.exists(self.port):
            try:
                self._ser = self._open(self.port)
                if not quiet:
                    self._log(f'connected on {self.port} @ {self.baud}')
                return
            except Exception as e:
                tried.append(f'{self.port} ({e})')
                self._ser = None

        if not self.autodetect:
            if not quiet and tried:
                self._log(f'open failed: {tried[0]}')
            return

        for cand in self._candidate_ports():
            path = cand['path']
            if path == self.port:
                continue          # already tried above
            ser = None
            try:
                ser = self._open(path)
                self._ser = ser
                if self._probe():
                    self.port = path
                    if not quiet:
                        tag = f" [{cand['vendor']}]" if cand['vendor'] else ''
                        self._log(f'{path}{tag} {cand["product"]} → '
                                  f'kb2040-bridge responding')
                    return
                tried.append(f'{path} (no pong)')
            except Exception as e:
                tried.append(f'{path} ({e})')
            self._ser = None
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass

        if not quiet:
            self._log('no bridge found' +
                      (f' — tried: {", ".join(tried)}' if tried else
                       ' (no USB serial ports present)'))

    def _probe(self) -> bool:
        """Handshake on an already-open port. Short timeout: this runs
        once per candidate, and one of the candidates is usually the REPL
        console endpoint, which will never answer."""
        r = self.send({'cmd': 'ping'}, timeout=0.6)
        return bool(r.get('pong'))

    def try_reconnect(self) -> bool:
        """Rate-limited reconnect. True if connected (already or freshly)."""
        with self._lock:
            if self.is_connected:
                return True
            if time.time() - self._last_reconnect_attempt < self.RECONNECT_INTERVAL:
                return False
            self._connect(quiet=True)
            if self.is_connected:
                self._log(f'reconnected on {self.port}', force=True)
                return True
            return False

    @property
    def is_connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def close(self):
        with self._lock:
            if self._ser:
                try:
                    self._ser.close()
                except Exception:
                    pass
            self._ser = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── Transport ─────────────────────────────────────────────
    def send(self, cmd: dict, timeout: float = None) -> dict:
        """Send one command, block for its reply. Never raises.

        Returns the board's response dict, or {'error': ...} on timeout /
        transport failure. Replies are matched by the 'id' this stamps on
        every command, so a response that arrives after its own timeout is
        discarded on the next call rather than being handed back as the
        answer to a different question.
        """
        timeout = self.timeout if timeout is None else timeout
        with self._lock:
            if not self.is_connected and not self.try_reconnect():
                return {'ok': False, 'error': 'bridge not connected'}

            self._seq += 1
            msg_id = self._seq
            payload = dict(cmd)
            payload['id'] = msg_id
            line = (json.dumps(payload) + '\n').encode('utf-8')

            try:
                self._ser.reset_input_buffer()
                self._ser.write(line)
                self._ser.flush()
            except Exception as e:
                self._drop(e)
                return {'ok': False, 'error': f'write failed: {e}'}

            deadline = time.time() + timeout
            buf = b''
            while time.time() < deadline:
                try:
                    chunk = self._ser.read(256)
                except Exception as e:
                    self._drop(e)
                    return {'ok': False, 'error': f'read failed: {e}'}
                if not chunk:
                    continue
                buf += chunk
                while b'\n' in buf:
                    raw, buf = buf.split(b'\n', 1)
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        resp = json.loads(raw.decode('utf-8', 'replace'))
                    except ValueError:
                        continue   # REPL noise / partial line — keep reading
                    if not isinstance(resp, dict):
                        continue
                    if resp.get('id') is not None and resp.get('id') != msg_id:
                        continue   # stale reply from a timed-out command
                    return resp
            return {'ok': False,
                    'error': f'timeout after {timeout}s waiting for '
                             f'{cmd.get("cmd")}'}

    def _drop(self, err):
        """Cable yanked / board reset — release the handle so the next
        send() reconnects instead of writing into a dead fd."""
        self._log(f'link lost: {err}', force=True)
        try:
            if self._ser:
                self._ser.close()
        except Exception:
            pass
        self._ser = None

    def _require(self, cmd: dict, *fields):
        r = self.send(cmd)
        if not r.get('ok') or r.get('error'):
            raise BridgeError(r.get('error') or f'{cmd.get("cmd")} failed: {r}')
        for f in fields:
            if f not in r:
                raise BridgeError(f'{cmd.get("cmd")}: response missing {f!r}')
        return r

    # ── Typed helpers ─────────────────────────────────────────
    def ping(self) -> bool:
        return bool(self.send({'cmd': 'ping'}).get('pong'))

    def info(self) -> dict:
        return self._require({'cmd': 'info'})

    def gpio_out(self, pin, value):
        self._require({'cmd': 'gpio_out', 'pin': pin,
                       'value': int(bool(value))})

    def gpio_in(self, pin, pull=None) -> int:
        cmd = {'cmd': 'gpio_in', 'pin': pin}
        if pull:
            cmd['pull'] = pull
        return int(self._require(cmd, 'value')['value'])

    def gpio_pwm(self, pin, duty, freq=1000):
        self._require({'cmd': 'gpio_pwm', 'pin': pin,
                       'duty': float(duty), 'freq': int(freq)})

    def analog_in(self, pin) -> float:
        return float(self._require({'cmd': 'analog_in', 'pin': pin},
                                   'value')['value'])

    def uart_write(self, uart_num, data: str):
        self._require({'cmd': 'uart_write', 'uart': int(uart_num),
                       'data': data})

    def uart_read(self, uart_num, n=64) -> bytes:
        # hex is the lossless field — the board only fills 'data' when the
        # buffer happens to be printable ASCII.
        r = self._require({'cmd': 'uart_read', 'uart': int(uart_num),
                           'n': int(n)}, 'hex')
        return bytes.fromhex(r['hex'])

    def uart_setup(self, uart_num, baud=None, tx=None, rx=None) -> dict:
        cmd = {'cmd': 'uart_setup', 'uart': int(uart_num)}
        if baud is not None: cmd['baud'] = int(baud)
        if tx is not None:   cmd['tx'] = tx
        if rx is not None:   cmd['rx'] = rx
        return self._require(cmd, 'config')['config']

    def i2c_scan(self) -> list:
        return list(self._require({'cmd': 'i2c_scan'}, 'addresses')['addresses'])

    def i2c_write(self, addr, data):
        self._require({'cmd': 'i2c_write', 'addr': int(addr),
                       'data': list(data)})

    def i2c_read(self, addr, n=1, reg=None) -> bytes:
        cmd = {'cmd': 'i2c_read', 'addr': int(addr), 'n': int(n)}
        if reg is not None:
            cmd['reg'] = [reg] if isinstance(reg, int) else list(reg)
        return bytes(self._require(cmd, 'data')['data'])

    def spi_xfer(self, data, cs=None, baudrate=1000000) -> bytes:
        cmd = {'cmd': 'spi_xfer', 'data': list(data),
               'baudrate': int(baudrate)}
        if cs is not None:
            cmd['cs'] = cs
        return bytes(self._require(cmd, 'data')['data'])

    def release(self, pin=None, uart=None):
        """Free a pin, a UART, or (with no arguments) everything."""
        cmd = {'cmd': 'release'}
        if pin is not None:
            cmd['pin'] = pin
        if uart is not None:
            cmd['uart'] = int(uart)
        self._require(cmd)

    def release_all(self):
        """Named to match KB2040Serial.release_all() so ActionExecutor can
        call it without caring which driver it holds."""
        try:
            self.release()
        except BridgeError:
            pass

    def reset_device(self):
        self.send({'cmd': 'reset'}, timeout=0.5)
        self.close()


# ── CLI self-test ─────────────────────────────────────────────
# python3 -m uart.bridge_client            → ping + info + i2c scan
# python3 -m uart.bridge_client gpio_out 5 1
# python3 -m uart.bridge_client analog_in A0
if __name__ == '__main__':
    import sys

    argv = sys.argv[1:]
    b = BridgeClient()
    if not b.is_connected:
        print('no bridge found — is kb2040_bridge.py running as code.py?')
        raise SystemExit(1)

    try:
        if not argv:
            print('ping     :', b.ping())
            info = b.info()
            print('node     :', info.get('node'), 'v' + str(info.get('version')))
            print('channel  :', info.get('cmd_channel'))
            print('cpu temp :', info.get('cpu_temp_c'), 'C')
            print('i2c scan :', [hex(a) for a in b.i2c_scan()])
        else:
            name, rest = argv[0], argv[1:]
            def _arg(v):
                try:
                    return int(v, 0)
                except ValueError:
                    return v
            cmd = {'cmd': name}
            if name in ('gpio_out',):
                cmd.update(pin=_arg(rest[0]), value=_arg(rest[1]))
            elif name in ('gpio_in', 'analog_in'):
                cmd.update(pin=_arg(rest[0]))
            elif name == 'gpio_pwm':
                cmd.update(pin=_arg(rest[0]), duty=float(rest[1]),
                           freq=_arg(rest[2]) if len(rest) > 2 else 1000)
            elif name == 'uart_write':
                cmd.update(uart=_arg(rest[0]), data=rest[1])
            elif name == 'uart_read':
                cmd.update(uart=_arg(rest[0]),
                           n=_arg(rest[1]) if len(rest) > 1 else 64)
            elif name == 'i2c_read':
                cmd.update(addr=_arg(rest[0]),
                           n=_arg(rest[1]) if len(rest) > 1 else 1)
            print(json.dumps(b.send(cmd), indent=2))
    finally:
        b.close()
