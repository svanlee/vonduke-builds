# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Respawn Behavior                    ║
# ║  Detects death/respawn screen and clicks Respawn      ║
# ╚══════════════════════════════════════════════════════╝
#
# Triggered when: no YOLO objects detected for N consecutive ticks
# (blank/death screen) OR Claude reports seeing a death/respawn screen
# in its observation.

import time


class RespawnBehavior:
    BLANK_TICKS_THRESHOLD = 5   # consecutive ticks with 0 objects before assuming death
    RESPAWN_COOLDOWN = 10.0     # seconds between respawn attempts

    def __init__(self, executor):
        self._executor = executor
        self._blank_ticks = 0
        self._last_respawn = 0.0

    def update(self, objects: list, last_observation: str = '') -> bool:
        """
        Call every tick. Returns True if respawn was attempted.
        objects: YOLO detected objects this tick
        last_observation: Claude's last observation string
        """
        obs_lower = last_observation.lower()
        death_keywords = ('you died', 'respawn', 'game over', 'death screen', 'score')
        claude_sees_death = any(k in obs_lower for k in death_keywords)

        if len(objects) == 0:
            self._blank_ticks += 1
        else:
            self._blank_ticks = 0

        blank_screen = self._blank_ticks >= self.BLANK_TICKS_THRESHOLD

        if blank_screen or claude_sees_death:
            now = time.time()
            if now - self._last_respawn > self.RESPAWN_COOLDOWN:
                self._last_respawn = now
                self._blank_ticks = 0
                print(f'[RESPAWN] death detected (blank={blank_screen}, claude={claude_sees_death}) — clicking respawn')
                self._executor.execute({
                    'key': None,
                    'click': [50.0, 60.0],   # Respawn button is roughly center-bottom
                    'button': 'left',
                    'gamepad': {'lx': 0, 'ly': 0, 'rx': 0, 'ry': 0, 'lt': 0, 'rt': 0, 'buttons': 0},
                    'source': 'respawn',
                })
                return True
        return False
