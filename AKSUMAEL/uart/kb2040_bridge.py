# ╔══════════════════════════════════════════════════════════════╗
# ║  AKSUMAEL KB2040 Bridge — CircuitPython general hardware I/O  ║
# ║                                                              ║
# ║  Replaces the HID firmware (rp2040/code.py) for non-Minecraft ║
# ║  work: the KB2040 stops pretending to be a keyboard/gamepad   ║
# ║  and becomes a dumb, synchronous GPIO/UART/I2C/SPI/ADC        ║
# ║  executor that AKSUMAEL drives over USB serial.               ║
# ║                                                              ║
# ║  Install: copy this file to CIRCUITPY as code.py.             ║
# ║           rp2040/boot.py already does the one thing this      ║
# ║           needs — usb_cdc.enable(console=True, data=True) —   ║
# ║           so it can stay as-is. Hard-reset after copying.     ║
# ║                                                              ║
# ║  Host side: uart/bridge_client.py (BridgeClient).             ║
# ╚══════════════════════════════════════════════════════════════╝
#
# ── Protocol ───────────────────────────────────────────────────
# Newline-delimited JSON over USB serial, 115200 baud (the baud is
# cosmetic on the USB CDC data endpoint — USB negotiates its own rate —
# but it matters on the busio.UART fallback below).
#
# One command line in, exactly one response line out, in order. Every
# response carries "ok" (bool) and "cmd" (echo). A command may carry an
# "id" of any JSON type; when present it is echoed back, which is what
# lets the host resynchronise after a timeout instead of reading a stale
# reply as the answer to the next question.
#
#   {"cmd":"gpio_out","pin":5,"value":1}        -> {"ok":true,"cmd":"gpio_out","pin":5,"value":1}
#   {"cmd":"gpio_in","pin":6}                    -> {"ok":true,"cmd":"gpio_in","pin":6,"value":0}
#   {"cmd":"gpio_pwm","pin":3,"duty":0.5,"freq":1000}
#   {"cmd":"uart_write","uart":1,"data":"AT\r\n"}
#   {"cmd":"uart_read","uart":1,"n":64}          -> {"ok":true,...,"n":4,"hex":"4f4b0d0a","data":"OK\r\n"}
#   {"cmd":"i2c_scan"}                           -> {"ok":true,...,"addresses":[64,104]}
#   {"cmd":"i2c_write","addr":64,"data":[0,1]}
#   {"cmd":"i2c_read","addr":64,"n":2}           -> {"ok":true,...,"data":[0,1],"hex":"0001"}
#   {"cmd":"spi_xfer","data":[255,0]}            -> {"ok":true,...,"data":[0,146],"hex":"0092"}
#   {"cmd":"analog_in","pin":"A0"}               -> {"ok":true,...,"pin":"A0","value":0.73,"raw":47841,"volts":2.41}
#   {"cmd":"ping"}                               -> {"ok":true,"pong":true,"node":"kb2040-bridge",...}
#
# Extras beyond the core set, because three weeks of bench work will want
# them: "uart_setup" (baud/pin change — AT devices are rarely 9600 when
# you need them to be), "release" (free a claimed pin/bus), "info", and
# "reset".
#
# On error: {"ok":false,"cmd":"<cmd>","error":"<type>: <message>"}. An
# error never kills the loop — a typo'd command must not take the bench
# rig down with it.
#
# ── KB2040 pin map (Adafruit "Kee Boar Driver", RP2040) ────────
# Commands accept either the silkscreen name ("D5", "A0", "SCK", "SDA")
# or the raw RP2040 GPIO number (5, 26, 18, 12). Both forms resolve to
# the same pin object; the number is authoritative, the name is an alias.
#
#   Silk   GPIO   Notes
#   ----   ----   ---------------------------------------------------
#   TX      GP0   UART0 TX   (also the busio.UART command-channel
#   RX      GP1   UART0 RX    fallback — see COMMAND CHANNEL below)
#   D2      GP2
#   D3      GP3
#   D4      GP4   UART1 TX   (default for {"uart":1})
#   D5      GP5   UART1 RX   (default for {"uart":1})
#   D6      GP6
#   D7      GP7
#   D8      GP8   UART1 TX   (alternate RP2040 mapping)
#   D9      GP9   UART1 RX   (alternate RP2040 mapping)
#   D10     GP10
#   MO     GP19   SPI0 MOSI
#   MI     GP20   SPI0 MISO
#   SCK    GP18   SPI0 SCK
#   SDA    GP12   I2C1 SDA   (STEMMA QT connector)
#   SCL    GP13   I2C1 SCL   (STEMMA QT connector)
#   A0     GP26   ADC0
#   A1     GP27   ADC1
#   A2     GP28   ADC2
#   A3     GP29   ADC3
#   —      GP17   on-board NeoPixel (not exposed; needs the neopixel lib)
#
# ADC exists on GP26-GP29 only. analog_in on any other pin is an error,
# not a silent 0.0 — a bench reading that is quietly wrong is worse than
# no reading.
#
# ── COMMAND CHANNEL ────────────────────────────────────────────
# Preferred: usb_cdc.data — the KB2040's own USB cable is the whole rig,
# no FTDI adapter, and TX/RX stay free for {"uart":0}.
#
# Fallback: if boot.py did not enable the CDC data endpoint,
# busio.UART(board.TX, board.RX) takes over as the command channel, which
# consumes UART0. In that mode {"uart":0} returns an error rather than
# fighting the host for the same pins.

