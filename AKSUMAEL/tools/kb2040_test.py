#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — KB2040 Hardware Validator          ║
# ╚══════════════════════════════════════════════════════╝
#
# Usage:
#   python3 tools/kb2040_test.py kb       — type a test string
#   python3 tools/kb2040_test.py mouse    — move cursor in a square
#   python3 tools/kb2040_test.py gamepad  — sweep sticks, trigger, buttons
#   python3 tools/kb2040_test.py all      — everything in sequence

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import config
from uart.kb2040_packer import (
    KB2040Serial, pack_keyboard, pack_mouse_relative,
    pack_gamepad, pack_release_all, key_to_hid,
)


def test_kb(ser):
    print('\n── Keyboard Test ──')
    print('Focus Notepad on the PC. Typing in 3 seconds...')
    time.sleep(3)
    for ch in 'aksumael kb2040 ok':
        hid, mod = key_to_hid(ch)
        if hid:
            ser.send(pack_keyboard(keys=[hid], modifiers=mod))
            time.sleep(0.04)
            ser.send(pack_keyboard())
            time.sleep(0.04)
    hid, _ = key_to_hid('enter')
    ser.send(pack_keyboard(keys=[hid]))
    time.sleep(0.04)
    ser.send(pack_keyboard())
    print('Sent: "aksumael kb2040 ok" + Enter')
    print('→ Did it appear in Notepad?')


def test_mouse(ser):
    print('\n── Mouse Test ──')
    print('Watch the PC cursor. Moving in 3 seconds...')
    time.sleep(3)
    for dx, dy in [(4,0),(0,4),(-4,0),(0,-4)]:
        for _ in range(15):
            ser.send(pack_mouse_relative(dx=dx, dy=dy))
            time.sleep(0.015)
    print('→ Did the cursor trace a small square?')


def test_gamepad(ser):
    print('\n── Gamepad Test (4-axis / 17-button) ──')
    print('On the PC, open the raw gamepad tester / gamepad-tester.net.')
    print('Sweeping in 5 seconds...')
    time.sleep(5)

    def ramp_axis(send_fn, lo, hi, step=8, delay=0.02):
        for v in range(lo, hi + 1, step):
            send_fn(v)
            time.sleep(delay)
        for v in range(hi, lo - 1, -step):
            send_fn(v)
            time.sleep(delay)
        send_fn(0)

    print('  left stick: left/right...')
    ramp_axis(lambda v: ser.send(pack_gamepad(lx=v)), -127, 127)

    print('  left stick: up/down...')
    ramp_axis(lambda v: ser.send(pack_gamepad(ly=v)), -127, 127)

    print('  right stick: left/right...')
    ramp_axis(lambda v: ser.send(pack_gamepad(rx=v)), -127, 127)

    print('  right stick: up/down...')
    ramp_axis(lambda v: ser.send(pack_gamepad(ry=v)), -127, 127)

    print('  button walk (1-16, sequential)...')
    for i in range(16):
        ser.send(pack_gamepad(buttons=(1 << i)))
        time.sleep(0.3)
    ser.send(pack_gamepad())

    print('  button 17 (Guide) -- not sent by current packer, expect no response')
    time.sleep(0.5)

    print('→ Did the tester show 4 axes (sticks centered at rest) and buttons 1-16?')

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    ser = KB2040Serial()
    if not ser.is_connected:
        print(f'Could not open {config.UART_PORT}')
        print('Checklist:')
        print('  • KB2040 flashed with boot.py + code.py + adafruit_hid lib?')
        print('  • FTDI TX → KB2040 D0?  FTDI RX ← D1?  GND shared?')
        print('  • ls -la /dev/ttyUSB0')
        return

    cmd = sys.argv[1]
    try:
        if cmd == 'kb':
            test_kb(ser)
        elif cmd == 'mouse':
            test_mouse(ser)
        elif cmd == 'gamepad':
            test_gamepad(ser)
        elif cmd == 'all':
            test_kb(ser)
            time.sleep(1)
            test_mouse(ser)
            time.sleep(1)
            test_gamepad(ser)
            print('\n── All tests sent ──')
            print('If all three worked, the full HID chain is validated.')
            print('Next: python3 tools/joystick_harness.py')
        else:
            print(__doc__)
    finally:
        ser.send(pack_release_all())
        ser.close()


if __name__ == '__main__':
    main()
