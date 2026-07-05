#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Joystick UART Test Harness         ║
# ║  I2C joystick → KB2040 → USB HID → PC/console       ║
# ║  Validates the full hardware pipeline, no Gemini    ║
# ╚══════════════════════════════════════════════════════╝
#
# Usage: python3 tools/joystick_harness.py
# Hardware: I2C joystick on Pi GPIO, KB2040 on /dev/serial0,
#           KB2040 USB-C plugged into the target PC.
#
# Open Notepad on the PC first — keypresses land wherever focus is.

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import config
from uart.kb2040_packer import (
    KB2040Serial, pack_keyboard, pack_gamepad, key_to_hid,
)

# Joystick buttons → keys:  A=W  B=S  C=Space  D=E
BUTTON_KEY_MAP = {
    0x01: 'w',
    0x02: 's',
    0x04: 'space',
    0x08: 'e',
}


def run_harness():
    print('AKSUMAEL Joystick → KB2040 Harness')
    print(f'  I2C addr: 0x{config.I2C_JOY_ADDR:02X}  bus: {config.I2C_BUS}')
    print(f'  UART:     {config.UART_PORT} @ {config.UART_BAUD}')
    print()

    # ── I2C joystick ──────────────────────────────────────────
    try:
        import smbus2
        bus = smbus2.SMBus(config.I2C_BUS)
        bus.read_byte(config.I2C_JOY_ADDR)
        print(f'[I2C] joystick found at 0x{config.I2C_JOY_ADDR:02X}')
    except ImportError:
        print('[I2C] smbus2 not installed: pip install smbus2 --break-system-packages')
        return
    except Exception as e:
        print(f'[I2C] joystick not found: {e}')
        print('      Wiring: SDA→Pin3, SCL→Pin5, VCC→Pin1 (3.3V), GND→Pin9')
        print('      Check:  i2cdetect -y 1   (should show 5a)')
        return

    # ── KB2040 UART ───────────────────────────────────────────
    ser = KB2040Serial()
    if not ser.is_connected:
        print(f'[UART] could not open {config.UART_PORT}')
        print('       Wiring: Pi GPIO14(TX)→KB2040 D0, GPIO15(RX)←D1, GND shared')
        print('       Check:  raspi-config Serial (shell OFF, UART ON)')
        print('       Check:  ls -la /dev/serial0  (should → ttyAMA0)')
        return

    print()
    print('Harness running. Open Notepad on the PC.')
    print('  Stick  → gamepad left axis')
    print('  A → W   B → S   C → Space   D → E')
    print('  Ctrl+C → stop')
    print()

    DEAD = config.I2C_DEADZONE

    def deadzone(v, centre=128):
        d = v - centre
        return d if abs(d) > DEAD else 0

    try:
        prev_btns = 0xFF
        prev_axes = (0, 0)
        while True:
            try:
                x_raw = bus.read_byte_data(config.I2C_JOY_ADDR, 0x00)
                y_raw = bus.read_byte_data(config.I2C_JOY_ADDR, 0x01)
                btns  = bus.read_byte_data(config.I2C_JOY_ADDR, 0x02)
            except Exception as e:
                print(f'\n[I2C] read error: {e}')
                time.sleep(0.5)
                continue

            gx = max(-127, min(127, deadzone(x_raw)))
            gy = max(-127, min(127, deadzone(y_raw)))

            # Gamepad packet — only on change to avoid UART spam
            if (gx, gy) != prev_axes:
                ser.send(pack_gamepad(lx=gx, ly=gy))
                prev_axes = (gx, gy)

            # Buttons → keyboard, on change only
            if btns != prev_btns:
                keys_held = []
                for mask, key_name in BUTTON_KEY_MAP.items():
                    if btns & mask:
                        hid, _ = key_to_hid(key_name)
                        if hid:
                            keys_held.append(hid)
                ser.send(pack_keyboard(keys=keys_held))
                prev_btns = btns

            btn_str = ''.join(
                n for b, n in [(1,'A'),(2,'B'),(4,'C'),(8,'D')] if btns & b
            ) or '-'
            print(f'X:{x_raw:3d} Y:{y_raw:3d} | gx:{gx:+4d} gy:{gy:+4d} | '
                  f'btn:{btn_str:<4}', end='\r')

            time.sleep(0.02)   # ~50 Hz

    except KeyboardInterrupt:
        print('\n\nReleasing all inputs...')
        ser.release_all()
        ser.close()
        print('Done. If W/S/Space/E appeared in Notepad, the pipeline is GOOD.')


if __name__ == '__main__':
    run_harness()