import board
import busio
import json
import microcontroller
import time

import digitalio
import analogio
import pwmio

try:
    import usb_cdc
except ImportError:          # HID-less builds
    usb_cdc = None

NODE     = "kb2040-bridge"
VERSION  = "1.0.0"
BAUD     = 115200

# ADC full scale. AnalogIn.value is always 0-65535 regardless of the
# 12-bit hardware, and reference_voltage is 3.3 V on the RP2040.
ADC_MAX  = 65535

# Default pins for {"uart": n}. GP4/GP5 is one of the RP2040's legal
# UART1 mappings and both are broken out on the KB2040; uart_setup can
# move it (e.g. to GP8/GP9) at runtime.
DEFAULT_UART_PINS = {0: (0, 1), 1: (4, 5)}


# ── Command channel ────────────────────────────────────────────
def _open_command_channel():
    """Return (stream, uses_uart0). Prefers the USB CDC data endpoint."""
    if usb_cdc is not None and usb_cdc.data is not None:
        s = usb_cdc.data
        # Non-blocking reads; bounded writes so a host that opened the
        # port and then stopped reading can't wedge the loop forever.
        try:
            s.timeout = 0
            s.write_timeout = 0.5
        except (AttributeError, ValueError):
            pass
        return s, False
    return busio.UART(getattr(board, "TX", None) or microcontroller.pin.GPIO0,
                      getattr(board, "RX", None) or microcontroller.pin.GPIO1,
                      baudrate=BAUD, timeout=0), True


stream, CMD_USES_UART0 = _open_command_channel()


# ── Pin resolution ─────────────────────────────────────────────
# Everything is claimed lazily and cached, keyed by the pin object's
# repr. A pin claimed as an output stays claimed: re-requesting it in a
# different role (input, PWM, ADC) deinits the old object first, because
# CircuitPython raises ValueError("in use") rather than reassigning.
_pins = {}       # key -> (role, obj)
_uarts = {}      # num -> busio.UART
_uart_cfg = {}   # num -> {"tx","rx","baud"}
_i2c = None
_spi = None


