# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.2.0 — Environment Registry               ║
# ║  ENV_NAME -> EnvironmentAdapter class                 ║
# ╚══════════════════════════════════════════════════════╝
#
# Adapter modules are imported lazily inside get_adapter_class(), not at
# module load time — environments/robocar_env.py touches rclpy and
# environments/*_env.py all touch ActionExecutor/YOLODetector at import,
# and none of that should run just because something imported this
# registry to look up "minecraft".

import config

_REGISTRY = {}


def _load_registry():
    if _REGISTRY:
        return _REGISTRY

    from environments.minecraft_env import MinecraftEnv
    from environments.fallout76_env import Fallout76Env
    from environments.driving_env import DrivingEnv
    from environments.robocar_env import RobocarEnv

    _REGISTRY.update({
        MinecraftEnv.ENV_NAME: MinecraftEnv,
        Fallout76Env.ENV_NAME: Fallout76Env,
        DrivingEnv.ENV_NAME: DrivingEnv,
        RobocarEnv.ENV_NAME: RobocarEnv,
    })
    return _REGISTRY


def get_adapter_class(env_name: str = None):
    """Look up the EnvironmentAdapter subclass for `env_name` (defaults to
    config.ACTIVE_ENV). Raises KeyError with the available names listed if
    env_name isn't registered."""
    env_name = env_name or config.ACTIVE_ENV
    registry = _load_registry()
    if env_name not in registry:
        raise KeyError(
            f"unknown environment '{env_name}' — available: {sorted(registry)}"
        )
    return registry[env_name]


def create_adapter(env_name: str = None):
    """Instantiate the adapter for `env_name` (defaults to config.ACTIVE_ENV).
    Never raises for missing hardware/game — adapters mark themselves
    .available = False and stay usable as stubs; this only raises if
    `env_name` itself isn't a registered environment."""
    cls = get_adapter_class(env_name)
    return cls()


def available_envs() -> list:
    return sorted(_load_registry())
