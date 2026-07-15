# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Auto Trainer                       ║
# ║  Background retraining triggered by frame accumulation║
# ╚══════════════════════════════════════════════════════╝
#
# AutoTrainer — background retraining triggered by frame accumulation.
# Spawns yolo_finetune.py train as a subprocess, then hot-reloads weights.

import subprocess, threading, time, os, sys, pathlib, json
import config
from memory.goals import GOALS_PATH

TRAIN_LOCK = pathlib.Path('/tmp/aksumael_training.lock')
PRESERVED_GOALS_PATH = 'data/preserved_goals.json'

# Goals that count as "nothing important in progress" — safe to interrupt
# without preserving. Mirrors the baseline states in memory.goals.GOAL_PRIORITIES.
_TRIVIAL_GOALS = {'idle', 'explore'}

GOAL_WAIT_TIMEOUT_SEC = 60
GOAL_WAIT_POLL_SEC    = 2


def _dataset_size() -> int:
    """Count total saved images in the yolo training dataset."""
    from tools.yolo_finetune import IMAGES_DIR
    total = 0
    for split in ('train', 'val'):
        d = f'{IMAGES_DIR}/{split}'
        if os.path.exists(d):
            total += len([f for f in os.listdir(d) if f.endswith('.jpg')])
    return total


# Cooldown applied after a failed training run — shorter than the normal
# post-success cooldown so a bad run doesn't get retried every 3 minutes,
# but still recovers faster than waiting a full hour.
FAILURE_COOLDOWN_SEC = 600


