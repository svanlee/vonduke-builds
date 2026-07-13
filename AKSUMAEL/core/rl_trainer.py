# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Online PPO Trainer for NeuralPolicy       ║
# ╚══════════════════════════════════════════════════════╝
"""
Collects (obs, goal, action, log_prob, value, reward) transitions from the
running tick loop (core/runtime.py calls record() once per tick the neural
policy acted, and tick() once per tick unconditionally) and runs a PPO
update on a background daemon thread every config.RL_TRAIN_EVERY_N_TICKS
ticks, so training never blocks the main loop. Checkpoints the policy to
config.NEURAL_POLICY_PATH after each update.
"""

import threading

import config
from core.neural_policy import NeuralPolicy

GAMMA      = 0.99
GAE_LAMBDA = 0.95
CLIP_EPS   = 0.2
PPO_EPOCHS = 4
LR         = 3e-4
MAX_BUFFER = 4000   # hard cap so a stalled trainer thread can't grow unbounded


class _Transition:
    __slots__ = ('obs', 'goal', 'action_idx', 'log_prob', 'value', 'reward')

    def __init__(self, obs, goal, action_idx, log_prob, value, reward):
        self.obs        = obs
        self.goal       = goal
        self.action_idx = action_idx
        self.log_prob   = log_prob
        self.value      = value
        self.reward     = reward


class RLTrainer:
    """Background PPO trainer for a single NeuralPolicy instance."""

    def __init__(self, policy: NeuralPolicy):
        self.policy = policy
        self.episode_count = 0   # cumulative transitions trained on

        self._buffer: list[_Transition] = []
        self._lock = threading.Lock()
        self._tick_count = 0
        self._pending_train = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                         name='RLTrainer')
        self._thread.start()

    # ── Public API (called from the main tick loop) ────────────
    def record(self, obs_features, goal_embedding, action_idx: int,
               log_prob: float, value: float, reward: float):
        """Log one (state, action, reward) transition. Cheap — just a list
        append under a lock, safe to call every tick the neural policy acts."""
        with self._lock:
            self._buffer.append(_Transition(
                list(obs_features), list(goal_embedding), action_idx, log_prob, value, reward))
            if len(self._buffer) > MAX_BUFFER:
                self._buffer = self._buffer[-MAX_BUFFER:]

    def tick(self):
        """Call once per main-loop tick regardless of whether the neural
        policy acted. Every config.RL_TRAIN_EVERY_N_TICKS ticks, wakes the
        background thread to run a PPO update on whatever is buffered."""
        self._tick_count += 1
        if self._tick_count % config.RL_TRAIN_EVERY_N_TICKS == 0:
            self._pending_train.set()

    def stop(self):
        self._stop.set()
        self._pending_train.set()
        self._thread.join(timeout=5)

    # ── Background thread ────────────────────────────────────────
    def _run(self):
        while not self._stop.is_set():
            self._pending_train.wait(timeout=5)
            self._pending_train.clear()
            if self._stop.is_set():
                break
            try:
                self._train_step()
            except Exception as e:
                print(f'[RL_TRAINER] PPO update error: {e}')

    def _train_step(self):
        with self._lock:
            batch = self._buffer
            self._buffer = []
        if len(batch) < 8:
            return

        import torch
        import torch.nn.functional as F

        net, value_head = self.policy.net, self.policy.value_head
        device = self.policy.device

        obs_in = torch.tensor([t.obs + t.goal for t in batch],
                               dtype=torch.float32, device=device)
        actions = torch.tensor([t.action_idx for t in batch],
                                dtype=torch.long, device=device)
        old_log_probs = torch.tensor([t.log_prob for t in batch],
                                      dtype=torch.float32, device=device)
        values  = [t.value for t in batch]
        rewards = [t.reward for t in batch]

        returns, advantages = _compute_gae(rewards, values)
        returns    = torch.tensor(returns, dtype=torch.float32, device=device)
        advantages = torch.tensor(advantages, dtype=torch.float32, device=device)
        advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)

        params = list(net.parameters()) + list(value_head.parameters())
        optimizer = torch.optim.Adam(params, lr=LR)

        loss = None
        for _ in range(PPO_EPOCHS):
            logits = net(obs_in)
            dist = torch.distributions.Categorical(logits=logits)
            new_log_probs = dist.log_prob(actions)
            entropy = dist.entropy().mean()

            ratio = torch.exp(new_log_probs - old_log_probs)
            surr1 = ratio * advantages
            surr2 = torch.clamp(ratio, 1 - CLIP_EPS, 1 + CLIP_EPS) * advantages
            policy_loss = -torch.min(surr1, surr2).mean()

            new_values = value_head(obs_in).squeeze(-1)
            value_loss = F.mse_loss(new_values, returns)

            loss = policy_loss + 0.5 * value_loss - 0.01 * entropy

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

        self.episode_count += len(batch)
        self.policy.save_checkpoint()
        print(f'[RL_TRAINER] PPO update: {len(batch)} transitions, '
              f'loss={loss.item():.4f}, total_episodes={self.episode_count}')


def _compute_gae(rewards: list, values: list):
    """Generalized Advantage Estimation over the collected window, treated
    as one continuous rollout (the tick loop doesn't currently mark
    episode boundaries at this granularity)."""
    advantages = [0.0] * len(rewards)
    last_adv = 0.0
    next_value = 0.0
    for t in reversed(range(len(rewards))):
        delta = rewards[t] + GAMMA * next_value - values[t]
        last_adv = delta + GAMMA * GAE_LAMBDA * last_adv
        advantages[t] = last_adv
        next_value = values[t]
    returns = [a + v for a, v in zip(advantages, values)]
    return returns, advantages
