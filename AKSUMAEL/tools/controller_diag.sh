#!/bin/bash
# Controller diagnostic — run from ttyd: bash tools/controller_diag.sh
cd ~/vonduke-builds/AKSUMAEL

echo "=== KB2040 / ttyUSB0 ==="
ls -la /dev/ttyUSB* 2>/dev/null || echo "NO ttyUSB devices"

echo ""
echo "=== evdev input devices ==="
python3 -c "
from evdev import list_devices, InputDevice
for p in list_devices():
    try:
        d = InputDevice(p)
        caps = d.capabilities()
        from evdev import ecodes
        has_abs = ecodes.EV_ABS in caps
        has_key = ecodes.EV_KEY in caps
        marker = ' <-- CONTROLLER CANDIDATE' if (has_abs and has_key) else ''
        print(f'{p}: {d.name}{marker}')
    except Exception as e:
        print(f'{p}: ERROR {e}')
" 2>/dev/null || echo "evdev not installed or failed"

echo ""
echo "=== Bot running? ==="
pgrep -f main.py && echo "YES" || echo "NO — restart with: bash run.sh"

echo ""
echo "=== Recent HumanAssist log lines ==="
journalctl -u aksumael --no-pager -n 20 2>/dev/null | grep -i "human\|controller\|evdev" || \
  echo "No systemd log. Check bot terminal for [HumanAssist] lines."

echo ""
echo "=== Instructions ==="
echo "1. Make sure Xbox controller is connected to THIS machine (robocar-hub) via USB or BT"
echo "2. Press Start on controller — bot log should show '[HumanAssist] Switched to HUMAN mode'"
echo "3. If no controller found above, connect it and run again"
