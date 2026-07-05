# AKSUMAEL KB2040 Firmware

## What it does
Receives AKSUMAEL UART packets from the Pi and outputs them as USB HID
keyboard + mouse + gamepad to the host PC or console (via Brook XB3).

## Flash instructions

### 1. Install CircuitPython
- Download the KB2040 `.uf2` from circuitpython.org/board/adafruit_kb2040
- Hold BOOT button on KB2040, plug USB-C into your laptop, release BOOT
- Drive `RPI-RP2` appears — drag the `.uf2` onto it
- It reboots and `CIRCUITPY` drive appears

### 2. Install adafruit_hid
- Download CircuitPython library bundle from circuitpython.org/libraries
- Match the bundle version to your CircuitPython version
- Copy the `adafruit_hid/` folder into `CIRCUITPY/lib/`

### 3. Flash AKSUMAEL firmware
- Copy `boot.py` → `CIRCUITPY/boot.py`
- Copy `code.py` → `CIRCUITPY/code.py`
- KB2040 reboots, USB drive disappears (normal — boot.py hides it)
- KB2040 now appears to the PC as keyboard + mouse + gamepad

### 4. Standalone test (before wiring to Pi)
Open a text editor on the PC. Run this on a second machine connected
to the KB2040's serial port to verify HID output:

```python
import serial, time
# Windows: COMx  Linux/Mac: /dev/ttyACM0
s = serial.Serial('/dev/ttyACM0', 115200)

def pkt(type_, data):
    body = bytes([0xAA, 0xBB, type_, len(data)]) + bytes(data)
    return body + bytes([(type_ + len(data) + sum(data)) & 0xFF])

# Press W key (HID keycode 0x1A)
s.write(pkt(0x01, [0x00, 0x00, 0x1A, 0x00, 0x00, 0x00, 0x00, 0x00]))
time.sleep(0.1)
# Release all
s.write(pkt(0xFF, []))
s.close()
```

### Re-flashing
If you need to update the firmware after boot.py hid the drive:
1. Unplug KB2040
2. Hold BOOT button
3. Plug in — `RPI-RP2` appears again
4. Drag new `.uf2` or copy new `code.py`

## Wiring (Pi → KB2040)
```
Pi Pin 8  (GPIO14 TX) → KB2040 D0  (RX)
Pi Pin 10 (GPIO15 RX) ← KB2040 D1  (TX)
Pi Pin 6  (GND)       ── KB2040 GND
KB2040 USB-C          → PC or Brook XB3
```
