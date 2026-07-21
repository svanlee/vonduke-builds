# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Respawn Behavior                    ║
# ║  Detects death/respawn screen and clicks Respawn      ║
# ╚══════════════════════════════════════════════════════╝
#
# Triggered when: no HUD elements detected for N consecutive ticks
# (blank/death screen) OR Claude reports seeing a death/respawn screen
# in its observation.

import time

# HUD elements Minecraft renders as screen-space UI, unaffected by scene
# lighting — they stay visible whether the agent is in a lit field or a
# pitch-black cave, but vanish entirely on the death/respawn screen. Using
# their absence (instead of raw `len(objects) == 0`) as the death signal
# avoids false-triggering underground, where YOLO can legitimately detect
# zero *world* objects (no ore/mobs/blocks in view) for many consecutive
# ticks while still very much alive (2026-07-21: a false death trigger in a
# cave wiped an in-progress goal stack, including an active dig_up climb).
HUD_LABELS = frozenset({'hotbar', 'health_bar', 'hunger_bar', 'armor_bar', 'xp_bar'})


class RespawnBehavior:
    BLANK_TICKS_THRESHOLD = 5   # consecutive ticks with no HUD element before assuming death
    RESPAWN_COOLDOWN = 10.0     # seconds between respawn attempts

    def __init__(self, executor, goals=None):
        self._executor = executor
        self._goals     = goals   # optional GoalStack — forced to return_to_base on respawn
        self._blank_ticks = 0
        self._last_respawn = 0.0

    def update(self, objects: list, last_observation: str = '', suppress_blank: bool = False) -> bool:
        """
        Call every tick. Returns True if respawn was attempted.
        objects: YOLO detected objects this tick
        last_observation: Claude's last observation string
        suppress_blank: ignore the blank-HUD signal (still honors
            claude_sees_death). Set while DIGCLIMB is aiming straight down
            (2026-07-21) — that camera pitch reliably drops YOLO's hotbar/
            health/hunger detections for a few frames even though the HUD
            is real screen-space overlay and is still on screen, which was
            false-triggering a respawn (and wiping the goal stack) every
            pillar-up cycle.
        """
        obs_lower = last_observation.lower()
        death_keywords = ('you died', 'respawn', 'game over', 'death screen', 'score')
        claude_sees_death = any(k in obs_lower for k in death_keywords)

        has_hud = any(o.get('label', '') in HUD_LABELS for o in objects)
        if not has_hud and not suppress_blank:
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
                    'click': [50.0, 50.0],   # Respawn button is center of death screen (~50% y); 60% was hitting "Title Screen"
                    'button': 'left',
                    'gamepad': {'lx': 0, 'ly': 0, 'rx': 0, 'ry': 0, 'lt': 0, 'rt': 0, 'buttons': 0},
                    'source': 'respawn',
                })
                if self._goals is not None:
                    # Dropped items are wherever we died — clear whatever was
                    # queued and head straight back to base/spawn to recover
                    # tools/inventory drops, rather than resuming the old goal.
                    self._goals.current = 'return_to_base'
                    self._goals.stack.clear()
                    self._goals.save()
                    print('[RESPAWN] goal forced to return_to_base')
                return True
        return False
