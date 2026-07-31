# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Position Kalman Filter                    ║
# ║                                                        ║
# ║  1-D KF per axis (x, y, z) — constant-velocity model ║
# ║  State:       [pos, vel]                              ║
# ║  Process:     dead-reckon from keypresses + facing    ║
# ║  Measurement: F3 OCR position (noisy, ~1 Hz)         ║
# ║                                                        ║
# ║  Why: F3 OCR gives noisy readings (~50-block outliers ║
# ║  already filtered by world_memory.py).  This KF      ║
# ║  smooths between reads using movement priors and      ║
# ║  provides calibrated uncertainty estimates.           ║
# ╚══════════════════════════════════════════════════════╝

from __future__ import annotations
import math
import time
from typing import Optional

import numpy as np

# ── Minecraft movement constants ────────────────────────
# Walking speed in blocks/second (vanilla survival, flat ground).
# Running (sprint) is ~5.6 bps.  We use walking as the conservative prior.
MC_WALK_SPEED_BPS  = 4.317   # blocks per second
MC_SPRINT_SPEED_BPS = 5.612
MC_FALL_SPEED_BPS  = 0.0     # vertical — gravity handled separately
MC_TICKS_PER_SEC   = 20      # Minecraft game ticks

# Cardinal facing → unit vector in (x, z) plane
# Minecraft: +x = east, -x = west, +z = south, -z = north
_FACING_VECTOR = {
    'north': ( 0.0,  0.0, -1.0),  # (dx, dy, dz)
    'south': ( 0.0,  0.0,  1.0),
    'east':  ( 1.0,  0.0,  0.0),
    'west':  (-1.0,  0.0,  0.0),
}

# ── Noise parameters ────────────────────────────────────
# Process noise Q: uncertainty added per second of dead-reckoning.
# Larger → trust measurements more; smaller → trust model more.
Q_POS_PER_SEC = 0.5   # blocks²/s  (position process noise)
Q_VEL_PER_SEC = 1.0   # (blocks/s)²/s  (velocity process noise)

# Measurement noise R: OCR is typically ±1 block accurate when it works.
# Outliers are caught by world_memory.py's Y_JUMP_REJECT_LIMIT before
# reaching us, so we don't need heavy R here.
R_OCR = 1.5           # blocks² (1-sigma ≈ 1.2 blocks)


class AxisKF:
    """1-D Kalman filter for a single position axis.

    State vector: [position, velocity]
    """

    def __init__(self, initial_pos: float = 0.0):
        self.x = np.array([initial_pos, 0.0])     # [pos, vel]
        # Initial covariance: high uncertainty on velocity, low on pos
        self.P = np.diag([4.0, 25.0])

        self.F = np.eye(2)   # state transition (updated with dt each predict)
        self.H = np.array([[1.0, 0.0]])            # measurement selects pos
        self.R = np.array([[R_OCR]])

    def predict(self, dt: float, accel: float = 0.0):
        """Propagate state forward by dt seconds with optional acceleration."""
        self.F = np.array([[1.0, dt],
                           [0.0, 1.0]])
        B = np.array([0.5 * dt**2, dt])
        self.x = self.F @ self.x + B * accel

        # Process noise Q grows with dt
        q_p = Q_POS_PER_SEC * dt
        q_v = Q_VEL_PER_SEC * dt
        Q = np.diag([q_p, q_v])
        self.P = self.F @ self.P @ self.F.T + Q

    def update(self, measurement: float):
        """Correct state with an F3 OCR position measurement."""
        y  = measurement - self.H @ self.x          # innovation
        S  = self.H @ self.P @ self.H.T + self.R   # innovation covariance
        K  = self.P @ self.H.T @ np.linalg.inv(S)  # Kalman gain
        self.x = self.x + K @ y
        self.P = (np.eye(2) - K @ self.H) @ self.P

    @property
    def pos(self) -> float:
        return float(self.x[0])

    @property
    def vel(self) -> float:
        return float(self.x[1])

    @property
    def pos_uncertainty(self) -> float:
        """1-sigma position uncertainty in blocks."""
        return float(math.sqrt(max(self.P[0, 0], 0.0)))


