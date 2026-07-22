# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — LeRobotDataset Episode Recorder    ║
# ╚══════════════════════════════════════════════════════╝
#
# Passive (observation, action) capture for offline behavioral cloning
# (ACT, Diffusion Policy) and RL fine-tuning. Hooks into the existing tick
# loop read-only — never influences what AKSUMAEL actually does in-game.
# Writes LeRobotDataset-style episodes under config.LEROBOT_DATA_DIR:
#   episodes/episode_NNNNNN.parquet   — per-tick observation/action rows
#   videos/episode_NNNNNN/frame_*.jpg — raw frames referenced by each row
#   meta/info.json                    — cumulative dataset metadata

import json
import os
import time

import cv2
import numpy as np
import pandas as pd

import config

# Order fixes the one-hot layout used by every recorded action vector —
# changing it would silently reinterpret every previously recorded episode.
KEYS = ['w', 'a', 's', 'd', 'space', 'shift', 'ctrl', 'e', 'q',
        '1', '2', '3', '4', '5', '6', '7', '8', '9',
        'f', 'r', 't', 'escape']
_KEY_INDEX = {k: i for i, k in enumerate(KEYS)}

STATE_DIM  = 6                 # y_level, hunger, health, wood_count, facing_yaw, facing_pitch
ACTION_DIM = len(KEYS) + 4     # + mouse_dx, mouse_dy, left_click, right_click

# F3's cardinal `facing` string is the only heading AKSUMAEL tracks — approximate
# Minecraft yaw (deg) for each so observation.state has a continuous heading value.
_FACING_YAW_DEG = {'south': 0.0, 'west': 90.0, 'north': 180.0, 'east': 270.0}


def encode_action(action_dict):
    """Flatten an executor.execute()-style action dict into a fixed float32 vector."""
    action_dict = action_dict or {}
    vec = np.zeros(ACTION_DIM, dtype=np.float32)

    key = action_dict.get('key')
    idx = _KEY_INDEX.get(key.lower()) if isinstance(key, str) else None
    if idx is not None:
        vec[idx] = 1.0

    look = action_dict.get('look') or {}
    dx = max(-500.0, min(500.0, float(look.get('dx', 0) or 0)))
    dy = max(-500.0, min(500.0, float(look.get('dy', 0) or 0)))
    vec[len(KEYS)] = dx / 500.0
    vec[len(KEYS) + 1] = dy / 500.0

    # Real actions use 'button'/'mouse_button_name' ('left'/'right'); 'click' is
    # normally [x_pct, y_pct] but is checked too in case it's ever a button name.
    button = action_dict.get('button') or action_dict.get('mouse_button_name') \
        or action_dict.get('click')
    button = button.lower() if isinstance(button, str) else ''
    vec[len(KEYS) + 2] = 1.0 if button == 'left' else 0.0
    vec[len(KEYS) + 3] = 1.0 if button == 'right' else 0.0
    return vec


def encode_state(world_mem, inventory):
    """Build the observation.state vector from WorldMemory + InventoryTracker."""
    y_level = float(getattr(world_mem, 'y_level', 0) or 0)
    hunger  = float(getattr(world_mem, 'hunger_pct', 1.0) or 0.0)
    health  = float(getattr(world_mem, 'health_pct', 1.0) or 0.0)
    wood    = float(inventory.wood_count()) if inventory is not None else 0.0
    yaw     = _FACING_YAW_DEG.get(getattr(world_mem, 'facing', None), 0.0)
    pitch   = float(getattr(world_mem, 'cumulative_pitch_dy', 0) or 0.0)
    return np.array([y_level, hunger, health, wood, yaw, pitch], dtype=np.float32)


class LeRobotRecorder:
    def __init__(self, data_dir=None, max_episode_ticks=None):
        self.data_dir          = data_dir or config.LEROBOT_DATA_DIR
        self.max_episode_ticks = max_episode_ticks or config.LEROBOT_MAX_EPISODE_TICKS
        self.episodes_dir = os.path.join(self.data_dir, 'episodes')
        self.videos_dir   = os.path.join(self.data_dir, 'videos')
        self.meta_dir     = os.path.join(self.data_dir, 'meta')
        for d in (self.episodes_dir, self.videos_dir, self.meta_dir):
            os.makedirs(d, exist_ok=True)

        self.episode_index = self._next_episode_index()
        self._goal          = None   # goal active when the current episode started
        self._rows           = []
        self._frame_index    = 0
        self._episode_start  = None
        self._episode_dir    = None

    def _next_episode_index(self):
        existing = [f for f in os.listdir(self.episodes_dir) if f.endswith('.parquet')]
        if not existing:
            return 0
        nums = [int(f[len('episode_'):-len('.parquet')]) for f in existing]
        return max(nums) + 1

    def _open_episode(self, goal):
        self._goal          = goal
        self._rows           = []
        self._frame_index    = 0
        self._episode_start  = time.time()
        self._episode_dir    = os.path.join(
            self.videos_dir, f'episode_{self.episode_index:06d}')
        os.makedirs(self._episode_dir, exist_ok=True)

    def record_step(self, frame, world_mem, action_dict, inventory=None, goal=None):
        """Call once per tick with the frame/state/action already computed
        by the tick loop. Starts a new episode on the first call, or when
        `goal` changes, or after LEROBOT_MAX_EPISODE_TICKS ticks."""
        if frame is None:
            return

        if self._episode_start is None:
            self._open_episode(goal)
        elif goal != self._goal and self._rows:
            self.finalize_episode()
            self._open_episode(goal)

        frame_path = os.path.join(self._episode_dir, f'frame_{self._frame_index:06d}.jpg')
        cv2.imwrite(frame_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])

        state_vec  = encode_state(world_mem, inventory)
        action_vec = encode_action(action_dict)

        self._rows.append({
            'observation.image': os.path.relpath(frame_path, self.data_dir),
            'observation.state': state_vec.tolist(),
            'action':            action_vec.tolist(),
            'episode_index':     self.episode_index,
            'frame_index':       self._frame_index,
            'timestamp':         time.time() - self._episode_start,
            'next.done':         False,
        })
        self._frame_index += 1

        if self._frame_index >= self.max_episode_ticks:
            self.finalize_episode()

    def finalize_episode(self):
        """Write the buffered rows to parquet and update meta/info.json.
        No-op if the current episode has no recorded steps."""
        if not self._rows:
            return

        self._rows[-1]['next.done'] = True
        df = pd.DataFrame(self._rows)
        out_path = os.path.join(
            self.episodes_dir, f'episode_{self.episode_index:06d}.parquet')
        df.to_parquet(out_path, index=False)

        self._update_meta(num_frames=len(self._rows))

        self.episode_index += 1
        self._rows          = []
        self._frame_index    = 0
        self._episode_start  = None
        self._episode_dir    = None

    def _update_meta(self, num_frames):
        meta_path = os.path.join(self.meta_dir, 'info.json')
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
        else:
            meta = {
                'total_episodes': 0,
                'total_frames':   0,
                'fps':            round(1.0 / config.LOOP_INTERVAL_SEC, 2),
                'state_dim':      STATE_DIM,
                'action_dim':     ACTION_DIM,
                'keys':           KEYS,
            }
        meta['total_episodes'] = self.episode_index + 1
        meta['total_frames']   = meta.get('total_frames', 0) + num_frames
        with open(meta_path, 'w') as f:
            json.dump(meta, f, indent=2)

    def close(self):
        """Flush any in-progress episode — call on clean shutdown."""
        self.finalize_episode()
