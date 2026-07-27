#!/usr/bin/env bash
# ============================================================
# Claude Usage Monitor — flash helper (Linux / macOS)
# Compiles + uploads to the LAFVIN ESP32-S3 AIoT board via arduino-cli.
#
# Usage:   ./flash.sh [port]
# Example (Linux/Axon rig): ./flash.sh /dev/ttyACM0
# Example (macOS):          ./flash.sh /dev/cu.usbmodem101
# The ESP32-S3 uses NATIVE USB (no CH340), so it enumerates as
# /dev/ttyACM* on Linux and /dev/cu.usbmodem* on macOS. If port is
# omitted, the first such device is used.
#
# If the upload can't sync, put the board in download mode:
# hold BOOT, tap RST, release BOOT, then re-run.
# ============================================================

set -euo pipefail

ARDUINO_CLI="${ARDUINO_CLI:-arduino-cli}"
FQBN="esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,CPUFreq=240,FlashFreq=80,FlashMode=qio,FlashSize=8M,PartitionScheme=default,PSRAM=disabled,DebugLevel=none"

ROOT="$(cd "$(dirname "$0")" && pwd)"
SKETCH="$ROOT/firmware/ClaudeUsageMonitor"

PORT="${1:-}"

# >>> CONFIRM THESE PINS AGAINST YOUR KIT'S User_Setup.h <<< (see build.sh)
TFT_PINS="-DTFT_MOSI=45 -DTFT_MISO=-1 -DTFT_SCLK=40 -DTFT_CS=42 -DTFT_DC=41 -DTFT_RST=39 -DTFT_BL=14 -DTFT_BACKLIGHT_ON=HIGH"
TFT_FONTS="-DLOAD_GLCD -DLOAD_FONT2 -DLOAD_FONT4 -DLOAD_FONT6 -DLOAD_FONT7 -DLOAD_FONT8 -DLOAD_GFXFF -DSMOOTH_FONT"
TFT_SPEED="-DSPI_FREQUENCY=40000000 -DSPI_READ_FREQUENCY=20000000"
TFT_DRIVER="-DST7789_DRIVER -DTFT_WIDTH=240 -DTFT_HEIGHT=320"
TFT_FLAGS="-DUSER_SETUP_LOADED $TFT_PINS $TFT_FONTS $TFT_SPEED $TFT_DRIVER"

if [[ -z "$PORT" ]]; then
  if [[ "$(uname -s)" == "Linux" ]]; then
    # ESP32-S3 native USB: /dev/ttyACM* (fall back to ttyUSB* for UART adapters).
    PORT=$(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null | head -n1 || true)
  else
    PORT=$(ls /dev/cu.usbmodem* /dev/cu.usbserial-* 2>/dev/null | head -n1 || true)
  fi
fi

if [[ -z "$PORT" ]]; then
  echo "ERROR: no serial port found and none provided." >&2
  if [[ "$(uname -s)" == "Linux" ]]; then
    echo "Plug the board into USB-C and check 'ls /dev/ttyACM* /dev/ttyUSB*'." >&2
    echo "If it appears but upload fails with permission denied, add yourself" >&2
    echo "to the dialout group:  sudo usermod -aG dialout \"\$USER\"  (re-login)." >&2
    echo "If no port ever appears, hold BOOT + tap RST to force download mode." >&2
  else
    echo "Plug the board into USB-C and check 'ls /dev/cu.usbmodem*'." >&2
    echo "If no port appears, hold BOOT + tap RST to force download mode." >&2
  fi
  exit 1
fi

echo "Port: $PORT"

"$ARDUINO_CLI" compile \
  --fqbn "$FQBN" \
  --build-property "compiler.cpp.extra_flags=$TFT_FLAGS" \
  --upload --port "$PORT" \
  "$SKETCH"
