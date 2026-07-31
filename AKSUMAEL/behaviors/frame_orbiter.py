# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — Frame Orbiter                             ║
# ║                                                       ║
# ║  Collects 10-15 labeled training frames of a target   ║
# ║  object by rotating the camera through multiple       ║
# ║  angles while the bot stands still.                   ║
# ║                                                       ║
# ║  Called by the curriculum supervisor in runtime.py    ║
# ║  every ORBIT_INTERVAL_SEC when a target is visible.  ║
# ╚══════════════════════════════════════════════════════╝

import time
import config

# Camera sweep positions (in units of config.LOOK_SCAN_STEP px).
# 5 horizontal positions × 2 vertical angles = 10 frames minimum.
# Sweep: far-left → left → center → right → far-right
_H_STEPS = [-6, -3, 0, 3, 6]
_V_OFFSETS = [0, -2]   # level, slightly up (catches tall objects like trees)

# Additional frames: strafe slightly left/right while centered (simulates
# parallax and gives the model side-profile views of the same object).
_STRAFE_KEYS = ['a', None, 'd']   # left, center, right

# Minimum seconds between orbit runs (per label). Prevents the same object
# from filling the dataset when the bot is standing still in front of it.
_ORBIT_COOLDOWN_SEC = 120.0

# Per-label last-orbit timestamp (in-memory — resets on restart, which is
# fine since the 120s cooldown is short enough that missing one run on
# restart doesn't matter).
_last_orbit: dict[str, float] = {}


class FrameOrbiter:
    """
    Multi-angle frame collector. Call run(target_label, objects, frame)
    every ORBIT_INTERVAL_SEC when a target label is detected.

    Returns the number of frames saved.
    """

    def __init__(self, collector, executor, capture_fn=None, auto_trainer=None):
        self._collector    = collector
        self._executor     = executor
        self._capture      = capture_fn   # () -> fresh BGR frame
        self._auto_trainer = auto_trainer
        self._active       = False

    def cooldown_ok(self, label: str) -> bool:
        return time.time() - _last_orbit.get(label, 0.0) >= _ORBIT_COOLDOWN_SEC

    def run(self, target_label: str, objects: list, frame) -> int:
        """
        Sweep the camera through 10+ positions, saving frames that contain
        target_label. Returns number of frames saved.
        """
        if not self._collector:
            return 0
        if self._active:
            return 0

        self._active = True
        _last_orbit[target_label] = time.time()
        saved = 0
        current_h = 0   # track cumulative horizontal displacement (px)

        print(f'[ORBITER] starting orbit for "{target_label}" — '
              f'sweeping {len(_H_STEPS)*len(_V_OFFSETS) + len(_STRAFE_KEYS)} positions')

        try:
            # ── Phase 1: camera sweep (horizontal × vertical) ──────────────
            for v_offset in _V_OFFSETS:
                for h_mult in _H_STEPS:
                    target_h = h_mult * config.LOOK_SCAN_STEP
                    delta_h  = target_h - current_h

                    if delta_h != 0 or v_offset != 0:
                        self._executor.execute({
                            'look':   {'dx': delta_h, 'dy': v_offset},
                            'source': 'orbiter_sweep',
                        })
                        current_h = target_h
                        time.sleep(0.15)   # let frame settle

                    shot = self._fresh_frame(frame)
                    shot_objects = self._current_objects(objects)

                    if self._has_target(shot_objects, target_label):
                        if self._collector.force_save(shot, shot_objects):
                            saved += 1
                            print(f'[ORBITER] frame {saved} saved '
                                  f'(h={h_mult:+d} v={v_offset:+d})')

            # Return to center after horizontal sweep
            if current_h != 0:
                self._executor.execute({
                    'look': {'dx': -current_h, 'dy': 0},
                    'source': 'orbiter_recenter',
                })
                time.sleep(0.15)
                current_h = 0

            # ── Phase 2: strafe parallax frames ────────────────────────────
            for key in _STRAFE_KEYS:
                if key:
                    self._executor.execute({
                        'key':    key,
                        'source': 'orbiter_strafe',
                    })
                    time.sleep(0.3)

                shot = self._fresh_frame(frame)
                shot_objects = self._current_objects(objects)

                if self._has_target(shot_objects, target_label):
                    if self._collector.force_save(shot, shot_objects):
                        saved += 1
                        print(f'[ORBITER] frame {saved} saved (strafe={key!r})')

            # Return to center strafe position (from 'd' offset)
            # The last _STRAFE_KEYS entry is 'd', so press 'a' to cancel
            self._executor.execute({
                'key':    'a',
                'source': 'orbiter_strafe_recenter',
            })
            time.sleep(0.2)

        except Exception as e:
            print(f'[ORBITER] error during orbit of "{target_label}": {e}')
        finally:
            self._active = False

        print(f'[ORBITER] done — {saved} frames collected for "{target_label}"')

        # Notify auto_trainer so it can decide whether to trigger a mini-train
        if saved > 0 and self._auto_trainer:
            self._auto_trainer.on_survey_saved(saved)
            if self._auto_trainer.should_train():
                self._auto_trainer.start_training()

        return saved

    # ── helpers ────────────────────────────────────────────────────────────

    def _fresh_frame(self, fallback):
        if self._capture:
            f = self._capture()
            if f is not None:
                return f
        return fallback

    def _current_objects(self, fallback):
        """Try to get the very latest pipeline objects. If capture_fn is
        wired to pipeline.latest_raw_frame, the objects will be updated by
        the YOLO thread automatically — but we have no direct reference to
        the pipeline here, so we fall back to the snapshot passed in."""
        return fallback

    def _has_target(self, objects: list, target_label: str) -> bool:
        return any(o.get('label') == target_label for o in objects)


# ── Curriculum target picker ───────────────────────────────────────────────

# Labels we especially want more training data for — checked first.
PRIORITY_LABELS = [
    'lava',        # bootstrap — color detector seeds first frames
    'diamond_ore', 'emerald_ore', 'iron_ore', 'gold_ore', 'coal_ore',
    'lapis_ore', 'copper_ore', 'villager', 'village_house',
]


def pick_orbit_target(objects: list) -> str | None:
    """
    Given the current list of detected objects, pick the best label to orbit.

    Priority:
      1. High-value labels in PRIORITY_LABELS (sorted by their priority order)
      2. Any object with the lowest detection confidence (most uncertain)
      3. None if no objects or all are HUD elements
    """
    if not objects:
        return None

    # Filter out HUD elements — orbiting them is meaningless
    _HUD = {'health_bar', 'hunger_bar', 'armor_bar', 'xp_bar', 'hotbar',
            'crosshair', 'chest_row'}
    candidates = [o for o in objects if o.get('label') not in _HUD]
    if not candidates:
        return None

    labels = {o['label'] for o in candidates}

    # Priority pick
    for lbl in PRIORITY_LABELS:
        if lbl in labels:
            return lbl

    # Lowest-confidence pick (most uncertain — most valuable to reinforce)
    lowest = min(candidates, key=lambda o: o.get('conf', 1.0))
    return lowest.get('label')
