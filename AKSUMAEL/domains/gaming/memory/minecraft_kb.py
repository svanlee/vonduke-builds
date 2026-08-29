"""
AKSUMAEL — Minecraft Knowledge Base
Two sections:
  mechanics   — static how-to guide per tech-tree phase
  discoveries — things AKSUMAEL learned from actual play (appended at runtime)
"""

import json
import os
import time

KB_FILE = "data/minecraft_kb.json"
MAX_DISCOVERIES = 100   # cap so the file doesn't grow forever


class MinecraftKB:
    def __init__(self):
        self._data = {"mechanics": {}, "discoveries": []}
        self._load()

    def _load(self):
        if os.path.exists(KB_FILE):
            try:
                with open(KB_FILE) as f:
                    self._data = json.load(f)
            except Exception:
                pass

    def _save(self):
        os.makedirs("data", exist_ok=True)
        with open(KB_FILE, "w") as f:
            json.dump(self._data, f, indent=2)

    # ── Read ──────────────────────────────────────────────────────

    def get_phase_mechanics(self, phase: str) -> str:
        """Full how-to block for the current phase — injected on strategic ticks."""
        m = self._data["mechanics"].get(phase)
        if not m:
            return ""
        lines = [f"[KB:{phase.upper()}] {m.get('goal', '')}"]
        lines.append(f"HOW: {m.get('how_to', '')}")
        tips = m.get("tips", [])
        if tips:
            lines.append("TIPS: " + " | ".join(tips))
        crafting = m.get("key_crafting", {})
        if crafting:
            recipes = "; ".join(f"{k}={v}" for k, v in crafting.items())
            lines.append(f"CRAFT: {recipes}")
        return "\n".join(lines)

    def recent_discoveries(self, n: int = 5) -> str:
        """Last N discoveries as a compact block."""
        disc = self._data.get("discoveries", [])[-n:]
        if not disc:
            return ""
        items = "; ".join(d["text"] for d in disc)
        return f"[DISCOVERIES] {items}"

    def strategic_context(self, phase: str, n_discoveries: int = 5) -> str:
        """Full mechanics + recent discoveries — for strategic planning ticks."""
        parts = [self.get_phase_mechanics(phase)]
        disc = self.recent_discoveries(n_discoveries)
        if disc:
            parts.append(disc)
        return "\n".join(p for p in parts if p)

    # ── Write ─────────────────────────────────────────────────────

    def add_discovery(self, text: str, tick: int = 0):
        """Append a new discovery (called when Claude returns a 'discovery' field)."""
        text = text.strip()
        if not text:
            return
        # Deduplicate: skip if very similar to an existing recent entry
        recent = [d["text"] for d in self._data["discoveries"][-10:]]
        if any(text.lower() in r.lower() or r.lower() in text.lower() for r in recent):
            return
        self._data["discoveries"].append({
            "text": text,
            "tick": tick,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        # Cap total discoveries
        if len(self._data["discoveries"]) > MAX_DISCOVERIES:
            self._data["discoveries"] = self._data["discoveries"][-MAX_DISCOVERIES:]
        self._save()
        print(f"[KB] discovery saved: {text[:80]}")
