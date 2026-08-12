#!/bin/bash
# Install gesture control dependencies on robocar-hub (Victus)
set -e
echo "[gesture] Installing mediapipe..."
pip install mediapipe --break-system-packages

echo "[gesture] Installing xdotool (for Jarvis keystroke tool)..."
sudo apt-get install -y xdotool scrot

echo "[gesture] Done. Test with:"
echo "  python3 gesture/run_gesture.py --display --no-bot"