def resolve_pin(spec):
    """Accept 5, "5", "D5", "GP5", "GPIO5", "A0", "SCK" -> microcontroller.Pin."""
    if isinstance(spec, bool):
        raise ValueError("pin must be a number or name, not a bool")
    if isinstance(spec, int):
        return getattr(microcontroller.pin, "GPIO%d" % spec)
    if not isinstance(spec, str):
        raise ValueError("bad pin spec: %r" % (spec,))
    s = spec.strip()
    if s.startswith("board."):
        s = s[6:]
    # Bare number as a string.
    try:
        return getattr(microcontroller.pin, "GPIO%d" % int(s))
    except (ValueError, AttributeError):
        pass
    up = s.upper()
    for name in (s, up, "GPIO" + up.lstrip("GP"), "D" + up.lstrip("D")):
        pin = getattr(board, name, None) or getattr(microcontroller.pin, name, None)
        if pin is not None:
            return pin
    raise ValueError("unknown pin: %s" % spec)


def _board_pin(name, gpio):
    """board.<name> if the build defines it, else the raw GPIO. Keeps the
    bus helpers working on a build whose board module spells things
    differently (or on a bare RP2040 that has no board aliases at all)."""
    pin = getattr(board, name, None)
    if pin is None:
        pin = getattr(microcontroller.pin, "GPIO%d" % gpio)
    return pin


def _pin_key(pin):
    return str(pin)


def _release_pin(pin):
    key = _pin_key(pin)
    entry = _pins.pop(key, None)
    if entry is not None:
        try:
            entry[1].deinit()
        except Exception:
            pass


def _claim(pin, role, factory):
    """Return a cached pin object in `role`, rebuilding it if the pin is
    currently claimed in some other role."""
    key = _pin_key(pin)
    entry = _pins.get(key)
    if entry is not None and entry[0] == role:
        return entry[1]
    if entry is not None:
        _release_pin(pin)
    obj = factory()
    _pins[key] = (role, obj)
    return obj


# ── Bus helpers ────────────────────────────────────────────────
def _get_i2c(freq=None):
    global _i2c
    if _i2c is None:
        kwargs = {}
        if freq:
            kwargs["frequency"] = int(freq)
        _i2c = busio.I2C(_board_pin("SCL", 13), _board_pin("SDA", 12), **kwargs)
    return _i2c


def _i2c_locked(freq=None):
    i2c = _get_i2c(freq)
    # try_lock() spins because the bus is shared with nothing else here —
    # a failure to lock means a previous command died mid-transaction.
    deadline = time.monotonic() + 1.0
    while not i2c.try_lock():
        if time.monotonic() > deadline:
            raise RuntimeError("I2C bus lock timeout")
        time.sleep(0.001)
    return i2c


def _get_spi(baudrate=1000000, polarity=0, phase=0):
    global _spi
    if _spi is None:
        _spi = busio.SPI(_board_pin("SCK", 18),
                         MOSI=_board_pin("MOSI", 19),
                         MISO=_board_pin("MISO", 20))
    deadline = time.monotonic() + 1.0
    while not _spi.try_lock():
        if time.monotonic() > deadline:
            raise RuntimeError("SPI bus lock timeout")
        time.sleep(0.001)
    _spi.configure(baudrate=int(baudrate), polarity=int(polarity),
                   phase=int(phase))
    return _spi


def _get_uart(num, baud=None, tx=None, rx=None):
    num = int(num)
    if num not in DEFAULT_UART_PINS:
        raise ValueError("uart must be 0 or 1")
    if num == 0 and CMD_USES_UART0:
        raise RuntimeError(
            "uart0 is the command channel on this boot (no usb_cdc.data) — "
            "enable the CDC data endpoint in boot.py to free it")
    cfg = _uart_cfg.get(num) or {}
    want = {
        "tx": tx if tx is not None else cfg.get("tx", DEFAULT_UART_PINS[num][0]),
        "rx": rx if rx is not None else cfg.get("rx", DEFAULT_UART_PINS[num][1]),
        "baud": int(baud) if baud else cfg.get("baud", BAUD),
    }
    existing = _uarts.get(num)
    if existing is not None and cfg == want:
        return existing
    if existing is not None:
        try:
            existing.deinit()
        except Exception:
            pass
        _uarts.pop(num, None)
    # timeout=0 keeps uart_read non-blocking: it returns whatever has
    # already landed in the RX buffer rather than stalling the command
    # loop waiting for a device that may never answer.
    u = busio.UART(resolve_pin(want["tx"]), resolve_pin(want["rx"]),
                   baudrate=want["baud"], timeout=0)
    _uarts[num] = u
    _uart_cfg[num] = want
    return u


