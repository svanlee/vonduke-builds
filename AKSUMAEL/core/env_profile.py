# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Environment Profile                        ║
# ║  The persistent identity kept for every OS/game/app    ║
# ║  AKSUMAEL has ever been plugged into via capture card  ║
# ║  + KB2040.                                              ║
# ╚══════════════════════════════════════════════════════╝
#
# An EnvProfile is the game-agnostic counterpart to the hand-written
# adapters in environments/*.py (see core/env_registry.py). Those adapters
# are high-effort, hand-coded integrations for environments someone has
# already sat down and written observe()/execute()/reward() logic for.
# EnvProfile exists for everything else: the boot-time detector
# (core/env_detector.py) mints one automatically for any environment it
# recognizes on the capture feed, no code required, and it fills in over
# time as core/label_queue.py accumulates labeled frames and
# core/skill_transfer.py accumulates skills. When an environment does get
# a real adapter later, nothing here needs to change — env_registry and
# EnvProfile can both point at the same env_id.

from __future__ import annotations

import dataclasses
import json
import os
import re
import time

PROFILES_DIR = "data/env_profiles"

# Environments below this many overlapping tokens against every known
# profile are treated as new rather than fuzzy-matched onto an existing
# one — see match_env_type().
_MATCH_THRESHOLD = 0.34

_STOPWORDS = {
    'the', 'a', 'an', 'game', 'application', 'app', 'screen', 'desktop',
    'os', 'window', 'interface', 'ui', 'system',
}


def _slugify(text: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')
    return slug or 'unknown_env'


def _tokens(text: str) -> set:
    words = re.findall(r'[a-z0-9]+', text.lower())
    return {w for w in words if w not in _STOPWORDS}


def default_action_space() -> dict:
    """Generic desktop action space every new profile starts with — the
    same keyboard + relative/absolute mouse primitives the KB2040 firmware
    already exposes (see uart/kb2040_packer.py) regardless of what's
    plugged in on the other end. Env-specific key maps (like
    data/envs/*.yaml's key_map) layer on top once the environment is
    understood."""
    return {
        'keys': ['w', 'a', 's', 'd', 'space', 'ctrl', 'shift', 'enter',
                 'esc', 'tab', 'up', 'down', 'left', 'right'],
        'mouse_move': True,
        'mouse_click': ['left', 'right'],
        'mouse_scroll': True,
    }


@dataclasses.dataclass
class EnvProfile:
    env_id: str
    display_name: str = ""
    description: str = ""
    yolo_classes: list = dataclasses.field(default_factory=list)
    yolo_model_path: str = ""
    skill_library: list = dataclasses.field(default_factory=list)
    action_space: dict = dataclasses.field(default_factory=default_action_space)
    reward_signals: dict = dataclasses.field(default_factory=dict)
    world_memory_path: str = ""
    created_at: float = dataclasses.field(default_factory=time.time)
    last_seen: float = dataclasses.field(default_factory=time.time)
    session_count: int = 0
    # True until enough labeled frames/skills accumulate that this stops
    # being a bare skeleton (see core/label_queue.py) — surfaced so callers
    # can e.g. weight exploration higher or avoid trusting yolo_classes yet.
    bootstrap: bool = True

    @property
    def dir(self) -> str:
        return os.path.join(PROFILES_DIR, self.env_id)

    @property
    def path(self) -> str:
        return os.path.join(self.dir, "profile.json")

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EnvProfile":
        field_names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in field_names})

    def save(self) -> None:
        os.makedirs(self.dir, exist_ok=True)
        try:
            with open(self.path, 'w') as f:
                json.dump(self.to_dict(), f, indent=2)
        except OSError as e:
            print(f'[ENV_PROFILE] save error for {self.env_id}: {e}')

    def touch(self) -> None:
        """Call once per boot when this profile is loaded for a new
        session — bumps last_seen/session_count and persists."""
        self.last_seen = time.time()
        self.session_count += 1
        self.save()

    def effective_yolo_model(self, fallback: str) -> str:
        """yolo_model_path if one's been fine-tuned for this env yet,
        else `fallback` (typically config.YOLO_MODEL)."""
        return self.yolo_model_path if self.yolo_model_path else fallback


def load(env_id: str) -> EnvProfile | None:
    path = os.path.join(PROFILES_DIR, env_id, "profile.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return EnvProfile.from_dict(data)
    except (OSError, json.JSONDecodeError) as e:
        print(f'[ENV_PROFILE] load error for {env_id}: {e}')
        return None


def list_profiles() -> list:
    """env_ids of every profile on disk, sorted."""
    if not os.path.isdir(PROFILES_DIR):
        return []
    return sorted(
        d for d in os.listdir(PROFILES_DIR)
        if os.path.isfile(os.path.join(PROFILES_DIR, d, "profile.json"))
    )


def create(env_id: str, display_name: str = "", description: str = "") -> EnvProfile:
    """New profile skeleton for a never-before-seen environment. Starts in
    bootstrap mode: no fine-tuned YOLO weights, no env-specific skills —
    just the generic action space and the universal skill category (see
    core/skill_transfer.py) until core/label_queue.py accumulates enough
    labeled frames to fine-tune a detector for it."""
    profile = EnvProfile(
        env_id=env_id,
        display_name=display_name or env_id,
        description=description,
        skill_library=["universal"],
        world_memory_path=os.path.join(PROFILES_DIR, env_id, "world_memory.json"),
        bootstrap=True,
    )
    profile.save()
    print(f'[ENV_PROFILE] created new profile: {env_id}')
    return profile


def get_or_create(env_id: str, display_name: str = "", description: str = "") -> EnvProfile:
    existing = load(env_id)
    if existing:
        return existing
    return create(env_id, display_name=display_name, description=description)


def match_env_type(env_type: str, threshold: float = _MATCH_THRESHOLD) -> str | None:
    """Fuzzy-match a free-text env_type (as reported by the vision LLM in
    core/env_detector.py) against known profiles' env_id/display_name.
    Returns the matching env_id, or None if nothing clears `threshold`
    (Jaccard token overlap) — the caller should mint a new profile in that
    case rather than force a bad match.

    An exact slug match (e.g. "Minecraft" -> "minecraft") always wins
    regardless of threshold, since stopword stripping can otherwise starve
    the token set for short single-word env types.
    """
    slug = _slugify(env_type)
    known = list_profiles()
    if slug in known:
        return slug

    query = _tokens(env_type)
    if not query:
        return None

    best_id, best_score = None, 0.0
    for candidate_id in known:
        profile = load(candidate_id)
        if not profile:
            continue
        candidate_tokens = _tokens(candidate_id) | _tokens(profile.display_name)
        if not candidate_tokens:
            continue
        overlap = query & candidate_tokens
        union = query | candidate_tokens
        score = len(overlap) / len(union) if union else 0.0
        if score > best_score:
            best_score, best_id = score, candidate_id

    return best_id if best_score >= threshold else None


def env_id_from_type(env_type: str) -> str:
    return _slugify(env_type)
