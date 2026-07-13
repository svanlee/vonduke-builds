# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Neural Policy Backbone                    ║
# ║  Low-level learned action policy (PPO-trained)        ║
# ╚══════════════════════════════════════════════════════╝
"""
NeuralPolicy — hybrid neural-symbolic low-level control.

The existing BDI/HTN/curriculum stack (core/planner.py, core/curriculum.py,
memory/goals.py) stays the high-level "what to do" layer unchanged. This
network only answers the low-level "which atomic action right now", given
a YOLO-derived feature vector and the current goal. Its output is blended
with the rule-based decision by core/policy_blender.py — it never drives
the game unilaterally, and is off unless config.NEURAL_POLICY_ENABLED.

TODO(vision-encoder): the obs vector is currently built entirely from YOLO
detections (core/feature_extractor.py) — a fixed, lossy view of the frame.
Once there's enough logged (frame, action, reward) data to justify it,
replace/augment that with a small CNN encoder over the raw capture frame
so the policy can see things YOLO's fixed class list misses.
"""

import os

import config

# Discrete action space the policy chooses over. Maps 1:1 to
# action_to_dict() below, which produces the same shape of action dict
# core/runtime.py already feeds to ActionExecutor.
ACTION_SPACE = [
    'w', 'a', 's', 'd', 'space', 'ctrl', 'shift', 'e', 'f', 'q', 'esc',
    'look_left', 'look_right', 'look_up', 'look_down',
    'click_left', 'click_right', 'noop',
]

_torch = None
_nn = None


def _ensure_torch() -> bool:
    global _torch, _nn
    if _torch is not None:
        return True
    try:
        import torch
        import torch.nn as nn
        _torch, _nn = torch, nn
        return True
    except ImportError:
        return False


def action_to_dict(action_name: str) -> dict:
    """Maps a discrete action name to an executor-shaped action dict."""
    if action_name in ('w', 'a', 's', 'd', 'space', 'ctrl', 'shift', 'e', 'f', 'q', 'esc'):
        return {'key': action_name}
    if action_name == 'look_left':
        return {'look': {'dx': -config.LOOK_SENSITIVITY, 'dy': 0}}
    if action_name == 'look_right':
        return {'look': {'dx': config.LOOK_SENSITIVITY, 'dy': 0}}
    if action_name == 'look_up':
        return {'look': {'dx': 0, 'dy': -config.LOOK_SENSITIVITY}}
    if action_name == 'look_down':
        return {'look': {'dx': 0, 'dy': config.LOOK_SENSITIVITY}}
    if action_name == 'click_left':
        return {'click': 'left'}
    if action_name == 'click_right':
        return {'click': 'right'}
    return {}   # noop


class NeuralPolicy:
    """3-layer MLP: (obs_features ++ goal_embedding) -> action logits.

    Small enough (256 hidden) to run at 4Hz on CPU when the GPU is busy
    with YOLO inference. A separate value head (same input, no shared
    weights) supports the PPO trainer in core/rl_trainer.py.
    """

    HIDDEN = 256

    def __init__(self, obs_dim: int, goal_dim: int, action_dim: int = None,
                 checkpoint_path: str = None):
        if not _ensure_torch():
            raise RuntimeError('NeuralPolicy requires PyTorch (pip install torch)')
        self.obs_dim  = obs_dim
        self.goal_dim = goal_dim
        self.action_dim = action_dim or len(ACTION_SPACE)
        self.checkpoint_path = checkpoint_path or config.NEURAL_POLICY_PATH
        self.device = _torch.device('cuda' if _torch.cuda.is_available() else 'cpu')

        in_dim = obs_dim + goal_dim
        self.net = _nn.Sequential(
            _nn.Linear(in_dim, self.HIDDEN),
            _nn.ReLU(),
            _nn.Linear(self.HIDDEN, self.HIDDEN),
            _nn.ReLU(),
            _nn.Linear(self.HIDDEN, self.action_dim),
        ).to(self.device)

        self.value_head = _nn.Sequential(
            _nn.Linear(in_dim, self.HIDDEN),
            _nn.ReLU(),
            _nn.Linear(self.HIDDEN, 1),
        ).to(self.device)

        self._load_checkpoint()

    def _load_checkpoint(self):
        if self.checkpoint_path and os.path.exists(self.checkpoint_path):
            try:
                state = _torch.load(self.checkpoint_path, map_location=self.device)
                self.net.load_state_dict(state['net'])
                self.value_head.load_state_dict(state['value_head'])
                print(f'[NEURAL_POLICY] loaded checkpoint {self.checkpoint_path}')
            except Exception as e:
                print(f'[NEURAL_POLICY] checkpoint load failed: {e} — starting fresh')

    def save_checkpoint(self, path: str = None):
        path = path or self.checkpoint_path
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        _torch.save({'net': self.net.state_dict(),
                     'value_head': self.value_head.state_dict()}, path)

    def _input_tensor(self, obs_features, goal_embedding):
        return _torch.tensor(list(obs_features) + list(goal_embedding),
                              dtype=_torch.float32, device=self.device)

    def forward(self, obs_features, goal_embedding):
        """obs_features: list/array[obs_dim], goal_embedding: list/array[goal_dim].
        Returns raw action logits, shape [action_dim]."""
        return self.net(self._input_tensor(obs_features, goal_embedding))

    def value(self, obs_features, goal_embedding):
        """State-value estimate for PPO advantage computation."""
        return self.value_head(self._input_tensor(obs_features, goal_embedding))

    def select_action(self, obs_features, goal_embedding, temperature: float = 1.0) -> dict:
        """Samples an action from the policy distribution (softmax over
        logits, temperature-scaled). Returns a dict with everything the
        caller needs both to act and to log a training transition:
        {'action_idx', 'action_name', 'action_dict', 'confidence', 'log_prob'}
        """
        with _torch.no_grad():
            logits = self.forward(obs_features, goal_embedding)
            probs = _torch.softmax(logits / max(temperature, 1e-6), dim=-1)
            dist = _torch.distributions.Categorical(probs)
            idx = dist.sample()
            confidence = float(probs[idx].item())
            log_prob = float(dist.log_prob(idx).item())

        name = ACTION_SPACE[int(idx.item())]
        return {
            'action_idx':   int(idx.item()),
            'action_name':  name,
            'action_dict':  action_to_dict(name),
            'confidence':   confidence,
            'log_prob':     log_prob,
        }