def _as_bytes(data):
    """Command payloads arrive as a string ("AT\\r\\n") or a byte list."""
    if isinstance(data, str):
        return data.encode("utf-8")
    if isinstance(data, (list, tuple)):
        return bytes(int(b) & 0xFF for b in data)
    if isinstance(data, int):
        return bytes([data & 0xFF])
    raise ValueError("data must be a string or a list of byte values")


def _hex(buf):
    return "".join("%02x" % b for b in buf)


def _ascii(buf):
    """Printable-ASCII view of a buffer, or None. The hex field is the
    lossless one; this is a convenience for AT-command style devices."""
    for b in buf:
        if b > 0x7E or (b < 0x20 and b not in (0x09, 0x0A, 0x0D)):
            return None
    return "".join(chr(b) for b in buf)


# ── Command handlers ───────────────────────────────────────────
def cmd_ping(c):
    return {"pong": True, "node": NODE, "version": VERSION,
            "uptime_s": round(time.monotonic(), 2)}


def cmd_info(c):
    return {
        "node": NODE,
        "version": VERSION,
        "board": getattr(board, "board_id", "kb2040"),
        "cpu_temp_c": round(microcontroller.cpu.temperature, 2),
        "cmd_channel": "uart0" if CMD_USES_UART0 else "usb_cdc.data",
        "claimed_pins": sorted(_pins.keys()),
        "uarts": {str(k): v for k, v in _uart_cfg.items()},
        "uptime_s": round(time.monotonic(), 2),
    }


def cmd_gpio_out(c):
    pin = resolve_pin(c["pin"])
    io = _claim(pin, "out", lambda: _make_output(pin))
    value = bool(c.get("value", 0))
    io.value = value
    return {"pin": c["pin"], "value": int(value)}


def _make_output(pin):
    io = digitalio.DigitalInOut(pin)
    io.direction = digitalio.Direction.OUTPUT
    return io


def cmd_gpio_in(c):
    pin = resolve_pin(c["pin"])
    pull = (c.get("pull") or "").lower()
    role = "in:" + pull
    io = _claim(pin, role, lambda: _make_input(pin, pull))
    return {"pin": c["pin"], "value": int(io.value)}


def _make_input(pin, pull):
    io = digitalio.DigitalInOut(pin)
    io.direction = digitalio.Direction.INPUT
    if pull == "up":
        io.pull = digitalio.Pull.UP
    elif pull == "down":
        io.pull = digitalio.Pull.DOWN
    return io


def cmd_gpio_pwm(c):
    pin = resolve_pin(c["pin"])
    duty = float(c.get("duty", 0.0))
    duty = 0.0 if duty < 0.0 else (1.0 if duty > 1.0 else duty)
    freq = int(c.get("freq", 1000))
    if freq < 1:
        raise ValueError("freq must be >= 1")
    # variable_frequency=True so a later gpio_pwm on the same pin can
    # change freq without tearing the output down (and glitching whatever
    # is driven by it).
    pwm = _claim(pin, "pwm",
                 lambda: pwmio.PWMOut(pin, frequency=freq,
                                      duty_cycle=0, variable_frequency=True))
    if pwm.frequency != freq:
        pwm.frequency = freq
    pwm.duty_cycle = int(duty * 65535)
    return {"pin": c["pin"], "duty": round(duty, 4), "freq": freq}


def cmd_analog_in(c):
    pin = resolve_pin(c["pin"])
    adc = _claim(pin, "adc", lambda: analogio.AnalogIn(pin))
    raw = adc.value
    return {
        "pin": c["pin"],
        "value": round(raw / ADC_MAX, 4),
        "raw": raw,
        "volts": round(raw * adc.reference_voltage / ADC_MAX, 4),
    }


