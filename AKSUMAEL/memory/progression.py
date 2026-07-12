"""
AKSUMAEL — Minecraft tech-tree phase tracker.
Master goal: BEAT MINECRAFT by killing the Ender Dragon.
Phases: wood → stone → iron → diamond → nether → end
"""

import json
import os
import time

PROGRESSION_FILE = "data/progression.json"

PHASES = ["wood", "stone", "iron", "diamond", "nether", "end"]

_PHASE_GOALS = {
    "wood": (
        "PHASE 1/6 — WOOD: Chop trees, craft a crafting table, then wooden → stone pickaxe. "
        "Survive the first night. Do NOT waste time waiting — find a tree and start chopping."
    ),
    "stone": (
        "PHASE 2/6 — STONE: Mine cobblestone, craft stone tools and sword. "
        "Kill animals for food. Find coal for torches. "
        "Dig underground to find iron ore — that is the next milestone."
    ),
    "iron": (
        "PHASE 3/6 — IRON: Mine iron ore at Y=15–50. Build a furnace, smelt iron ore. "
        "Craft iron pickaxe, iron sword, iron armor. "
        "Once you have iron tools, go deeper (below Y=16) to find diamonds."
    ),
    "diamond": (
        "PHASE 4/6 — DIAMOND: Mine diamonds at Y=-58 to Y=16 (strip-mine or cave dive). "
        "Craft diamond pickaxe and sword. "
        "Collect 10+ obsidian blocks (mine near lava with diamond pickaxe). "
        "Craft flint and steel. Build a Nether portal: 4-wide × 5-tall obsidian frame."
    ),
    "nether": (
        "PHASE 5/6 — NETHER: You are in the Nether. Navigate to a Nether Fortress. "
        "Kill Blazes for 6+ blaze rods. Kill Endermen for 12+ ender pearls. "
        "Craft Eyes of Ender (blaze powder + ender pearl). Return to Overworld."
    ),
    "end": (
        "PHASE 6/6 — THE END: Throw Eyes of Ender to locate the stronghold. "
        "Find the End Portal room. Fill all 12 portal frame blocks with Eyes of Ender. "
        "Enter The End. Destroy all End Crystals on obsidian pillars first. "
        "Attack the Ender Dragon when it hovers over the portal. "
        "KILLING THE ENDER DRAGON = BEATING MINECRAFT. THIS IS THE FINAL GOAL."
    ),
}

_NEXT_MILESTONE = {
    "wood":    "craft a stone pickaxe",
    "stone":   "smelt 10+ iron ingots and craft an iron pickaxe",
    "iron":    "collect 12+ diamonds",
    "diamond": "build a Nether portal and step through it",
    "nether":  "collect 6 blaze rods + 12 ender pearls",
    "end":     "KILL THE ENDER DRAGON",
}

# Items that signal phase completion (inventory keys → minimum count)
_PHASE_UNLOCK = {
    "wood": {
        "items_any": [("stone_pickaxe", 1), ("iron_pickaxe", 1), ("diamond_pickaxe", 1)],
    },
    "stone": {
        "items_any": [("iron_ingot", 3), ("iron_pickaxe", 1)],
    },
    "iron": {
        "items_any": [("diamond", 3)],
    },
    "diamond": {
        # Detected by Nether-specific seen objects or items
        "seen_any":  ["netherrack", "nether_brick", "glowstone", "soul_sand",
                      "blaze", "ghast", "nether_fortress"],
        "items_any": [("blaze_rod", 1), ("blaze_powder", 1), ("nether_brick", 1)],
    },
    "nether": {
        "items_all": [("blaze_rod", 6), ("ender_pearl", 6)],
    },
    # "end" has no auto-unlock — beating the game ends the session
}


class ProgressionTracker:
    def __init__(self):
        self.phase = "wood"
        self.phase_start_tick = 0
        self.completed_phases: list = []
        self._load()

    def _load(self):
        if os.path.exists(PROGRESSION_FILE):
            try:
                d = json.load(open(PROGRESSION_FILE))
                self.phase = d.get("phase", "wood")
                self.phase_start_tick = d.get("phase_start_tick", 0)
                self.completed_phases = d.get("completed_phases", [])
            except Exception:
                pass

    def save(self):
        os.makedirs("data", exist_ok=True)
        with open(PROGRESSION_FILE, "w") as f:
            json.dump({
                "phase": self.phase,
                "phase_start_tick": self.phase_start_tick,
                "completed_phases": self.completed_phases,
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            }, f, indent=2)

    def advance_phase(self, new_phase: str, tick: int = 0):
        if new_phase == self.phase:
            return
        print(f"[PROGRESSION] *** PHASE COMPLETE: {self.phase.upper()} "
              f"→ {new_phase.upper()} ***")
        self.completed_phases.append(self.phase)
        self.phase = new_phase
        self.phase_start_tick = tick
        self.save()

    def auto_update(self, inventory, world_mem, tick: int = 0):
        """Check inventory/world state and advance phase when milestones are hit."""
        if self.phase == "end":
            return   # nothing to advance to

        unlock = _PHASE_UNLOCK.get(self.phase)
        if not unlock:
            return

        items = getattr(inventory, "items", {})
        seen  = getattr(world_mem, "seen_objects", {})

        # Check items_any: at least one tuple condition matches
        if "items_any" in unlock:
            for label, count in unlock["items_any"]:
                if items.get(label, 0) >= count:
                    self.advance_phase(PHASES[PHASES.index(self.phase) + 1], tick)
                    return

        # Check seen_any: any Nether object seen
        if "seen_any" in unlock:
            for label in unlock["seen_any"]:
                if seen.get(label, 0) > 0:
                    self.advance_phase(PHASES[PHASES.index(self.phase) + 1], tick)
                    return

        # Check items_all: ALL tuple conditions must match
        if "items_all" in unlock:
            if all(items.get(label, 0) >= count for label, count in unlock["items_all"]):
                self.advance_phase(PHASES[PHASES.index(self.phase) + 1], tick)

    def phase_context(self) -> str:
        """Full phase description — injected into every LLM prompt."""
        phase_idx = PHASES.index(self.phase) + 1
        total = len(PHASES)
        return (
            f"=== MASTER GOAL: BEAT MINECRAFT (kill the Ender Dragon) ===\n"
            f"Progress: {phase_idx}/{total} phases complete. "
            f"Completed: {', '.join(self.completed_phases) or 'none'}.\n"
            f"{_PHASE_GOALS[self.phase]}\n"
            f"Next milestone: {_NEXT_MILESTONE[self.phase]}"
        )

    def context_summary(self) -> str:
        """One-line summary — prepended to the history block."""
        return (
            f"[PROGRESSION] Phase {PHASES.index(self.phase)+1}/6 — {self.phase.upper()} | "
            f"Next: {_NEXT_MILESTONE[self.phase]}"
        )
