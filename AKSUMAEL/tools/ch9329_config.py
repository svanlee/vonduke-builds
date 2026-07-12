#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — CH9329 Hardware Validator          ║
# ╚══════════════════════════════════════════════════════╝
#
# Run this FIRST when bringing up the CH9329 on hardware.
# It confirms the chip is alive, USB enumerated, and that
# keyboard + mouse actually reach the PC.
#
# Usage:
#   python3 tools/ch9329_config.py info        — handshake + status
#   python3 tools/ch9329_config.py test-kb     — type a test string
#   python3 tools/ch9329_config.py test-mouse  — move + click
#   python3 tools/ch9329_config.py test-all    — full sequence
#
# Before running test-kb / test-mouse, open Notepad (or any text
# field) on the DESKTOP PC and click into it, so output is visible.

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import config
from uart.ch9329_packer import (
    CH9329Serial, pack_keyboard, pack_mouse_relative,
    pack_mouse_absolute, pack_mouse_click_at, key_to_hid,
)


def cmd_info(ser):
    print('\n── CH9329 Status ──')
    info = ser.get_info()
    if not info:
        print('No response from chip. Checklist:')
        print('  • TX/RX crossed?  FTDI TX→CH9329 RX, FTDI RX←CH9329 TX')
        print('  • GND shared between the laptop and CH9329?')
        print('  • CH9329 VCC on 5V, not 3.3V?')
        print('  • Correct port?  (ls -la /dev/ttyUSB0)')
        return False
    print(f'  Firmware:   v{info["version"]}')
    print(f'  USB status: {"enumerated ✓" if info["usb_ready"] else "NOT enumerated ✗"}')
    print(f'  Raw reply:  {info["raw"]}')
    if not info['usb_ready']:
        print('\n  USB not enumerated — plug the CH9329 USB-A into the PC.')
    return True


def cmd_test_kb(ser):
    print('\n── Keyboard Test ──')
    print('Click into Notepad on the PC now. Typing in 3 seconds...')
    time.sleep(3)
    test_str = 'aksumael ch9329 ok'
    for ch in test_str:
        hid, mod = key_to_hid(ch)
        if hid:
            ser.send(pack_keyboard(keys=[hid], modifiers=mod))
            time.sleep(0.04)
            ser.send(pack_keyboard())  # release
            time.sleep(0.04)
    # Press Enter
    hid, _ = key_to_hid('enter')
    ser.send(pack_keyboard(keys=[hid]))
    time.sleep(0.04)
    ser.send(pack_keyboard())
    print(f'Sent: "{test_str}" + Enter')
    print('Did it appear in Notepad? If yes, keyboard works.')


def cmd_test_mouse(ser):
    print('\n── Mouse Test ──')
    print('Watch the PC cursor. Moving in 3 seconds...')
    time.sleep(3)

    # Relative movement: draw a small square
    print('Relative move (square)...')
    moves = [(40,0),(0,40),(-40,0),(0,-40)]
    for dx, dy in moves:
        for _ in range(10):
            ser.send(pack_mouse_relative(dx=dx//10, dy=dy//10))
            time.sleep(0.02)

    time.sleep(0.5)
    # Absolute move: jump to centre then corners
    print('Absolute move (centre → corners)...')
    for x, y in [(50,50),(10,10),(90,10),(90,90),(10,90),(50,50)]:
        ser.send(pack_mouse_absolute(x, y))
        time.sleep(0.4)

    print('Did the cursor move in a square then jump to corners?')
    print('If absolute jumps landed wrong, edit ABS_RANGE in ch9329_packer.py')
    print('(try 0x7FFF instead of 4096).')


def cmd_test_all(ser):
    if not cmd_info(ser):
        return
    cmd_test_kb(ser)
    time.sleep(1)
    cmd_test_mouse(ser)
    print('\n── Validation complete ──')
    print('If keyboard typed and mouse moved, the PC HID path is GOOD.')
    print('Gamepad/console path is separate — CH9329 has no native gamepad.')


USAGE = """
CH9329 Hardware Validator

  python3 tools/ch9329_config.py info        chip handshake + USB status
  python3 tools/ch9329_config.py test-kb     type a test string
  python3 tools/ch9329_config.py test-mouse  move cursor + corners
  python3 tools/ch9329_config.py test-all    full validation

Open Notepad on the PC and click into it before test-kb / test-all.
"""

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(0)

    ser = CH9329Serial()
    if not ser.is_connected:
        print(f'Could not open {config.UART_PORT}.')
        print('Check UART is enabled and the port in config.py is correct.')
        sys.exit(1)

    cmd = sys.argv[1]
    try:
        if cmd == 'info':
            cmd_info(ser)
        elif cmd == 'test-kb':
            cmd_test_kb(ser)
        elif cmd == 'test-mouse':
            cmd_test_mouse(ser)
        elif cmd == 'test-all':
            cmd_test_all(ser)
        else:
            print(USAGE)
    finally:
        ser.close()