def cmd_uart_setup(c):
    num = int(c.get("uart", 1))
    u = _get_uart(num, baud=c.get("baud"), tx=c.get("tx"), rx=c.get("rx"))
    del u
    return {"uart": num, "config": _uart_cfg[num]}


def cmd_uart_write(c):
    num = int(c.get("uart", 1))
    u = _get_uart(num, baud=c.get("baud"))
    buf = _as_bytes(c.get("data", ""))
    written = u.write(buf)
    return {"uart": num, "written": written if written is not None else len(buf)}


def cmd_uart_read(c):
    num = int(c.get("uart", 1))
    u = _get_uart(num, baud=c.get("baud"))
    n = int(c.get("n", 64))
    buf = u.read(n) if n > 0 else None
    buf = buf or b""
    out = {"uart": num, "n": len(buf), "hex": _hex(buf)}
    text = _ascii(buf)
    if text is not None:
        out["data"] = text
    return out


def cmd_i2c_scan(c):
    i2c = _i2c_locked(c.get("freq"))
    try:
        found = list(i2c.scan())
    finally:
        i2c.unlock()
    return {"addresses": found,
            "hex": ["0x%02x" % a for a in found],
            "count": len(found)}


def cmd_i2c_write(c):
    addr = int(c["addr"])
    buf = _as_bytes(c.get("data", []))
    i2c = _i2c_locked(c.get("freq"))
    try:
        i2c.writeto(addr, buf)
    finally:
        i2c.unlock()
    return {"addr": addr, "written": len(buf)}


def cmd_i2c_read(c):
    addr = int(c["addr"])
    n = int(c.get("n", 1))
    if n < 1:
        raise ValueError("n must be >= 1")
    buf = bytearray(n)
    i2c = _i2c_locked(c.get("freq"))
    try:
        # An optional "reg"/"write" payload makes this a repeated-start
        # write-then-read, which is how most I2C sensors want to be read.
        pre = c.get("reg", c.get("write"))
        if pre is not None:
            i2c.writeto_then_readfrom(addr, _as_bytes(pre), buf)
        else:
            i2c.readfrom_into(addr, buf)
    finally:
        i2c.unlock()
    return {"addr": addr, "n": n, "data": list(buf), "hex": _hex(buf)}


def cmd_spi_xfer(c):
    out_buf = _as_bytes(c.get("data", []))
    in_buf = bytearray(len(out_buf))
    cs = None
    if c.get("cs") is not None:
        cs_pin = resolve_pin(c["cs"])
        cs = _claim(cs_pin, "out", lambda: _make_output(cs_pin))
    spi = _get_spi(baudrate=c.get("baudrate", 1000000),
                   polarity=c.get("polarity", 0),
                   phase=c.get("phase", 0))
    try:
        if cs is not None:
            cs.value = False          # active-low chip select
        spi.write_readinto(out_buf, in_buf)
    finally:
        if cs is not None:
            cs.value = True
        spi.unlock()
    return {"n": len(in_buf), "data": list(in_buf), "hex": _hex(in_buf)}


def cmd_release(c):
    """Free a claimed pin, a UART, or every claimed resource. Needed
    because a pin claimed as an output stays claimed until deinit, and a
    bench rig gets rewired far more often than it gets rebooted."""
    global _i2c, _spi
    target = c.get("pin")
    if target is not None:
        _release_pin(resolve_pin(target))
        return {"released": target}
    if c.get("uart") is not None:
        num = int(c["uart"])
        u = _uarts.pop(num, None)
        _uart_cfg.pop(num, None)
        if u is not None:
            try:
                u.deinit()
            except Exception:
                pass
        return {"released": "uart%d" % num}
    for key in list(_pins):
        try:
            _pins[key][1].deinit()
        except Exception:
            pass
        _pins.pop(key, None)
    for num in list(_uarts):
        try:
            _uarts[num].deinit()
        except Exception:
            pass
        _uarts.pop(num, None)
        _uart_cfg.pop(num, None)
    for bus in (_i2c, _spi):
        if bus is not None:
            try:
                bus.deinit()
            except Exception:
                pass
    _i2c = None
    _spi = None
    return {"released": "all"}


