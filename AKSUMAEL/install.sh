#!/bin/bash
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — One-Shot Installer (Raspberry Pi)  ║
# ║  Usage: chmod +x install.sh && ./install.sh         ║
# ╚══════════════════════════════════════════════════════╝
# Safe to re-run any time. Does NOT abort on a single failure.

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   AKSUMAEL v1.0.0 — Installing             ║"
echo "╚══════════════════════════════════════════╝"

# ── [1/7] System packages ─────────────────────────────────────
echo ""
echo "[1/7] System packages..."
sudo apt update -q

# Install each package individually so one missing name
# (varies across Pi OS versions) doesn't kill the rest.
PKGS="python3 python3-pip python3-opencv v4l-utils i2c-tools \
      portaudio19-dev libasound2-dev ffmpeg \
      espeak espeak-ng libespeak1 \
      python3-numpy"
for p in $PKGS; do
    sudo apt install -y "$p" 2>/dev/null \
        && echo "  ✓ $p" \
        || echo "  – $p (skipped, not available on this OS version)"
done

# ── [2/7] Enable I2C + UART automatically ─────────────────────
echo ""
echo "[2/7] Enabling interfaces (raspi-config nonint)..."
if command -v raspi-config >/dev/null; then
    sudo raspi-config nonint do_i2c 0 \
        && echo "  ✓ I2C enabled" || echo "  ✗ I2C enable failed"
    # do_serial_cons 1 = console OFF, do_serial_hw 0 = UART ON
    sudo raspi-config nonint do_serial_cons 1 2>/dev/null
    sudo raspi-config nonint do_serial_hw 0 2>/dev/null \
        && echo "  ✓ UART enabled, serial console disabled" \
        || echo "  – UART: set manually via raspi-config → Interface → Serial"
else
    echo "  – raspi-config not found (not a Pi?) — enable I2C/UART manually"
fi

# ── [3/7] Fix the ttyS0 / ttyAMA0 swap if needed ──────────────
echo ""
echo "[3/7] Checking UART mapping..."
BOOTCFG="/boot/firmware/config.txt"
[ -f "$BOOTCFG" ] || BOOTCFG="/boot/config.txt"

if [ -e /dev/serial0 ]; then
    TARGET=$(readlink /dev/serial0)
    if [ "$TARGET" = "ttyS0" ]; then
        echo "  serial0 → ttyS0 (mini-UART, unreliable) — fixing..."
        if ! grep -q "dtoverlay=disable-bt" "$BOOTCFG"; then
            echo "dtoverlay=disable-bt" | sudo tee -a "$BOOTCFG" >/dev/null
            echo "  ✓ added dtoverlay=disable-bt — REBOOT REQUIRED"
            NEED_REBOOT=1
        fi
    else
        echo "  ✓ serial0 → $TARGET (good)"
    fi
else
    echo "  – /dev/serial0 missing — will appear after reboot"
    NEED_REBOOT=1
fi

# ── [4/7] User permissions ────────────────────────────────────
echo ""
echo "[4/7] User group permissions..."
for grp in video dialout i2c gpio audio; do
    if getent group "$grp" >/dev/null; then
        sudo usermod -aG "$grp" "$USER" \
            && echo "  ✓ $USER added to $grp"
    fi
done
echo "  (group changes apply after logout/login)"

# ── [5/7] Python packages ─────────────────────────────────────
echo ""
echo "[5/7] Python packages (this can take a while on a Pi)..."
pip3 install -r requirements.txt --break-system-packages 2>&1 \
    | grep -E "Successfully installed|already satisfied" | tail -3
echo "  ✓ pip install finished"

# ── [6/7] Data directories ────────────────────────────────────
echo ""
echo "[6/7] Data directories..."
mkdir -p data/skills data/models
[ -f data/yolo_labels.json ] || echo '{}' > data/yolo_labels.json
echo "  ✓ data/ ready"

# ── [7/7] Self-tests ─────────────────────────────────────────
echo ""
echo "[7/7] Module self-tests..."
python3 -m uart.kb2040_packer  >/dev/null 2>&1 && echo "  ✓ KB2040 packer"  || echo "  ✗ KB2040 packer"
python3 -m uart.ch9329_packer  >/dev/null 2>&1 && echo "  ✓ CH9329 packer"  || echo "  ✗ CH9329 packer"
python3 -m audio.voice_persona >/dev/null 2>&1 && echo "  ✓ Voice persona"  || echo "  ✗ Voice persona"
python3 -m skills.skill_system >/dev/null 2>&1 && echo "  ✓ Skill system"   || echo "  ✗ Skill system"
python3 -m audio.game_ear      >/dev/null 2>&1 && echo "  ✓ Game ear"       || echo "  ✗ Game ear"

# ── Hardware presence report ──────────────────────────────────
echo ""
echo "── Hardware check ──"
if [ -e /dev/i2c-1 ]; then
    i2cdetect -y 1 2>/dev/null | grep -qi "5a" \
        && echo "  ✓ I2C joystick found at 0x5A" \
        || echo "  – I2C joystick not detected (wire it, or ignore if not connected yet)"
else
    echo "  – I2C bus not active yet (reboot first)"
fi

ls /dev/video* >/dev/null 2>&1 \
    && echo "  ✓ Video device(s): $(ls /dev/video* | tr '\n' ' ')" \
    || echo "  – No video device (plug in capture card)"

arecord -l 2>/dev/null | grep -qi "usb" \
    && echo "  ✓ USB audio device present (check: arecord -l)" \
    || echo "  – No USB audio (capture card audio appears when plugged in)"

# ── Done ──────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Install complete                        ║"
echo "╠══════════════════════════════════════════╣"
echo "║  1. nano config.py  → add Gemini key      ║"
echo "║  2. Flash KB2040    → rp2040/README.md    ║"
echo "║  3. Wire hardware   → README.md           ║"
echo "║  4. python3 tools/kb2040_test.py all      ║"
echo "║  5. python3 tools/joystick_harness.py     ║"
echo "║  6. python3 main.py  (print mode first)   ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Free Gemini key: https://aistudio.google.com/app/apikey"

if [ "$NEED_REBOOT" = "1" ]; then
    echo ""
    echo "⚠⚠⚠  REBOOT REQUIRED before hardware will work:  sudo reboot"
fi
echo ""
