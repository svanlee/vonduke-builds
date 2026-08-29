"""
domains.robotics — Robotics domain for AKSUMAEL / Jarvis

Loaded by main.py when RoboCar hardware or AgenticROS is detected.
Provides ROS2 integration, sensor fusion, navigation behaviors,
and real-world YOLO (person/object detection, not game sprites).

Core Jarvis modules (core/, jarvis/, audio/tts, hardware/) are NOT imported
here — the robotics domain plugs into Jarvis, not the other way around.
"""
