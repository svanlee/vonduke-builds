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
#
# Food-slot tracking: an eat is judged "failed" if the hunger_bar width is
# unchanged on the tick right after it fires (i.e. eating had no visible
# effect — slot 9 was likely empty). After a few failed eats in a row we
# flag slot9_empty and run the grab_food skill directly (bypassing
# SkillSystem, since its trigger — the always-visible hotbar — would get
# it pruned by HUD_ALWAYS_VISIBLE matching/prune_bad logic).

import json
import time
from pathlib import Path

GRAB_FOOD_SKILL = Path('data/skills/grab_food.json')
WIDTH_EPS = 1.0   # px — treat width delta below this as "unchanged"


class HungerBehavior:
    EAT_THRESHOLD_FRAC = 0.30   # eat when bar width < 30% of widest seen (~6/20 shanks)
    EAT_COOLDOWN = 15.0         # seconds between eat attempts
    FOOD_SLOT = '9'             # hotbar slot assumed to hold food
    MAX_FAILED_EATS = 3         # consecutive no-effect eats before flagging slot 9 empty

    def __init__(self, executor, goals=None):
        self._executor  = executor
        self._goals     = goals   # optional GoalStack — pushes/pops 'find_food'
        self._max_width = 0.0
        self._last_eat  = 0.0

        self.slot9_empty = False   # tracked via inference, not direct observation
        self._failed_eats = 0
        self._awaiting_eat_result = False
        self._width_before_eat = None

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

        self._check_eat_result(width, world_mem)

        if frac >= self.EAT_THRESHOLD_FRAC:
            return False

        now = time.time()
        if now - self._last_eat < self.EAT_COOLDOWN:
            return False
        self._last_eat = now

        print(f'[HUNGER] bar at {frac:.0%} of max width — eating')
        self._width_before_eat = width
        self._awaiting_eat_result = True
        try:
            self._executor.execute({
                'key': self.FOOD_SLOT, 'click': None, 'gamepad': None,
                'source': 'hunger',
            })
            time.sleep(0.15)
            self._executor.execute({
                'key': None, 'click': [50.0, 50.0], 'button': 'right',
                'gamepad': None, 'source': 'hunger',
            })
        except Exception as e:
            print(f'[HUNGER] eat action errored: {e}')
            self._awaiting_eat_result = False
            return False

        return True

    def _check_eat_result(self, width: float, world_mem=None):
        """Judge the outcome of the eat fired on the previous tick."""
        if not self._awaiting_eat_result:
            return
        self._awaiting_eat_result = False

        if abs(width - self._width_before_eat) < WIDTH_EPS:
            self._failed_eats += 1
            print(f'[HUNGER] eat had no effect on bar width '
                  f'({self._failed_eats}/{self.MAX_FAILED_EATS} failed)')
            if self._failed_eats >= self.MAX_FAILED_EATS and not self.slot9_empty:
                self._flag_slot9_empty(world_mem)
        else:
            # Bar width moved — eat succeeded, assume slot 9 has food.
            self._failed_eats = 0
            was_empty = self.slot9_empty
            self.slot9_empty = False
            if was_empty and self._goals is not None and self._goals.current_goal() == 'find_food':
                print('[HUNGER] eat succeeded — clearing find_food goal')
                self._goals.pop()

    def _flag_slot9_empty(self, world_mem=None):
        self.slot9_empty = True
        msg = 'WARNING: slot 9 may be empty — need food'
        print(f'[HUNGER] {msg}')
        if world_mem is not None:
            world_mem.update([], event=msg)
        if self._goals is not None and not self._goals.has_goal('find_food'):
            self._goals.push('find_food')
        self._run_grab_food_skill()

    def _run_grab_food_skill(self):
        if not GRAB_FOOD_SKILL.exists():
            return
        try:
            data = json.loads(GRAB_FOOD_SKILL.read_text())
        except Exception as e:
            print(f'[HUNGER] could not load {GRAB_FOOD_SKILL}: {e}')
            return

        print(f'[HUNGER] slot 9 empty — running {data.get("name", "grab_food")}')
        for step in data.get('actions', []):
            key = step.get('key')
            if key:
                self._executor.execute({
                    'key': key, 'click': None, 'gamepad': None,
                    'source': 'hunger',
                })
            hold_ms = step.get('hold_ms')
            if hold_ms:
                time.sleep(hold_ms / 1000.0)

        self.on_inventory_opened()

    def on_inventory_opened(self):
        """Reset failure tracking — inventory contents may have changed."""
        self._failed_eats = 0
        self.slot9_empty = False
