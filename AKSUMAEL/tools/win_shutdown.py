"""Send Win+R → 'shutdown /s /t 0' → Enter to the HP via KB2040 HID."""
import serial, time

PORT  = '/dev/ttyUSB0'
BAUD  = 115200
HEADER     = bytes([0xAA, 0xBB])
TYPE_KB    = 0x01
TYPE_REL   = 0xFF
MOD_LMETA  = 0x08   # Windows key

KEY_MAP = {
    'a':0x04,'b':0x05,'c':0x06,'d':0x07,'e':0x08,'f':0x09,
    'g':0x0A,'h':0x0B,'i':0x0C,'j':0x0D,'k':0x0E,'l':0x0F,
    'm':0x10,'n':0x11,'o':0x12,'p':0x13,'q':0x14,'r':0x15,
    's':0x16,'t':0x17,'u':0x18,'v':0x19,'w':0x1A,'x':0x1B,
    'y':0x1C,'z':0x1D,
    '1':0x1E,'2':0x1F,'3':0x20,'4':0x21,'5':0x22,
    '6':0x23,'7':0x24,'8':0x25,'9':0x26,'0':0x27,
    'enter':0x28, ' ':0x2C, '/':0x38,
}

def _frame(pkt_type, data):
    body = list(HEADER) + [pkt_type, len(data)] + list(data)
    cksum = (pkt_type + len(data) + sum(data)) & 0xFF
    return bytes(body + [cksum])

def release(ser):
    ser.write(_frame(TYPE_REL, []))
    time.sleep(0.08)

def press(ser, key, mod=0):
    kc = KEY_MAP.get(key.lower(), 0)
    if not kc:
        print(f'  [!] no keycode for {repr(key)}')
        return
    ser.write(_frame(TYPE_KB, [mod, 0, kc, 0, 0, 0, 0, 0]))
    time.sleep(0.08)
    release(ser)
    time.sleep(0.05)

with serial.Serial(PORT, BAUD, timeout=1) as ser:
    time.sleep(0.3)

    # Win+R
    print('Sending Win+R ...')
    kc_r = KEY_MAP['r']
    ser.write(_frame(TYPE_KB, [MOD_LMETA, 0, kc_r, 0, 0, 0, 0, 0]))
    time.sleep(0.12)
    release(ser)
    time.sleep(1.2)   # wait for Run dialog

    # Type: shutdown /s /t 0
    cmd = 'shutdown /s /t 0'
    print(f'Typing: {cmd}')
    for ch in cmd:
        press(ser, ch)

    time.sleep(0.2)

    # Enter
    print('Pressing Enter ...')
    press(ser, 'enter')
    time.sleep(0.2)
    release(ser)

print('Done — Windows should shut down in a moment.')
