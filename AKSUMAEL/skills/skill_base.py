# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Skill Base (Phase 2)                      ║
# ║  Typed dataclass wrapper over skills/skill_system.py  ║
# ╚══════════════════════════════════════════════════════╝
#
# This module does NOT replace `skills.skill_system.Skill` — that class stays
# the runtime representation (steps, timing, replay, mining, evolution).
# What lives here is a formal, typed *interface* on top of it:
#
#   BeliefPrecondition — typed preconditions checked against a belief-state dict
#   Skill              — a named, described, belief-gated capability
#
# A `Skill` may wrap a legacy skill (`Skill.from_legacy`, used by
# registry.load_from_json_dir) or stand alone as a hand-authored behaviour that
# is really executed by core/fsm.py (chop_tree, dig_up, ...).

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

# `_canonical` maps a raw label onto its LABEL_SYNONYMS group name
# ('oak' → 'tree', 'bread' → 'food', 'coal' → 'ore'). It is private to
# skill_system but this module is the same package and deliberately reuses the
# one canonicalisation table rather than starting a second, divergent one.
from skills.skill_system import (
    Skill as LegacySkill, _canonical, HUD_ALWAYS_VISIBLE,
    HW_ACTION_KIND, normalise_step as _normalise_step,
)

__all__ = [
    'BeliefPrecondition', 'Skill',
    'labels_match', 'belief_labels', 'belief_inventory',
    'belief_health', 'belief_y_level', 'belief_last_used',
    'ACTION_KINDS', 'VALID_ACTION_KINDS', 'HW_ACTION_KIND',
    'action_kinds', 'is_hw_action', 'normalise_step',
]


# ── Action kinds ───────────────────────────────────────────────
# A skill step's `action` is a flat dict whose *keys* name what to do. The
# four primary kinds:
#
#   key      -> a keyboard press          {'key': 'space'}
#   click    -> an absolute mouse click   {'click': [0.5, 0.5]}
#   gamepad  -> a controller report       {'gamepad': {'lx': ..., 'buttons': ...}}
#   hw       -> a KB2040 bridge command   {'hw': {'cmd': 'gpio_out', 'pin': 5, 'value': 1}}
#
# `hw` is the odd one out: the first three drive the *game* through the HID
# firmware, while `hw` drives whatever is wired to the board — GPIO, I2C,
# SPI, ADC — through uart/kb2040_bridge.py's JSON protocol. It goes through
# BridgeClient.send() rather than ActionExecutor.execute(), which is why
# SkillReplayer in skills/skill_system.py has to branch on it.
#
# HW_ACTION_KIND itself is defined in skill_system (imported above) because
# the step parser there needs it and cannot import this module back.
ACTION_KINDS = frozenset({'key', 'click', 'gamepad', HW_ACTION_KIND})

# Kinds the recorded skills in data/skills/*.json actually emit alongside
# the four above. Listed separately because they are variants rather than
# categories (`look`/`mouse_hold` are mouse detail, `keyboard_state` is a
# true key hold), but they must count as valid — a validator that rejected
# them would reject most of the mined skill library.
EXTRA_ACTION_KINDS = frozenset({
    'look', 'mouse_hold', 'mouse_button', 'mouse_button_name', 'keyboard_state',
})
VALID_ACTION_KINDS = ACTION_KINDS | EXTRA_ACTION_KINDS


def action_kinds(action: Any) -> set[str]:
    """The valid kind names present (and non-empty) in an action dict.

    Empty for a no-op step — every recorded step carries the full key set
    with nulls in the slots it doesn't use, so presence of a key means
    nothing; a truthy value does.
    """
    if not isinstance(action, dict):
        return set()
    return {k for k in VALID_ACTION_KINDS if action.get(k)}


def is_hw_action(action: Any) -> bool:
    """True if this action is a KB2040 bridge command."""
    return isinstance(action, dict) and bool(action.get(HW_ACTION_KIND))


# Re-exported from skill_system, which owns step parsing (SkillStep is the
# runtime representation and this module sits on top of it, so the import
# can only go this way). Listed in __all__ because the kind vocabulary is
# this module's job and a caller validating a step should not have to know
# that the parser lives one layer down.
normalise_step = _normalise_step


