# Backward-compat re-export — EpisodeMemory was consolidated into
# memory/episodic.py on 2026-08-14.  Import from there going forward:
#   from memory.episodic import EpisodeMemory
# This shim keeps ``from core.episode_memory import EpisodeMemory`` working.
from memory.episodic import EpisodeMemory  # noqa: F401

__all__ = ['EpisodeMemory']
