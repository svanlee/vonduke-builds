# Board reference — Sunton CYD 4.0" (ESP32-4827S040)

The board silkscreen reads **"4.0" LCD Display · ESP32-32E · 320x480 · Resistance
Touch"**. It is the 4.0" member of the Sunton "Cheap Yellow Display" (CYD)
family, sold as **ESP32-4827S040**. There is no official manual published for
this variant (the [CYD community repo](https://github.com/witnessmenow/ESP32-Cheap-Yellow-Display)
covers the 2.8" sibling, which shares most of the pinout), so this document is
the working reference used by this project. (Board photos live upstream at
[`rafelton/claude-usage-view-esp32/docs/photos`](https://github.com/rafelton/claude-usage-view-esp32/tree/main/docs/photos).)

## Specs

| Item | Value |
|---|---|
| MCU | ESP32-32E (ESP32-D0WD-V3), dual core 240 MHz, **no PSRAM** |
| Flash | 4 MB |
| Display | 4.0" TFT, **ST7796S**, 320x480, SPI @ 40 MHz, portrait-native |
| Touch | **XPT2046** resistive — shares the display SPI bus on this variant |
| USB | USB-C, **CH340** serial bridge (driver needed on macOS) |
| Storage | microSD slot (SPI) |
| Audio | Speaker JST connector driven by an **SC8002B** amp |
| Connectors | UART (5V/GND/TXD/RXD), BAT, SPEAKER, I2C, SPI, IO35/IO39 JSTs |
| Buttons | RESET, BOOT (GPIO 0) |

## Pinout used by this project

| Function | GPIO |
|---|---|
| TFT MOSI / MISO / SCLK | 13 / 12 / 14 |
| TFT CS / DC / RST | 15 / 2 / — (tied to EN) |
| TFT backlight | **27** (2.8" CYD uses 21 — common gotcha) |
| Touch CS / IRQ | 33 / 36 (shared SPI bus with the TFT) |
| Speaker | 26 |
| BOOT button | 0 |

## Quirks (learned the hard way)

- **Touch shares the display SPI bus.** Even if you don't use touch, drive
  GPIO 33 (touch CS) HIGH at boot so the XPT2046 never fights the TFT on MISO.
- **The audio amp boots muted.** The SC8002B stays silent until it is "kicked"
  with PWM activity on a set of GPIOs early at boot. This project doesn't use
  audio, so the amp is simply left muted (which also means no idle hiss).
- **Backlight is GPIO 27**, unlike the 2.8" board (GPIO 21). A dark-but-alive
  screen usually means the wrong BL pin.
- **Portrait-native panel.** 320x480 with `setRotation(1)` for 480x320
  landscape (TFT_eSPI build flags stay `TFT_WIDTH=320 / TFT_HEIGHT=480`).
- **Flashing**: merged `.bin` at address `0x0000`; the CH340 needs its driver
  on macOS (`/dev/cu.usbserial-*`).
- **No PSRAM** — full-screen sprites (480x320x2 bytes = 300 KB) don't fit;
  draw directly with `setTextPadding()`/partial fills to avoid flicker.

## TFT_eSPI configuration

Configured entirely via build flags (no `User_Setup.h`) — see
[`build.sh`](../build.sh):

```
-DUSER_SETUP_LOADED -DST7796_DRIVER -DTFT_WIDTH=320 -DTFT_HEIGHT=480
-DTFT_MOSI=13 -DTFT_MISO=12 -DTFT_SCLK=14 -DTFT_CS=15 -DTFT_DC=2 -DTFT_RST=-1
-DTFT_BL=27 -DTFT_BACKLIGHT_ON=HIGH -DTOUCH_CS=33
-DSPI_FREQUENCY=40000000
```

## Related resources

- [ESP32 Cheap Yellow Display community repo](https://github.com/witnessmenow/ESP32-Cheap-Yellow-Display) — 2.8" variant, same family
- [Sunton board definitions for PlatformIO](https://github.com/rzeldent/platformio-espressif32-sunton) — other CYD variants
- [TFT_eSPI](https://github.com/Bodmer/TFT_eSPI) — display driver library