# ── Label matching ─────────────────────────────────────────────
# Preconditions are written in generic terms ('tree', 'ore', 'food'); the
# belief state carries concrete YOLO/inventory labels ('oak_log',
# 'deepslate_iron_ore', 'bread'). These helpers bridge the two.

def _norm(label: Any) -> str:
    return str(label or '').lower().strip()


def _tokens(label: str) -> list[str]:
    return [t for t in label.replace('-', '_').split('_') if t]


def labels_match(required: str, present: str) -> bool:
    """True if the concrete label `present` satisfies the requirement `required`.

    Matching is deliberately *directional*: a generic requirement widens to
    cover specific labels, but never the reverse. So 'tree' matches 'oak_log'
    and 'ore' matches 'diamond_ore', while 'iron_ingot' does NOT match
    'gold_ingot' (which a naive token-overlap rule would wrongly accept, since
    both canonicalise through the shared 'ore'/'ingot' tokens).
    """
    r, p = _norm(required), _norm(present)
    if not r or not p:
        return False
    if r == p:
        return True
    rc = _canonical(r)
    # Whole-label group equality: 'log' ~ 'tree', 'cobblestone' ~ 'stone'.
    if rc == _canonical(p):
        return True
    # Generic requirement vs a token of a specific label:
    # 'tree'/'oak_log', 'ore'/'diamond_ore', 'apple'/'golden_apple'.
    for tok in _tokens(p):
        if r == tok or rc == _canonical(tok):
            return True
    return False


def _any_match(required: list[str], present: set[str]) -> bool:
    return any(labels_match(r, p) for r in required for p in present)


def _all_match(required: list[str], present: set[str]) -> bool:
    return all(any(labels_match(r, p) for p in present) for r in required)


# ── Belief-state accessors ─────────────────────────────────────
# The belief state is a plain dict so callers can assemble one from whatever
# they have (world_mem, a YOLO frame, a test fixture) without importing
# runtime types. Recognised keys:
#
#   yolo_detections : list[dict] with 'label', or list[str], or {label: conf}
#   inventory       : {item: count}, or list[str]
#   health          : 0.0–1.0 fraction (a 0–20 value is accepted and scaled)
#   y_level         : int
#   last_used_times : {skill_name: unix_ts}

def belief_labels(belief_state: dict) -> set[str]:
    raw = (belief_state.get('yolo_detections')
           if belief_state.get('yolo_detections') is not None
           else belief_state.get('objects', belief_state.get('objects_seen')))
    if not raw:
        return set()
    if isinstance(raw, dict):                      # {label: confidence}
        return {_norm(k) for k in raw if _norm(k)}
    out = set()
    for o in raw:
        label = o.get('label') if isinstance(o, dict) else o
        if _norm(label):
            out.add(_norm(label))
    return out


def belief_inventory(belief_state: dict) -> dict[str, int]:
    raw = belief_state.get('inventory') or {}
    if isinstance(raw, dict):
        return {_norm(k): int(v or 0) for k, v in raw.items() if _norm(k)}
    return {_norm(item): 1 for item in raw if _norm(item)}


def belief_health(belief_state: dict) -> Optional[float]:
    """Health as a 0.0–1.0 fraction, or None if unknown.

    Runtime reports `world_mem.health_pct` as a fraction; a 0–20 hearts value
    is also accepted and scaled, so both conventions are safe to pass in.
    """
    h = belief_state.get('health', belief_state.get('health_pct'))
    if h is None:
        return None
    try:
        h = float(h)
    except (TypeError, ValueError):
        return None
    return h / 20.0 if h > 1.0 else h


def belief_y_level(belief_state: dict) -> Optional[int]:
    y = belief_state.get('y_level', belief_state.get('y'))
    if y is None:
        return None
    try:
        return int(y)
    except (TypeError, ValueError):
        return None


def belief_last_used(belief_state: dict) -> dict[str, float]:
    return belief_state.get('last_used_times') or {}


# ── Preconditions ──────────────────────────────────────────────