def cmd_reset(c):
    # Answer before rebooting, otherwise the host waits out its full
    # timeout on a board that already did what it was told.
    _respond({"ok": True, "cmd": "reset", "resetting": True}, c)
    time.sleep(0.1)
    microcontroller.reset()


HANDLERS = {
    "ping":       cmd_ping,
    "info":       cmd_info,
    "gpio_out":   cmd_gpio_out,
    "gpio_in":    cmd_gpio_in,
    "gpio_pwm":   cmd_gpio_pwm,
    "analog_in":  cmd_analog_in,
    "uart_setup": cmd_uart_setup,
    "uart_write": cmd_uart_write,
    "uart_read":  cmd_uart_read,
    "i2c_scan":   cmd_i2c_scan,
    "i2c_write":  cmd_i2c_write,
    "i2c_read":   cmd_i2c_read,
    "spi_xfer":   cmd_spi_xfer,
    "release":    cmd_release,
    "reset":      cmd_reset,
}


# ── Dispatch / IO ──────────────────────────────────────────────
def _respond(resp, cmd_obj=None):
    if cmd_obj is not None and isinstance(cmd_obj, dict) and "id" in cmd_obj:
        resp["id"] = cmd_obj["id"]
    try:
        stream.write(json.dumps(resp).encode("utf-8") + b"\n")
    except Exception:
        # Host closed the port mid-write. Dropping the reply is the only
        # option; the loop must survive it.
        pass


def handle_line(line):
    try:
        cmd_obj = json.loads(line)
    except (ValueError, TypeError):
        _respond({"ok": False, "cmd": None, "error": "ValueError: bad JSON"})
        return
    if not isinstance(cmd_obj, dict):
        _respond({"ok": False, "cmd": None,
                  "error": "ValueError: command must be a JSON object"})
        return
    name = cmd_obj.get("cmd")
    fn = HANDLERS.get(name)
    if fn is None:
        _respond({"ok": False, "cmd": name,
                  "error": "ValueError: unknown cmd %r" % (name,)}, cmd_obj)
        return
    try:
        result = fn(cmd_obj) or {}
        result["ok"] = True
        result["cmd"] = name
        _respond(result, cmd_obj)
    except KeyError as e:
        _respond({"ok": False, "cmd": name,
                  "error": "KeyError: missing field %s" % (e,)}, cmd_obj)
    except Exception as e:
        _respond({"ok": False, "cmd": name,
                  "error": "%s: %s" % (type(e).__name__, e)}, cmd_obj)


# Cap the accumulator so a host that streams bytes without ever sending a
# newline can't grow it until the board runs out of RAM.
MAX_LINE = 4096
rx = bytearray()


def _pump():
    global rx
    waiting = getattr(stream, "in_waiting", 0)
    if not waiting:
        return
    chunk = stream.read(waiting)
    if not chunk:
        return
    rx.extend(chunk)
    while True:
        idx = rx.find(b"\n")
        if idx < 0:
            break
        line = bytes(rx[:idx])
        del rx[:idx + 1]
        line = line.strip()
        if line:
            handle_line(line)
    if len(rx) > MAX_LINE:
        del rx[:]
        _respond({"ok": False, "cmd": None,
                  "error": "ValueError: line too long, buffer discarded"})


print("%s v%s ready (%s)" %
      (NODE, VERSION, "uart0" if CMD_USES_UART0 else "usb_cdc.data"))

while True:
    try:
        _pump()
    except Exception as e:
        # Last-resort guard: nothing a single command does should be able
        # to stop the bridge answering the next one.
        try:
            _respond({"ok": False, "cmd": None,
                      "error": "%s: %s" % (type(e).__name__, e)})
        except Exception:
            pass
    time.sleep(0.001)
