# ╔══════════════════════════════════════════════════════╗
# ║  Mastermind — Drone/Agent Type Registry               ║
# ║  What kinds of bodies can join the hive, and what     ║
# ║  each one is good for.                                ║
# ╚══════════════════════════════════════════════════════╝
#
# An agent's "type" here is independent of its `env` (config.ACTIVE_ENV,
# e.g. "minecraft"/"driving"/"robocar") — env is *what world it acts in*,
# type is *what kind of body it has*. A robocar env adapter always reports
# type "ground", but nothing stops a future air env from also reporting
# "ground" if it's e.g. a tethered blimp. The coordinator uses type to
# decide which physical capabilities an agent has (fly/dive/steer) when
# matching goals to bodies; it uses env to decide which adapter/action
# space the agent is actually running.

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentType:
    name: str
    capabilities: frozenset
    preferred_goals: tuple


AGENT_TYPES = {
    "ground": AgentType(
        name="ground",
        capabilities=frozenset({"move", "mine", "build", "inventory", "melee"}),
        preferred_goals=("mine_ore", "explore", "gather", "build", "return_to_base"),
    ),
    "air": AgentType(
        name="air",
        capabilities=frozenset({"fly", "hover", "scout", "camera"}),
        preferred_goals=("scout_area", "map_region", "search_target", "relay_position"),
    ),
    "sea": AgentType(
        name="sea",
        capabilities=frozenset({"float", "dive", "camera", "sonar"}),
        preferred_goals=("scout_area", "search_target", "map_region"),
    ),
    "robocar": AgentType(
        name="robocar",
        capabilities=frozenset({"drive", "camera", "obstacle_avoid"}),
        preferred_goals=("patrol", "scout_area", "return_to_base", "follow_waypoint"),
    ),
}


def get_agent_type(name: str) -> AgentType:
    """Look up an AgentType by name, raising KeyError with available names
    listed if `name` isn't registered."""
    if name not in AGENT_TYPES:
        raise KeyError(f"unknown agent type '{name}' — available: {sorted(AGENT_TYPES)}")
    return AGENT_TYPES[name]


def capable_of(agent_type_name: str, capability: str) -> bool:
    t = AGENT_TYPES.get(agent_type_name)
    return bool(t and capability in t.capabilities)


def agents_for_goal(goal_type: str) -> list:
    """Return the names of agent types that prefer `goal_type`, ordered as
    declared above (ground body types first)."""
    return [name for name, t in AGENT_TYPES.items() if goal_type in t.preferred_goals]


# env_name (config.ACTIVE_ENV / EnvironmentAdapter.ENV_NAME) -> default
# agent type, used when an agent client doesn't explicitly declare one.
ENV_TO_DEFAULT_TYPE = {
    "minecraft": "ground",
    "fallout76": "ground",
    "driving": "robocar",
    "robocar": "robocar",
}


def default_type_for_env(env_name: str) -> str:
    return ENV_TO_DEFAULT_TYPE.get(env_name, "ground")
