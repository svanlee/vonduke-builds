"""
domains.gaming — Gaming domain for AKSUMAEL / Jarvis

Loaded by main.py when a capture card or game environment is detected.
Provides game-specific behaviors, vision (screen reading, YOLO game classes),
memory (inventory, world model, chest tracking), and HID control.

Core Jarvis modules (core/, jarvis/, audio/tts, hardware/) are NOT imported
here — the gaming domain plugs into Jarvis, not the other way around.
"""