@dataclass
class BeliefPrecondition:
    """Typed preconditions evaluated against a belief-state dict.

    `yolo_visible` and `has_item` are ANY-of by default: the skill is
    applicable if *any* listed label is visible / *any* listed item is held.
    That is what makes "chop_tree needs tree OR log" and "eat_food needs food
    OR apple OR bread OR ..." expressible as one flat list.

    `has_item_mode='all'` switches items to ALL-of, which is the semantics the
    legacy `Skill.check_preconditions()` uses — `from_legacy()` sets it so
    JSON-loaded skills keep behaving exactly as they do today.

    Health is a 0.0–1.0 fraction. `min_health` is a floor (skill needs at least
    this much health), `max_health` a ceiling (skill only makes sense while
    hurt — this is what stops eat_food firing at full health).
    """

    yolo_visible: list[str] = field(default_factory=list)
    has_item: list[str] = field(default_factory=list)
    min_health: Optional[float] = None
    max_health: Optional[float] = None
    y_level_max: Optional[int] = None
    cooldown_s: float = 0.0
    has_item_mode: str = 'any'          # 'any' | 'all'

    def is_empty(self) -> bool:
        return not (self.yolo_visible or self.has_item
                    or self.min_health is not None
                    or self.max_health is not None
                    or self.y_level_max is not None
                    or self.cooldown_s)

    # ── Evaluation ─────────────────────────────────────────────
    def unmet(self, belief_state: dict, skill_name: str = '',
              now: float | None = None) -> list[str]:
        """Human-readable reasons this precondition fails. Empty list = met.

        Unknown belief values never block: if the belief state carries no
        health reading, a health gate is skipped rather than failed. The bot
        runs with partial perception constantly (see the 2026-07-21 hud_reader
        ROI bug) and a missing reading must not silently disable every skill.
        """
        reasons: list[str] = []

        if self.yolo_visible:
            seen = belief_labels(belief_state)
            if not _any_match(self.yolo_visible, seen):
                reasons.append(f'none of {self.yolo_visible} visible')

        if self.has_item:
            held = {k for k, v in belief_inventory(belief_state).items() if v > 0}
            ok = (_all_match(self.has_item, held) if self.has_item_mode == 'all'
                  else _any_match(self.has_item, held))
            if not ok:
                joiner = 'all of' if self.has_item_mode == 'all' else 'none of'
                reasons.append(f'{joiner} {self.has_item} in inventory')

        health = belief_health(belief_state)
        if health is not None:
            if self.min_health is not None and health < self.min_health:
                reasons.append(f'health {health:.2f} < min {self.min_health:.2f}')
            if self.max_health is not None and health > self.max_health:
                reasons.append(f'health {health:.2f} > max {self.max_health:.2f}')

        y = belief_y_level(belief_state)
        if y is not None and self.y_level_max is not None and y > self.y_level_max:
            reasons.append(f'y_level {y} > max {self.y_level_max}')

        if self.cooldown_s and skill_name:
            last = belief_last_used(belief_state).get(skill_name)
            if last is not None:
                now = now if now is not None else belief_state.get('now', time.time())
                elapsed = now - float(last)
                if elapsed < self.cooldown_s:
                    reasons.append(f'cooling down ({elapsed:.1f}s '
                                   f'of {self.cooldown_s:.1f}s)')

        return reasons

    def check(self, belief_state: dict, skill_name: str = '',
              now: float | None = None) -> bool:
        return not self.unmet(belief_state, skill_name, now)

    # ── Serialisation ──────────────────────────────────────────
    def to_dict(self) -> dict:
        """Compact dict — omits unset gates so LLM-facing output stays terse."""
        d: dict[str, Any] = {}
        if self.yolo_visible:
            d['yolo_visible'] = list(self.yolo_visible)
        if self.has_item:
            d['has_item'] = list(self.has_item)
            if self.has_item_mode != 'any':
                d['has_item_mode'] = self.has_item_mode
        if self.min_health is not None:
            d['min_health'] = self.min_health
        if self.max_health is not None:
            d['max_health'] = self.max_health
        if self.y_level_max is not None:
            d['y_level_max'] = self.y_level_max
        if self.cooldown_s:
            d['cooldown_s'] = self.cooldown_s
        return d

    @classmethod
    def from_dict(cls, d: dict | None) -> 'BeliefPrecondition':
        d = d or {}
        return cls(
            yolo_visible=list(d.get('yolo_visible') or []),
            has_item=list(d.get('has_item') or []),
            min_health=d.get('min_health'),
            max_health=d.get('max_health'),
            y_level_max=d.get('y_level_max'),
            cooldown_s=float(d.get('cooldown_s') or 0.0),
            has_item_mode=d.get('has_item_mode', 'any'),
        )

    @classmethod
    def from_legacy(cls, preconditions: dict | None) -> 'BeliefPrecondition':
        """Convert a legacy `Skill.preconditions` dict, preserving semantics.

        Legacy keys: has_item (ALL-of), yolo_visible (ANY-of), max_y_level
        (exclusive — legacy fails when `y_level >= max_y_level`, so the
        inclusive `y_level_max` here is one lower).
        """
        d = preconditions or {}
        max_y = d.get('max_y_level')
        return cls(
            yolo_visible=list(d.get('yolo_visible') or []),
            has_item=list(d.get('has_item') or []),
            min_health=d.get('min_health'),
            max_health=d.get('max_health'),
            y_level_max=(int(max_y) - 1) if max_y is not None else d.get('y_level_max'),
            cooldown_s=float(d.get('cooldown_s') or 0.0),
            has_item_mode='all' if d.get('has_item') else 'any',
        )


