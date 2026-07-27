# Credits

The ESP32 firmware (`firmware/ClaudeUsageMonitor/`), the `build.sh`/`flash.sh`
wrappers, `docs/HARDWARE.md`, and the original macOS bridge
(`bridge/claude_usage_bridge.macos.py`) are the work of Rafael, from
[`rafelton/claude-usage-view-esp32`](https://github.com/rafelton/claude-usage-view-esp32)
(MIT).

Vendored here as the Axon rig deployment of that project — the Axon rig being
the LAFVIN ESP32-S3 AIoT kit (ST7789 240×320), not upstream's Sunton CYD 4.0"
(ESP32-WROOM + ST7796 480×320). Changes made in this directory:

**Host bridge (macOS → Linux):**

- `bridge/claude_usage_bridge.py` — Linux port of the bridge. Reads the Claude
  Code OAuth token from `~/.claude/.credentials.json` (with a `secret-tool`
  fallback) instead of the macOS Keychain. Otherwise byte-for-byte the same
  usage-fetch / simplify / serve logic.
- `bridge/claude-usage-bridge.service` + `bridge/install.sh` — systemd `--user`
  service replacing the launchd plist, matching AKSUMAEL's `axon.service`
  convention on the host.

**ESP32 firmware (CYD 4.0" → LAFVIN ESP32-S3):**

- `firmware/.../config.h` — board profile switched to LAFVIN S3 / ST7789,
  240×320 → landscape 320×240; touch-CS removed (panel has no touch).
- `firmware/.../display.cpp` — layout reflowed for the shorter 240px panel
  (header + two 88px cards + strip; font 6 percents instead of font 8; splash
  title in full-ASCII font 4).
- `firmware/.../ClaudeUsageMonitor.ino` — touch-CS init guarded behind
  `#ifdef TOUCH_CS_PIN`.
- `build.sh` / `flash.sh` — ESP32-S3 FQBN, ST7789 driver flags, native-USB
  (`/dev/ttyACM*`) port autodetect, corrected sketch path (`firmware/…`), and a
  clearly-flagged TFT pin block to confirm against the kit's User_Setup.h.
- `firmware/.../wifi_setup.cpp`, `config_local.h.example` — bridge URL / portal
  copy point at the Axon rig instead of a Mac.
- `docs/HARDWARE.md` — rewritten for the LAFVIN board.

Note: the LAFVIN board's exact ST7789 GPIO pins are board-specific and were not
authoritatively available when this was written; the pins in build.sh/flash.sh
are a documented starting point to confirm against the kit's own User_Setup.h.