def _read_goal_state() -> dict:
    """Read GoalStack's on-disk state ({"current":..., "stack":[...]}).
    Reading the file rather than holding a live GoalStack reference keeps
    AutoTrainer decoupled from the runtime loop's object graph."""
    if not os.path.exists(GOALS_PATH):
        return {}
    try:
        with open(GOALS_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _goal_is_active(state: dict) -> bool:
    return state.get('current', 'idle') not in _TRIVIAL_GOALS


def _wait_for_goal_to_settle(timeout_sec: float = GOAL_WAIT_TIMEOUT_SEC,
                              poll_sec: float = GOAL_WAIT_POLL_SEC) -> dict:
    """Give an in-progress goal up to timeout_sec to finish (retire back to
    idle/explore) before we kill aksumael out from under it. Returns
    whatever goal state was last observed."""
    waited = 0.0
    state = _read_goal_state()
    while _goal_is_active(state) and waited < timeout_sec:
        time.sleep(poll_sec)
        waited += poll_sec
        state = _read_goal_state()
    return state


def _preserve_goal_state(state: dict):
    """Persist an in-progress goal (current + queued stack) to disk so
    core.runtime can re-inject it via the injected_goals.json mechanism
    once aksumael comes back up, instead of silently dropping it when
    autotrain stops the service mid-goal."""
    if not _goal_is_active(state):
        return
    try:
        os.makedirs(os.path.dirname(PRESERVED_GOALS_PATH) or '.', exist_ok=True)
        with open(PRESERVED_GOALS_PATH, 'w') as f:
            json.dump(state, f)
        print(f'[AUTOTRAIN] preserved in-progress goal "{state.get("current")}" '
              f'to {PRESERVED_GOALS_PATH}')
    except OSError as e:
        print(f'[AUTOTRAIN] failed to preserve goal state: {e}')


class AutoTrainer:
    def __init__(self, yolo_detector):
        self._yolo      = yolo_detector
        self._frames_since_train = 0
        self._last_train = 0.0
        self._training   = False
        self._lock       = threading.Lock()

    def on_survey_saved(self, n_frames: int = 1):
        """Call this each time survey saves a frame."""
        with self._lock:
            self._frames_since_train += n_frames

    @property
    def frames_since_train(self):
        with self._lock:
            return self._frames_since_train

    def should_train(self) -> bool:
        with self._lock:
            if self._training:
                return False
            if time.time() - self._last_train < config.AUTO_TRAIN_COOLDOWN_SEC:
                return False
            if self._frames_since_train < config.AUTO_TRAIN_AFTER_FRAMES:
                return False
        return _dataset_size() >= config.AUTO_TRAIN_MIN_TOTAL

    def start_training(self):
        """Spawn background training thread."""
        with self._lock:
            if self._training:
                return
            self._training = True
        t = threading.Thread(target=self._train_thread, daemon=True)
        t.start()
        print(f'[AUTOTRAIN] started — {self._frames_since_train} new frames collected')

    def label_then_train(self) -> bool:
        """Run the Claude auto-labeler over pending survey frames, then hand
        the stop/train/restart sequence off to a detached external script.

        aksumael can't cleanly `systemctl stop` itself: systemd kills the
        whole *cgroup*, including whatever thread issued the stop, so any
        restart logic that used to run afterward in *this* process never
        executed — aksumael never came back after a training cycle.
        start_new_session=True (setsid) only escapes the process *session*,
        not the cgroup, so the restart script still died with the rest of
        aksumael.service. Instead we preserve any in-progress goal, then
        launch tools/autotrain_restart.sh via `systemd-run --user --scope`
        into background.slice, which creates a brand-new transient
        scope/cgroup outside aksumael.service's — that survives the stop.
        It does the stop -> train -> restart dance on its own. Returns True
        if the handoff happened, False if this cycle was skipped.
        """
        if TRAIN_LOCK.exists():
            print(f'[AUTOTRAIN] training already in progress ({TRAIN_LOCK.read_text().strip()}) — skipping this cycle')
            return False

        python = sys.executable
        tools_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'tools'))

        autolabel_script = os.path.join(tools_dir, 'claude_autolabel.py')
        print('[AUTOTRAIN] auto-labeling new survey frames with Claude...')
        label_result = subprocess.run(
            [python, autolabel_script],
            capture_output=True, text=True, timeout=1800
        )
        if label_result.returncode != 0:
            print(f'[AUTOTRAIN] auto-labeling failed:\n{label_result.stderr[-500:]}')
        else:
            print('[AUTOTRAIN] auto-labeling complete')

        # Lockfile is written before we hand off so a second trigger inside
        # this process (or a stray restart) sees training in progress and
        # skips instead of racing the detached script for GPU.
        TRAIN_LOCK.write_text(str(os.getpid()))

        # Don't yank aksumael out from under an in-progress goal — give it
        # up to GOAL_WAIT_TIMEOUT_SEC to reach idle/explore on its own, and
        # if it doesn't, preserve the goal so it can be re-injected on the
        # next startup (see core.runtime).
        print('[AUTOTRAIN] checking for an active goal before stopping aksumael...')
        goal_state = _wait_for_goal_to_settle()
        if _goal_is_active(goal_state):
            print(f'[AUTOTRAIN] goal "{goal_state.get("current")}" still active '
                  f'after {GOAL_WAIT_TIMEOUT_SEC}s wait — preserving it before stopping')
            _preserve_goal_state(goal_state)
        else:
            print('[AUTOTRAIN] no active goal (idle/explore) — proceeding to stop')

        restart_script = os.path.join(tools_dir, 'autotrain_restart.sh')
        log_path = '/tmp/autotrain_restart.log'
        print(f'[AUTOTRAIN] handing off stop/train/restart to {restart_script} '
              f'(detached via systemd-run — log at {log_path})')
        subprocess.Popen(
            ['systemd-run', '--user', '--scope', '--slice=background.slice',
             'bash', restart_script],
            stdout=open(log_path, 'w'), stderr=subprocess.STDOUT,
        )
        return True

    def _train_thread(self):
        try:
            print('[AUTOTRAIN] training in background (this takes a few minutes)...')
            handed_off = self.label_then_train()
            if handed_off:
                print('[AUTOTRAIN] handed off to autotrain_restart.sh — '
                      'aksumael will restart once training completes')
            else:
                print('[AUTOTRAIN] training cycle skipped (lock already held)')
                with self._lock:
                    self._training = False
        except subprocess.TimeoutExpired:
            print('[AUTOTRAIN] auto-labeling timed out after 30 minutes')
            with self._lock:
                self._training = False
                self._last_train = time.time() - config.AUTO_TRAIN_COOLDOWN_SEC + FAILURE_COOLDOWN_SEC
        except Exception as e:
            print(f'[AUTOTRAIN] error: {e}')
            with self._lock:
                self._training = False
                self._last_train = time.time() - config.AUTO_TRAIN_COOLDOWN_SEC + FAILURE_COOLDOWN_SEC