# ── Skill ──────────────────────────────────────────────────────

@dataclass
class Skill:
    """A named capability with typed, belief-state-checkable preconditions.

    `legacy` holds the wrapped `skill_system.Skill` when this skill came from a
    data/skills/*.json file — that object still owns the replayable steps and
    the reward bookkeeping. Hand-authored skills leave it None and name their
    executor in `handler` (e.g. 'fsm:chop_tree').
    """

    name: str
    description: str = ''
    preconditions: BeliefPrecondition = field(default_factory=BeliefPrecondition)
    trigger_objects: list[str] = field(default_factory=list)
    handler: Optional[str] = None
    source: str = 'hand'                # 'hand' | 'json'
    priority: float = 0.0               # ties broken by avg_reward, then uses
    avg_reward: float = 0.0
    uses: int = 0
    blacklisted: bool = False
    legacy: Optional[LegacySkill] = field(default=None, repr=False, compare=False)

    @property
    def steps(self) -> list:
        """Replayable steps, if this wraps a legacy skill (else empty)."""
        return list(self.legacy.steps) if self.legacy is not None else []

    def is_applicable(self, belief_state: dict, now: float | None = None) -> bool:
        if self.blacklisted:
            return False
        return self.preconditions.check(belief_state, self.name, now)

    def why_not(self, belief_state: dict, now: float | None = None) -> list[str]:
        """Reasons this skill is not applicable — for planner/debug output."""
        if self.blacklisted:
            return ['blacklisted']
        return self.preconditions.unmet(belief_state, self.name, now)

    def describe(self) -> dict:
        """Human/LLM-readable description. Stable keys, no runtime objects."""
        return {
            'name':        self.name,
            'description': self.description,
            'requires':    self.preconditions.to_dict(),
            'triggers':    list(self.trigger_objects),
            'handler':     self.handler,
            'source':      self.source,
            'avg_reward':  round(self.avg_reward, 3),
            'uses':        self.uses,
            'blacklisted': self.blacklisted,
        }

    @classmethod
    def from_legacy(cls, legacy: LegacySkill, description: str = '') -> 'Skill':
        """Wrap an existing `skill_system.Skill` without mutating it."""
        pre = BeliefPrecondition.from_legacy(legacy.preconditions)
        # Most JSON skills carry no explicit preconditions — they are matched by
        # trigger overlap alone (SkillSystem.find_best). Derive a visibility gate
        # from the trigger so find_applicable() means the same thing for them;
        # HUD labels are stripped because they are on screen every tick and would
        # make the skill unconditionally applicable.
        if not pre.yolo_visible and legacy.trigger_objects:
            pre.yolo_visible = sorted(set(legacy.trigger_objects) - HUD_ALWAYS_VISIBLE)
        return cls(
            name=legacy.name,
            description=description or f'Replay {len(legacy.steps)}-step learned '
                                       f'sequence for {legacy.trigger_objects}.',
            preconditions=pre,
            trigger_objects=list(legacy.trigger_objects),
            handler='skill_system:replay',
            source='json',
            avg_reward=float(legacy.avg_reward or 0.0),
            uses=int(legacy.uses or 0),
            blacklisted=bool(legacy.blacklisted),
            legacy=legacy,
        )

    def __repr__(self):
        return (f'<Skill {self.name} src={self.source} '
                f'req={self.preconditions.to_dict()}>')
