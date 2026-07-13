# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Neural / Rule-Based Policy Blender        ║
# ╚══════════════════════════════════════════════════════╝
"""
Merges core/neural_policy.NeuralPolicy's suggestion with the rule-based
decision core/runtime.py already produces (skill replay / FSM / LLM). The
net never overrides an active skill and only fills in when the rule-based
side has nothing to say and the net itself is confident — see blend().
"""

import random

NEURAL_CONF_THRESHOLD = 0.8

# Curriculum-stage -> neural weight. As training episodes accumulate
# (tracked by core/rl_trainer.RLTrainer.episode_count) the blender leans on
# the net more even when a rule_action exists, mirroring the way
# core/curriculum.py escalates goal difficulty over a session rather than
# handing over control in one step.
_STAGE_WEIGHTS = [
    (0,      0.0),
    (5_000,  0.3),
    (20_000, 0.5),
    (50_000, 0.7),
]


def neural_weight_for_episodes(episode_count: int) -> float:
    """Monotonic step schedule — highest threshold reached wins."""
    weight = 0.0
    for threshold, w in _STAGE_WEIGHTS:
        if episode_count >= threshold:
            weight = w
    return weight


def blend(neural_action: dict | None, rule_action: dict | None,
          confidence: float, skill_active: bool = False,
          episode_count: int = 0) -> dict | None:
    """Returns the action dict to execute this tick, or None if neither
    side has anything (caller should fall back to its own idle/carry logic).

    neural_action: the 'action_dict' produced by NeuralPolicy.select_action(),
        or None if the neural policy wasn't queried this tick.
    rule_action:   the skill/FSM-derived action dict, or None if no skill
        matched and no rule fired.
    confidence:    neural_action's own confidence (0-1), passed separately
        so callers can gate without unpacking neural_action.
    skill_active:  True while a learned skill replay is mid-execution —
        rule always wins here regardless of neural confidence.
    episode_count: cumulative PPO training transitions/episodes so far,
        drives the curriculum-stage weight schedule above.
    """
    if skill_active:
        return rule_action

    if rule_action is None:
        if neural_action is not None and confidence > NEURAL_CONF_THRESHOLD:
            return neural_action
        return None

    if neural_action is None or confidence <= NEURAL_CONF_THRESHOLD:
        return rule_action

    # Both sides have something and the net is confident — hand over
    # control with probability equal to the current curriculum-stage
    # weight, so the rule-based path stays dominant until the net has
    # actually earned trust through logged training.
    weight = neural_weight_for_episodes(episode_count)
    if weight > 0 and random.random() < weight:
        return neural_action

    return rule_action
