# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Torch Placement Behavior            ║
# ║  Places torches periodically in dark areas to stop    ║
# ║  mob spawns near the mining site                       ║
# ╚══════════════════════════════════════════════════════╝
#
# "Dark area" is approximated from signals we already track — there's no
# direct YOLO "darkness" class — using WorldMemory: either it's night
# (is_daytime() false) or we're below TORCH_DARK_Y_LEVEL (cave/underground,
# same y_range bucketing world_memory.context_summary() already uses).

import time
import config


class TorchBehavior:
    def __init__(self, executor):
        self._executor    = executor
        self._last_place  = 0.0

    def is_dark(self, world_mem) -> bool:
        return (not world_mem.is_daytime()) or (world_mem.y_level < config.TORCH_DARK_Y_LEVEL)

    def should_trigger(self, world_mem) -> bool:
        if not self.is_dark(world_mem):
            return False
        return (time.time() - self._last_place) >= config.TORCH_COOLDOWN_SEC

    def place(self):
        """Select the torch hotbar slot and place one at the crosshair."""
        self._last_place = time.time()
        print(f'[TORCH] dark area (Y-level/night) — placing torch (slot {config.TORCH_SLOT})')
        self._executor.execute({'key': config.TORCH_SLOT, 'click': None,
                                'gamepad': None, 'source': 'torch'})
        time.sleep(0.15)
        self._executor.execute({'key': None, 'click': [50.0, 50.0], 'button': 'right',
                                'gamepad': None, 'source': 'torch'})
