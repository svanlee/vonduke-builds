"""
safety/beliefs.py — BeliefBase (abstract) and BeliefProxy (AKSUMAEL adapter).

To port to a new agent, subclass BeliefBase and implement all abstract properties.
BeliefProxy is AKSUMAEL-specific; it stays here as the reference implementation.
"""

import time
from abc import ABC, abstractmethod


# ── Abstract interface ────────────────────────────────────────────────────────

class BeliefBase(ABC):
    """
    Minimal belief interface required by the safety invariants.

    New agent? Subclass this, implement all abstract properties,
    pass your instance to Supervisor.review().
    """

    # ── vital signs ──────────────────────────────────────────────────────────
    @property
    @abstractmethod
    def health(self) -> float:
        """Normalised 0–1 health. 1.0 = full."""

    @property
    @abstractmethod
    def hunger(self) -> float:
        """Normalised 0–1 satiation. 1.0 = full."""

    @property
    @abstractmethod
    def has_food(self) -> bool:
        """True if consumable food is accessible."""

    # ── navigation ───────────────────────────────────────────────────────────
    @property
    @abstractmethod
    def orientation(self):
        """Heading string or compass bearing; None if unknown."""

    @property
    @abstractmethod
    def y_level(self) -> float:
        """Vertical position in world units."""

    # ── perception ───────────────────────────────────────────────────────────
    @property
    @abstractmethod
    def last_vision_ts(self) -> float:
        """Unix timestamp of most recent vision frame. 0.0 if never received."""

    @property
    @abstractmethod
    def inventory_count(self) -> int:
        """Number of occupied inventory slots."""

    @property
    @abstractmethod
    def inventory_max(self) -> int:
        """Total inventory capacity (slots)."""

    # ── optional ─────────────────────────────────────────────────────────────
    @property
    def mine_pitch(self) -> float:
        """Camera pitch in degrees during MINE. Override if tracked."""
        return 0.0

    @property
    def light_level(self):
        """Ambient light level or None."""
        return None

    @property
    def threats(self) -> list:
        """List of visible hostile entities. Override if tracked."""
        return []

    @property
    def context_key(self) -> str:
        """Coarse spatial key for procedural memory. Override if position tracked."""
        return "global"

    def summary(self) -> str:
        """Human-readable one-liner for Tier 2 prompts."""
        return (f"health={self.health:.0%} hunger={self.hunger:.0%} "
                f"facing={self.orientation} inventory={self.inventory_count}/{self.inventory_max}")


# ── AKSUMAEL adapter ─────────────────────────────────────────────────────────

class BeliefProxy(BeliefBase):
    """
    Reads from AKSUMAEL's WorldMemory + live FSM fields.

    world_mem       — WorldMemory instance (or SimpleNamespace in tests)
    mine_pitch_val  — camera pitch in degrees from runtime.py (float)
    inv_count       — current occupied slot count from runtime.py (int)
    last_vision_ts  — unix timestamp of last YOLO frame (float)
    """

    def __init__(self, world_mem, mine_pitch: float = 0.0,
                 inventory_count: int = 0, last_vision_ts: float = 0.0):
        self._wm             = world_mem
        self._mine_pitch     = mine_pitch
        self._inventory_count = inventory_count
        self._last_vision_ts = last_vision_ts

    # ── vital signs ──────────────────────────────────────────────────────────
    @property
    def health(self) -> float:
        raw = getattr(self._wm, 'health_pct', 1.0)
        # WorldMemory stores 0–1; convert to 0–20 for Minecraft-scale invariants
        return raw * 20.0

    @property
    def hunger(self) -> float:
        raw = getattr(self._wm, 'hunger_pct', 1.0)
        return raw * 20.0

    @property
    def has_food(self) -> bool:
        return bool(getattr(self._wm, 'food_items', []))

    # ── navigation ───────────────────────────────────────────────────────────
    @property
    def orientation(self):
        return getattr(self._wm, 'facing', None)

    @property
    def y_level(self) -> float:
        return getattr(self._wm, 'y_level', 64)

    # ── perception ───────────────────────────────────────────────────────────
    @property
    def last_vision_ts(self) -> float:
        return self._last_vision_ts

    @property
    def inventory_count(self) -> int:
        return self._inventory_count

    @property
    def inventory_max(self) -> int:
        return 36   # Minecraft fixed slot count

    # ── optional overrides ───────────────────────────────────────────────────
    @property
    def mine_pitch(self) -> float:
        return self._mine_pitch

    @property
    def light_level(self):
        return getattr(self._wm, 'light_level', None)

    @property
    def threats(self) -> list:
        return []   # wire in hostile mob list if tracked

    @property
    def context_key(self) -> str:
        wm = self._wm
        px = getattr(wm, 'pos_x', None)
        pz = getattr(wm, 'pos_z', None)
        if px is not None and pz is not None:
            return f"{int(px)//32}:{int(pz)//32}"
        return "global"
