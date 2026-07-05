# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Reward System                      ║
# ║  Vision + audio + manual reward signals             ║
# ╚══════════════════════════════════════════════════════╝

import config


class RewardSystem:
    def __init__(self):
        self.total   = 0.0
        self.ticks   = 0
        self._ema    = 0.0          # exponential moving average
        self._decay  = config.REWARD_DECAY
        self._manual = 0.0         # accumulated manual reward this tick
        self._audio  = 0.0         # accumulated audio reward this tick

    def compute(self, state: dict, action_result: dict) -> float:
        """
        Called once per tick. Combines all reward sources.
        Returns the total reward for this tick.
        """
        r = 0.0

        # Vision reward: seeing objects = agent is active
        if state.get('objects'):
            r += 0.2

        # Confidence reward: high confidence decisions
        conf = action_result.get('confidence', 0)
        r += conf * 0.5

        # Penalty for waiting (low-activity signal)
        if action_result.get('action') == 'wait' and not self._manual:
            r -= 0.1

        # Incorporate accumulated signals from this tick
        r += self._manual
        r += self._audio

        # Reset per-tick accumulators
        self._manual = 0.0
        self._audio  = 0.0

        r = round(r, 3)
        self.total += r
        self.ticks += 1
        # EMA update
        self._ema = self._decay * self._ema + (1 - self._decay) * r

        return r

    def add_manual(self, value: float):
        """User-provided reward signal (joystick A/B or voice)."""
        self._manual += value

    def add_audio_reward(self, value: float):
        """Audio event reward signal from GameEar."""
        self._audio += value

    def average(self) -> float:
        return round(self.total / self.ticks, 3) if self.ticks else 0.0

    def ema(self) -> float:
        """Exponential moving average — more responsive than cumulative avg."""
        return round(self._ema, 3)
