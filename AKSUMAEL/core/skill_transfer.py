# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Cross-Environment Skill Transfer           ║
# ╚══════════════════════════════════════════════════════╝
#
# data/skills/ is one flat pool (skills/skill_system.py) with no notion of
# "which environment is this for" — every skill mined so far was mined
# against Minecraft's YOLO vocabulary. This module adds a layer on top,
# without touching how skills are mined, matched, or stored:
#
#   universal skills (Skill.universal == True) — generic UI interaction
#   (navigate_menu, click_button, read_text, type_text, scroll) that stays
#   loaded no matter which core/env_profile.py profile is active.
#
#   env-specific skills — everything else. Scoped to a profile by
#   trigger_objects overlap with that profile's yolo_classes, so a skill
#   mined for Minecraft ore mining never fires while driving the robocar.
#
# The result is installed as a session-only filter on SkillSystem (see
# SkillSystem.set_active_filter) — nothing is deleted, blacklisted, or
# re-tagged on disk just because a different environment is active this
# session.

from __future__ import annotations

from skills.skill_system import SkillSystem
from core.env_profile import EnvProfile

# Canonical categories a skill can be tagged universal under. This is
# documentation/classification only — the thing skill_transfer actually
# acts on is the Skill.universal boolean (see mark_universal below).
UNIVERSAL_SKILL_CATEGORIES = (
    'navigate_menu', 'click_button', 'read_text', 'type_text', 'scroll',
)


def mark_universal(skill_system: SkillSystem, name: str) -> bool:
    """Tag an existing skill as universal (always loaded regardless of
    which env_profile is active) and persist it. Returns False if `name`
    isn't a known skill."""
    sk = skill_system.skills.get(name)
    if sk is None:
        return False
    sk.universal = True
    skill_system.save(sk)
    return True


def active_skill_names(skill_system: SkillSystem, profile: EnvProfile) -> set | None:
    """Compute which skill names should be active for `profile`: every
    universal skill, plus every env-specific skill whose trigger_objects
    overlap the profile's yolo_classes.

    Returns None (meaning "no filtering — every non-blacklisted skill is
    eligible") when `profile.yolo_classes` is empty. This is deliberate,
    not a gap: Minecraft's own adapter (environments/minecraft_env.py)
    keeps OBJECT_CLASSES empty by design because its vocabulary is taught
    live via the YOLO label DB rather than fixed up front, and a freshly
    bootstrapped profile (core/env_detector.py) starts with yolo_classes
    empty too, before core/label_queue.py has trained a detector for it.
    In both cases there's nothing to meaningfully scope against yet, so
    restricting to universal-only would silently disable every skill
    Minecraft has ever mined — exactly the backward-compatibility break
    this module exists to avoid.
    """
    env_classes = set(profile.yolo_classes or [])
    if not env_classes:
        return None

    active = set()
    for sk in skill_system.skills.values():
        if sk.universal or (set(sk.trigger_objects) & env_classes):
            active.add(sk.name)
    return active


def apply_profile(skill_system: SkillSystem, profile: EnvProfile) -> set | None:
    """Compute and install the active-skill filter for `profile` on
    `skill_system`. Returns the resulting active-name set (or None if
    unfiltered) for logging."""
    names = active_skill_names(skill_system, profile)
    skill_system.set_active_filter(names)
    return names


def clear_filter(skill_system: SkillSystem) -> None:
    """Restore unfiltered behavior — the state a fresh SkillSystem()
    already starts in; provided for symmetry/explicitness at shutdown or
    when switching profiles mid-session."""
    skill_system.set_active_filter(None)
