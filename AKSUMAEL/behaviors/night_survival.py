# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Night Survival Behavior             ║
# ║  Pillar up (or dig in) at dusk, wait out the night,   ║
# ║  descend at dawn                                       ║
# ╚══════════════════════════════════════════════════════╝
#
# world_mem.game_tick is a heuristic day-cycle counter (wraps at
# config.MC_DAY_TICKS — see WorldMemory.update). We can't read the real
# MC clock, so this fires once per approximated day: when game_tick
# crosses NIGHT_APPROACH_TICK we haven't already handled this day, we
# shelter until world_mem.is_daytime() reports true again.
#
# Building-block availability is judged from the InventoryReader cache
# (real screen-read counts) when it's warm; if it's cold/empty we
# optimistically assume blocks are available (mining always yields
# cobblestone) rather than blocking shelter on an unknown.

import time
import config

_BLOCK_ITEMS = (
    'cobblestone', 'stone', 'dirt', 'netherrack', 'sand', 'gravel',
    'oak_planks', 'spruce_planks', 'birch_planks', 'jungle_planks',
    'acacia_planks', 'dark_oak_planks', 'cobbled_deepslate',
)

_LOOK_DOWN  = {'dx': 0, 'dy': 300}   # pitch camera toward the ground
_LOOK_LEVEL = {'dx': 0, 'dy': -300}  # pitch camera back toward the horizon


def _idle() -> dict:
    return {'observation': '', 'action': 'wait',
            'key': None, 'click': None, 'look': None,
            'gamepad': None, 'confidence': 0.0}


class NightSurvivalBehavior:
    """
    Tick-driven shelter state machine.

    States: 'idle' -> 'pillar' -> 'dig_in' (fallback) -> 'waiting' -> 'descend' -> 'idle'
    """

    def __init__(self, executor, goals):
        self._executor = executor
        self._goals     = goals

        self._state          = 'idle'
        self._last_night_day = -1     # approximated day index last handled
        self._pillar_count   = 0
        self._wait_ticks      = 0
        self._dug_in          = False  # True if we fell back to the dig-in strategy

    def is_active(self) -> bool:
        return self._state != 'idle'

    def _has_blocks(self, inv_snapshot: dict) -> bool:
        if not inv_snapshot:
            return True   # unknown — don't block shelter on a cold cache
        return any(inv_snapshot.get(item, 0) > 0 for item in _BLOCK_ITEMS)

    def update(self, world_mem, inv_snapshot: dict, tick: int):
        """
        Call every tick. Returns an action_dict to force this tick, or
        None if night survival isn't engaged (caller falls through to
        skills/FSM/LLM as usual).
        """
        approaching_night = (
            world_mem.game_tick > config.NIGHT_APPROACH_TICK
            and not world_mem.is_daytime()
        )
        day_index = world_mem.total_ticks // config.MC_DAY_TICKS

        if self._state == 'idle':
            if not (approaching_night and self._last_night_day != day_index):
                return None
            self._last_night_day = day_index
            self._goals.push('find_shelter')
            if self._has_blocks(inv_snapshot):
                print('[NIGHT] dusk detected — pillaring up')
                self._state        = 'pillar'
                self._pillar_count = 0
                return self._do_pillar()
            else:
                print('[NIGHT] dusk detected, no blocks — digging in instead')
                self._state  = 'dig_in'
                self._dug_in = True
                return self._do_dig_in()

        if self._state == 'pillar':
            return self._do_pillar()

        if self._state == 'dig_in':
            return self._do_dig_in()

        if self._state == 'waiting':
            return self._do_wait(world_mem)

        if self._state == 'descend':
            return self._do_descend()

        return None

    def _do_pillar(self) -> dict:
        """Jump + place a block underneath, repeated PILLAR_HEIGHT times."""
        ad = _idle()
        if self._pillar_count == 0:
            self._executor.execute({'key': config.BLOCK_SLOT, 'source': 'night'})
            self._executor.execute({'look': _LOOK_DOWN, 'source': 'night'})

        self._executor.execute({'key': 'space', 'source': 'night'})
        time.sleep(0.2)
        self._executor.execute({'click': [50.0, 50.0], 'button': 'right', 'source': 'night'})
        time.sleep(0.2)
        self._pillar_count += 1

        ad['action']      = f'night:pillar ({self._pillar_count}/{config.PILLAR_HEIGHT})'
        ad['observation'] = 'Pillaring up for the night'
        ad['confidence']  = 0.8

        if self._pillar_count >= config.PILLAR_HEIGHT:
            print(f'[NIGHT] pillared {config.PILLAR_HEIGHT} blocks — waiting for dawn')
            self._state       = 'waiting'
            self._wait_ticks   = 0
            self._executor.execute({'look': _LOOK_LEVEL, 'source': 'night'})
        return ad

    def _do_dig_in(self) -> dict:
        """No blocks available — mine a niche into the ground/wall and hole up."""
        ad = _idle()
        ad['key']         = 'w'
        ad['click']       = [50.0, 50.0]
        ad['delay_ms']    = config.MINE_HOLD_MS
        ad['action']      = 'night:dig_in'
        ad['observation'] = 'No blocks — digging in for the night'
        ad['confidence']  = 0.6

        self._pillar_count += 1
        if self._pillar_count >= 4:   # a few ticks of digging is enough for a 1-block niche
            print('[NIGHT] dug in — waiting for dawn')
            self._state      = 'waiting'
            self._wait_ticks = 0
        return ad

    def _do_wait(self, world_mem) -> dict:
        ad = _idle()
        ad['action']      = 'night:waiting_for_dawn'
        ad['observation'] = 'Sheltering — waiting for dawn'
        ad['confidence']  = 0.7
        self._wait_ticks  += 1

        if world_mem.is_daytime() or self._wait_ticks >= config.NIGHT_MAX_WAIT_TICKS:
            print(f'[NIGHT] dawn (or timeout after {self._wait_ticks} ticks) — descending')
            if self._dug_in:
                # Dig-in fallback has nothing to descend through — just leave.
                self._state = 'idle'
                self._dug_in = False
                self._pillar_count = 0
                if self._goals.current_goal() == 'find_shelter':
                    self._goals.pop()
                return None
            self._state        = 'descend'
            self._pillar_count = 0
            self._executor.execute({'look': _LOOK_DOWN, 'source': 'night'})
        return ad

    def _do_descend(self) -> dict:
        """Break back down through the placed pillar."""
        ad = _idle()
        ad['key']         = '2'   # pickaxe — breaks cobblestone/dirt fastest
        ad['click']       = [50.0, 50.0]
        ad['delay_ms']    = config.MINE_HOLD_MS
        ad['action']      = f'night:descend ({self._pillar_count + 1}/{config.PILLAR_HEIGHT})'
        ad['observation'] = 'Mining back down at dawn'
        ad['confidence']  = 0.7

        self._pillar_count += 1
        if self._pillar_count >= config.PILLAR_HEIGHT:
            print('[NIGHT] reached the ground — resuming normal activity')
            self._state        = 'idle'
            self._pillar_count = 0
            self._executor.execute({'look': _LOOK_LEVEL, 'source': 'night'})
            if self._goals.current_goal() == 'find_shelter':
                self._goals.pop()
        return ad
