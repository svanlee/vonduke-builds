#!/bin/bash
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — One-Shot Installer (Linux laptop)  ║
# ║  Usage: chmod +x install.sh && ./install.sh         ║
# ╚══════════════════════════════════════════════════════╝
# Safe to re-run any time. Does NOT abort on a single failure.
# No Raspberry Pi in the current setup — the brain is a laptop
# (HP Victus, RTX 4050); UART to the KB2040 goes through an FTDI
# FT232RL USB-TTL adapter (shows up as /dev/ttyUSB0, no raspi-config /
# /boot/config.txt steps needed), and there's no I2C joystick module.

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   AKSUMAEL v1.0.0 — Installing             ║"
echo "╚══════════════════════════════════════════╝"

# ── [1/5] System packages ─────────────────────────────────────
echo ""
echo "[1/5] System packages..."
sudo apt update -q

# Install each package individually so one missing name
# (varies across distro versions) doesn't kill the rest.
PKGS="python3 python3-pip python3-opencv v4l-utils \
      portaudio19-dev libasound2-dev ffmpeg \
      espeak espeak-ng libespeak1 \
      python3-numpy"
for p in $PKGS; do
    sudo apt install -y "$p" 2>/dev/null \
        && echo "  ✓ $p" \
        || echo "  – $p (skipped, not available on this OS version)"
done

# ── [2/5] User permissions ────────────────────────────────────
echo ""
echo "[2/5] User group permissions..."
for grp in video dialout audio; do
    if getent group "$grp" >/dev/null; then
        sudo usermod -aG "$grp" "$USER" \
            && echo "  ✓ $USER added to $grp"
    fi
done
echo "  (group changes apply after logout/login; 'dialout' is what lets"
echo "   the FTDI USB-TTL adapter open /dev/ttyUSB0 without sudo)"

# ── [3/5] Python packages ─────────────────────────────────────
echo ""
echo "[3/5] Python packages..."
pip3 install -r requirements.txt --break-system-packages 2>&1 \
    | grep -E "Successfully installed|already satisfied" | tail -3
echo "  ✓ pip install finished"

# ── [4/5] Data directories ────────────────────────────────────
echo ""
echo "[4/5] Data directories..."
mkdir -p data/skills data/models
[ -f data/yolo_labels.json ] || echo '{}' > data/yolo_labels.json
echo "  ✓ data/ ready"

# ── [5/5] Self-tests ─────────────────────────────────────────
echo ""
echo "[5/5] Module self-tests..."
python3 -m uart.kb2040_packer  >/dev/null 2>&1 && echo "  ✓ KB2040 packer"  || echo "  ✗ KB2040 packer"
python3 -m uart.ch9329_packer  >/dev/null 2>&1 && echo "  ✓ CH9329 packer"  || echo "  ✗ CH9329 packer"
python3 -m audio.voice_persona >/dev/null 2>&1 && echo "  ✓ Voice persona"  || echo "  ✗ Voice persona"
python3 -m skills.skill_system >/dev/null 2>&1 && echo "  ✓ Skill system"   || echo "  ✗ Skill system"
python3 -m audio.game_ear      >/dev/null 2>&1 && echo "  ✓ Game ear"       || echo "  ✗ Game ear"

# ── Hardware presence report ──────────────────────────────────
echo ""
echo "── Hardware check ──"
ls /dev/ttyUSB* >/dev/null 2>&1 \
    && echo "  ✓ FTDI UART adapter present: $(ls /dev/ttyUSB* | tr '\n' ' ')" \
    || echo "  – No /dev/ttyUSB* (plug in the FTDI adapter → KB2040)"

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
echo "║  1. nano config.py  → LLM keys/URL        ║"
echo "║  2. Flash KB2040    → rp2040/README.md    ║"
echo "║  3. Wire hardware   → README.md           ║"
echo "║  4. python3 tools/kb2040_test.py all      ║"
echo "║  5. python3 main.py  (print mode first)   ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "VISION_PROVIDER defaults to \"local\" (mesh-llm) — set"
echo "GEMINI_API_KEY / ANTHROPIC_API_KEY for the fallback tiers."
echo ""
