# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Hunger Behavior                     ║
# ║  Watches hunger_bar bbox width and eats when low       ║
# ╚══════════════════════════════════════════════════════╝
#
# hunger_bar is a HUD element that is visible every tick, so it can't be
# used as a mined-skill trigger (see HUD_ALWAYS_VISIBLE in skill_system.py
# — any skill triggered solely by it would be pruned). Instead this fires
# directly off the YOLO detection's bounding-box width, the same way
# RespawnBehavior watches for a blank screen.

import time


class HungerBehavior:
    EAT_THRESHOLD_FRAC = 0.40   # eat when bar width < 40% of widest seen
    EAT_COOLDOWN = 15.0         # seconds between eat attempts
    FOOD_SLOT = '9'             # hotbar slot assumed to hold food

    def __init__(self, executor):
        self._executor  = executor
        self._max_width = 0.0
        self._last_eat  = 0.0

    def update(self, objects: list, world_mem=None) -> bool:
        """
        Call every tick with current YOLO detections.
        Returns True if an eat action was fired.
        """
        bar = next((o for o in objects if o.get('label') == 'hunger_bar'), None)
        if not bar:
            return False

        box = bar.get('box')
        if not box or len(box) != 4:
            return False

        width = box[2] - box[0]
        if width <= 0:
            return False

        self._max_width = max(self._max_width, width)
        frac = width / self._max_width if self._max_width else 1.0

        if world_mem is not None:
            world_mem.set_hunger_fraction(frac)

        if frac >= self.EAT_THRESHOLD_FRAC:
            return False

        now = time.time()
        if now - self._last_eat < self.EAT_COOLDOWN:
            return False
        self._last_eat = now

        print(f'[HUNGER] bar at {frac:.0%} of max width — eating')
        self._executor.execute({
            'key': self.FOOD_SLOT, 'click': None, 'gamepad': None,
            'source': 'hunger',
        })
        time.sleep(0.15)
        self._executor.execute({
            'key': None, 'click': [50.0, 50.0], 'button': 'right',
            'gamepad': None, 'source': 'hunger',
        })
        return True
