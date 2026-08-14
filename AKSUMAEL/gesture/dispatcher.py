"""
gesture/dispatcher.py — Routes GestureCommands to AKSUMAEL goals or
remote nodes (RoboCar, hive ESP32s) over UDP.

Command mapping:
  STOP        → inject_goal("stop") + UDP "STOP"
  FORWARD     → inject_goal("explore") + UDP "FORWARD"
  HOLD        → clear_goals() + UDP "HOLD"
  TURN_LEFT   → UDP "TURN_LEFT" (no AKSUMAEL goal — drive command)
  TURN_RIGHT  → UDP "TURN_RIGHT"

UDP packet format: plain ASCII string (e.g. "STOP\\n"), no framing.
Targets: list of (host, port) tuples. Default includes RoboCar.

Usage:
    from gesture.recognizer import GestureRecognizer, GestureCommand
    from gesture.dispatcher import GestureDispatcher

    rec = GestureRecognizer(camera_index=0)
    dis = GestureDispatcher(targets=[("192.168.0.202", 7700)])

    for result in rec.stream():
        dis.dispatch(result.command)
"""

from __future__ import annotations

import json
import pathlib
import socket
import time
from typing import List, Tuple

from gesture.recognizer import GestureCommand

BASE_DIR = pathlib.Path(__file__).parent.parent

# Default UDP targets. Override in constructor.
# RoboCar listens at 192.168.0.202:7700 by default.
DEFAULT_TARGETS: List[Tuple[str, int]] = [
    ("192.168.0.104", 7700),  # AK-01 RoboCar (Pi 4 queen node)
]

# AKSUMAEL goal map — commands that translate to bot goals
GOAL_MAP = {
    GestureCommand.STOP:    ("stop", 9),          # high priority — interrupt
    GestureCommand.FORWARD: ("explore", 5),
    GestureCommand.HOLD:    None,                 # clear goals instead
}


class GestureDispatcher:
    """
    Routes gesture commands to AKSUMAEL (via injected_goals.json) and/or
    remote nodes (via UDP broadcast).

    Set aksumael=True to inject goals into the running bot.
    Set targets=[] to disable UDP (local only).
    """

    def __init__(
        self,
        targets: List[Tuple[str, int]] = None,
        aksumael: bool = True,
        verbose: bool = True,
    ):
        self._targets = targets if targets is not None else DEFAULT_TARGETS
        self._aksumael = aksumael
        self._verbose = verbose
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._last_dispatch = GestureCommand.NONE
        self._last_ts = 0.0

    def dispatch(self, command: GestureCommand, force: bool = False) -> bool:
        """
        Dispatch a gesture command.
        Returns True if the command was acted on, False if skipped (duplicate).
        """
        if command == GestureCommand.NONE:
            return False

        now = time.time()
        # Deduplicate: same command within 0.5s is ignored
        if not force and command == self._last_dispatch and now - self._last_ts < 0.5:
            return False

        self._last_dispatch = command
        self._last_ts = now

        if self._verbose:
            print(f'[GESTURE] {command.value}')

        # ── AKSUMAEL goal injection ────────────────────────────────────────
        if self._aksumael:
            self._inject_aksumael(command)

        # ── UDP broadcast to remote nodes ──────────────────────────────────
        if self._targets:
            self._udp_send(command)

        return True

    def _inject_aksumael(self, command: GestureCommand):
        """Inject a goal or clear goals in AKSUMAEL.

        Prefers JarvisBrain.handle_gesture() (in-process tool dispatch) when
        the brain singleton is available — same tool infrastructure used by
        voice commands. Falls back to direct file injection when the brain
        isn't loaded (e.g. gesture/run_gesture.py running standalone).
        """
        # ── Prefer in-process brain dispatch ──────────────────────────────
        try:
            from jarvis.brain import get_brain
            brain = get_brain()
            if brain.handle_gesture(command):
                return
        except Exception as e:
            print(f'[GESTURE] brain dispatch failed ({e}), falling back to file injection')

        # ── File-based fallback ────────────────────────────────────────────
        if command == GestureCommand.HOLD:
            goals_path = BASE_DIR / "data" / "goals.json"
            try:
                with open(goals_path, "w") as f:
                    json.dump({"current": None, "stack": []}, f)
                print('[GESTURE] AKSUMAEL goals cleared (file fallback)')
            except Exception as e:
                print(f'[GESTURE] clear goals failed: {e}')
            return

        goal_entry = GOAL_MAP.get(command)
        if goal_entry is None:
            return  # TURN_LEFT / TURN_RIGHT are drive commands only

        goal, priority = goal_entry
        injected_path = BASE_DIR / "data" / "injected_goals.json"
        try:
            try:
                with open(injected_path) as f:
                    existing = json.load(f)
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []

            existing.append({
                "goal":     goal,
                "priority": priority,
                "source":   f"gesture:{command.value}",
            })
            with open(injected_path, "w") as f:
                json.dump(existing, f, indent=2)
            print(f'[GESTURE] AKSUMAEL → {goal} (priority {priority}, file fallback)')
        except Exception as e:
            print(f'[GESTURE] inject goal failed: {e}')

    def _udp_send(self, command: GestureCommand):
        """Send command string over UDP to all registered targets."""
        payload = f'{command.value}\n'.encode()
        for host, port in self._targets:
            try:
                self._sock.sendto(payload, (host, port))
            except Exception as e:
                print(f'[GESTURE] UDP send to {host}:{port} failed: {e}')

    def close(self):
        self._sock.close()
