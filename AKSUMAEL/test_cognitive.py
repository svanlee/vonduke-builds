#!/usr/bin/env python3
"""Smoke test: inner monologue, no hardware required."""
from core.cognitive import CognitiveArchitecture

ca = CognitiveArchitecture()

def show(tick):
    print(f"--- tick {tick} ---")
    print("  thought:   ", ca.monologue.thoughts[-1] if ca.monologue.thoughts else '(none)')

# Tick 1: calm scene — HUD only
calm = [
    {"label": "health_bar", "conf": 0.98},
    {"label": "hotbar",     "conf": 0.97},
    {"label": "crosshair",  "conf": 0.95},
]
ca.update(tick=1, objects=calm, action_dict={"action": "idle"}, reward=0.0, goal="explore")
show(1)

# Tick 2: creeper enters frame
danger = calm + [{"label": "creeper", "conf": 0.91}]
ca.update(tick=2, objects=danger, action_dict={"action": "idle"}, reward=0.0, goal="flee_danger")
show(2)

# Tick 3: creeper gone, diamond ore spotted
opportunity = calm + [{"label": "diamond_ore", "conf": 0.88}]
ca.update(tick=3, objects=opportunity, action_dict={"action": "move_forward"}, reward=0.5, goal="mine_diamonds")
show(3)