class PositionKF:
    """3-axis Kalman filter for AKSUMAEL's Minecraft position.

    Usage:
        kf = PositionKF(x0=45.0, y0=65.0, z0=-10.0)

        # Each bot tick — predict using current movement intent
        kf.predict(dt=tick_dt, action=action_dict, facing='north',
                   sprinting=False)

        # When F3 OCR returns a valid reading
        kf.update_from_f3(x=45.3, y=65.0, z=-10.1)

        print(kf.position)   # smoothed (x, y, z) estimate
        print(kf.uncertainty) # (σx, σy, σz)
    """

    def __init__(self, x0: float = 0.0, y0: float = 64.0, z0: float = 0.0):
        self._kf = {
            'x': AxisKF(x0),
            'y': AxisKF(y0),
            'z': AxisKF(z0),
        }
        self._last_predict_ts: float = time.monotonic()
        self.initialized = False

    # ── public API ──────────────────────────────────────

    def predict(self,
                dt: Optional[float] = None,
                action: Optional[dict] = None,
                facing: Optional[str] = None,
                sprinting: bool = False):
        """Call every tick.  action is the action_dict from the executor."""
        now = time.monotonic()
        if dt is None:
            dt = now - self._last_predict_ts
        self._last_predict_ts = now

        dx, dy, dz = self._movement_delta(dt, action, facing, sprinting)
        # Convert block deltas to velocity priors for each axis
        # (acceleration = 0; we're setting velocity directly from intent)
        for axis, delta in (('x', dx), ('y', dy), ('z', dz)):
            kf = self._kf[axis]
            # Inject the intended velocity as a soft prior on velocity state
            if delta != 0.0:
                kf.x[1] = delta / max(dt, 1e-3)   # implied vel (blocks/s)
            kf.predict(dt)

    def update_from_f3(self, x: float, y: float, z: float):
        """Call when F3 OCR returns valid position data."""
        self._kf['x'].update(x)
        self._kf['y'].update(y)
        self._kf['z'].update(z)
        if not self.initialized:
            # Bootstrap: set state directly on first measurement
            for axis, val in (('x', x), ('y', y), ('z', z)):
                self._kf[axis].x[0] = val
            self.initialized = True

    @property
    def position(self) -> tuple[float, float, float]:
        return (self._kf['x'].pos,
                self._kf['y'].pos,
                self._kf['z'].pos)

    @property
    def uncertainty(self) -> tuple[float, float, float]:
        return (self._kf['x'].pos_uncertainty,
                self._kf['y'].pos_uncertainty,
                self._kf['z'].pos_uncertainty)

    @property
    def position_dict(self) -> dict:
        x, y, z = self.position
        sx, sy, sz = self.uncertainty
        return {'x': x, 'y': y, 'z': z,
                'sigma_x': sx, 'sigma_y': sy, 'sigma_z': sz}

    # ── private ─────────────────────────────────────────

    @staticmethod
    def _movement_delta(dt: float,
                        action: Optional[dict],
                        facing: Optional[str],
                        sprinting: bool) -> tuple[float, float, float]:
        """Convert an action_dict + facing into expected displacement (blocks)."""
        if action is None:
            return 0.0, 0.0, 0.0

        key = (action.get('key') or '').lower()
        gamepad = action.get('gamepad') or {}
        speed = MC_SPRINT_SPEED_BPS if sprinting else MC_WALK_SPEED_BPS

        # ── keyboard movement ───────────────────────────
        fwd = 0.0
        if key == 'w':
            fwd = speed * dt
        elif key == 's':
            fwd = -speed * dt * 0.3   # backward is slower

        # Strafe (a/d) — perpendicular to facing
        strafe = 0.0
        if key == 'a':
            strafe = -speed * dt * 0.6
        elif key == 'd':
            strafe =  speed * dt * 0.6

        # Vertical
        dy = 0.0
        if key == 'space':
            dy =  1.25 * dt   # rough jump apex
        elif key == 'shift':
            dy = -0.5 * dt    # sneak down

        # ── gamepad analog stick ────────────────────────
        lx = gamepad.get('lx', 0)   # left stick x (strafe)
        ly = gamepad.get('ly', 0)   # left stick y (forward)
        if abs(lx) > 0.1 or abs(ly) > 0.1:
            fwd    = -ly * speed * dt   # negative ly = forward in most mappings
            strafe =  lx * speed * dt

        # ── resolve facing → world-space displacement ───
        face_vec = _FACING_VECTOR.get(facing or '', (0.0, 0.0, 0.0))
        # Perpendicular (right) of facing for strafe
        # Rotate face_vec 90° clockwise around Y: (fx, 0, fz) → (fz, 0, -fx)
        fx, _, fz = face_vec
        right_vec = (fz, 0.0, -fx)

        dx = fwd * fx + strafe * right_vec[0]
        dz = fwd * fz + strafe * right_vec[2]

        return dx, dy, dz
