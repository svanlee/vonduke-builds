# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Skill Registry (Phase 2)                  ║
# ║  register / find_applicable / describe_all            ║
# ╚══════════════════════════════════════════════════════╝
#
# An in-memory dict of `skills.skill_base.Skill` keyed by name. It sits *on
# top* of SkillSystem — it never mutates, writes, or deletes anything in
# data/skills/, and SkillSystem's own mining/evolution loop is untouched.
#
#   from skills.registry import REGISTRY
#   REGISTRY.load_from_json_dir()                  # + the 48 learned skills
#   REGISTRY.find_applicable(belief_state)         # what can I do right now?
#   REGISTRY.describe_all()                        # what can I do at all? (LLM)

from __future__ import annotations

import json
import os
from typing import Iterator, Optional

import config
from core.fsm import ORE_TARGETS, TREE_TARGETS
from skills.skill_base import BeliefPrecondition, Skill
from skills.skill_system import Skill as LegacySkill

__all__ = ['SkillRegistry', 'REGISTRY', 'register_builtin_skills',
           'register', 'find_applicable', 'describe_all', 'load_from_json_dir']


class SkillRegistry:
    """Name → Skill. No persistence, no database — rebuilt on every boot."""

    def __init__(self, name: str = 'default'):
        self.name = name
        self._skills: dict[str, Skill] = {}

    # ── Registration ───────────────────────────────────────────
    def register(self, skill: Skill, *, overwrite: bool = True) -> Optional[Skill]:
        """Add `skill`. Returns the registered skill, or None if a skill of
        that name already exists and `overwrite` is False."""
        if not isinstance(skill, Skill):
            raise TypeError(f'register() expects skills.skill_base.Skill, '
                            f'got {type(skill).__name__}')
        if not skill.name:
            raise ValueError('cannot register a skill with an empty name')
        if skill.name in self._skills and not overwrite:
            return None
        self._skills[skill.name] = skill
        return skill

    def unregister(self, name: str) -> bool:
        return self._skills.pop(name, None) is not None

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def all(self, include_blacklisted: bool = False) -> list[Skill]:
        return [s for s in self._skills.values()
                if include_blacklisted or not s.blacklisted]

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: object) -> bool:
        return name in self._skills

    def __iter__(self) -> Iterator[Skill]:
        return iter(self._skills.values())

    def clear(self) -> None:
        self._skills.clear()

    # ── Lookup ─────────────────────────────────────────────────
    def find_applicable(self, belief_state: dict,
                        now: float | None = None) -> list[Skill]:
        """Every registered skill whose preconditions ALL hold in `belief_state`.

        Best first: priority, then avg_reward, then uses, then name.
        Blacklisted skills are never returned.
        """
        hits = [s for s in self._skills.values()
                if s.is_applicable(belief_state, now)]
        hits.sort(key=lambda s: (-s.priority, -s.avg_reward, -s.uses, s.name))
        return hits

    def explain(self, belief_state: dict, now: float | None = None) -> dict[str, list[str]]:
        """{skill_name: reasons it is not applicable} — debug/planner aid.
        Applicable skills map to an empty list."""
        return {s.name: s.why_not(belief_state, now) for s in self._skills.values()}

    def describe_all(self, include_blacklisted: bool = False) -> list[dict]:
        """Human-readable descriptions of every skill, for LLM planner prompts."""
        return [s.describe()
                for s in sorted(self.all(include_blacklisted), key=lambda s: s.name)]

    # ── JSON loading (read-only) ───────────────────────────────
    def load_from_json_dir(self, path: str | None = None, *,
                           overwrite: bool = False, verbose: bool = False) -> int:
        """Register every data/skills/*.json file. Returns the number added.

        Strictly read-only: unlike SkillSystem._load_all(), this never purges,
        rewrites, or deletes a skill file. `overwrite=False` (the default) means
        hand-authored registrations win over a same-named JSON file — the
        registered chop_tree stays the FSM behaviour, not chop_tree.json.
        """
        path = path or config.SKILLS_DIR
        if not os.path.isdir(path):
            return 0
        added = 0
        for fn in sorted(os.listdir(path)):
            if not fn.endswith('.json'):
                continue
            full = os.path.join(path, fn)
            if not os.path.isfile(full):
                continue
            try:
                with open(full) as f:
                    data = json.load(f)
                legacy = LegacySkill.from_dict(data)
            except (OSError, ValueError, KeyError) as e:
                if verbose:
                    print(f'[REGISTRY] skipping unreadable skill {fn}: {e}')
                continue
            if self.register(Skill.from_legacy(legacy), overwrite=overwrite):
                added += 1
            elif verbose:
                print(f'[REGISTRY] {legacy.name} already registered — keeping '
                      f'existing definition, ignoring {fn}')
        return added

    def __repr__(self):
        return f'<SkillRegistry {self.name} n={len(self._skills)}>'


# ── Hand-authored skills ───────────────────────────────────────
# These wrap behaviours that live in core/fsm.py. The registry describes and
# gates them; the FSM still executes them. `handler` records which.

def register_builtin_skills(registry: SkillRegistry) -> list[Skill]:
    """Register the hand-authored FSM behaviours. Returns what was added."""
    builtins = [
        Skill(
            name='chop_tree',
            description='Approach the nearest tree and break logs for wood.',
            preconditions=BeliefPrecondition(
                yolo_visible=['tree', 'log'],
                cooldown_s=5.0,
            ),
            trigger_objects=sorted(TREE_TARGETS),
            handler='fsm:chop_tree',
            priority=1.0,
        ),
        Skill(
            name='eat_food',
            description='Eat a held food item to restore health and hunger.',
            preconditions=BeliefPrecondition(
                # ANY-of: 'food' covers the whole synonym group; the explicit
                # names catch inventories that report concrete item labels.
                has_item=['food', 'apple', 'bread', 'carrot', 'meat'],
                # Ceiling, not floor: eating is pointless at full health, so the
                # skill drops out of find_applicable() once health recovers.
                max_health=0.9,
                cooldown_s=3.0,
            ),
            handler='fsm:eat_food',
            priority=3.0,
        ),
        Skill(
            name='mine_ore',
            description='Mine a visible ore vein with the pickaxe.',
            preconditions=BeliefPrecondition(
                yolo_visible=sorted(ORE_TARGETS),
                cooldown_s=2.0,
            ),
            trigger_objects=sorted(ORE_TARGETS),
            handler='fsm:mine_ore',
            priority=2.0,
        ),
    ]
    return [registry.register(s) for s in builtins]


# ── Default registry ───────────────────────────────────────────
# Built at import time with the hand-authored skills only. Loading the 48 JSON
# skills is an explicit call (`REGISTRY.load_from_json_dir()`) so importing this
# module never touches the filesystem.

REGISTRY = SkillRegistry('default')
register_builtin_skills(REGISTRY)


def register(skill: Skill, *, overwrite: bool = True) -> Optional[Skill]:
    return REGISTRY.register(skill, overwrite=overwrite)


def find_applicable(belief_state: dict, now: float | None = None) -> list[Skill]:
    return REGISTRY.find_applicable(belief_state, now)


def describe_all(include_blacklisted: bool = False) -> list[dict]:
    return REGISTRY.describe_all(include_blacklisted)


def load_from_json_dir(path: str | None = None, **kw) -> int:
    return REGISTRY.load_from_json_dir(path, **kw)
