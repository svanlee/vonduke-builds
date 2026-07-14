# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Auto Trainer                       ║
# ║  Background retraining triggered by frame accumulation║
# ╚══════════════════════════════════════════════════════╝
#
# AutoTrainer — background retraining triggered by frame accumulation.
# Spawns yolo_finetune.py train as a subprocess, then hot-reloads weights.

import subprocess, threading, time, os, sys, pathlib, json
import urllib.request, urllib.error
import config
from memory.goals import GOALS_PATH

TRAIN_LOCK = pathlib.Path('/tmp/aksumael_training.lock')
PRESERVED_GOALS_PATH = 'data/preserved_goals.json'

# Goals that count as "nothing important in progress" — safe to interrupt
# without preserving. Mirrors the baseline states in memory.goals.GOAL_PRIORITIES.
_TRIVIAL_GOALS = {'idle', 'explore'}

GOAL_WAIT_TIMEOUT_SEC = 60
GOAL_WAIT_POLL_SEC    = 2

MESH_LLM_HEALTH_TIMEOUT_SEC = 60
MESH_LLM_HEALTH_POLL_SEC    = 2


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


def _wait_for_mesh_llm_healthy(timeout_sec: float = MESH_LLM_HEALTH_TIMEOUT_SEC,
                                poll_sec: float = MESH_LLM_HEALTH_POLL_SEC) -> bool:
    """Poll mesh-llm's OpenAI-compatible /v1/models endpoint until it
    responds, instead of assuming a fixed sleep was long enough. Logs
    whatever model list comes back so a stuck/empty load is visible in the
    autotrain logs rather than silently causing 'all LLM tiers failed'
    downstream in inventory_reader / vision_brain."""
    url = f'{config.LOCAL_LLM_URL}/models'
    waited = 0.0
    while waited < timeout_sec:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
            models = [m.get('id') for m in data.get('data', [])]
            # The endpoint answers 200 with an empty list while the API
            # layer is up but no model has finished loading yet (including
            # mid-crash-loop) — that's not "healthy", keep polling.
            if models:
                print(f'[AUTOTRAIN] mesh-llm healthy after {waited:.0f}s — models loaded: {models}')
                return True
            if waited == 0 or waited % 10 == 0:
                print(f'[AUTOTRAIN] mesh-llm API up but no models loaded yet — '
                      f'{waited:.0f}s/{timeout_sec:.0f}s')
        except Exception as e:
            if waited == 0 or waited % 10 == 0:
                print(f'[AUTOTRAIN] mesh-llm not healthy yet ({e}) — '
                      f'{waited:.0f}s/{timeout_sec:.0f}s')
        time.sleep(poll_sec)
        waited += poll_sec
    print(f'[AUTOTRAIN] mesh-llm did not become healthy within {timeout_sec:.0f}s — '
          f'starting aksumael anyway')
    return False


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

    def label_then_train(self):
        """Run the Claude auto-labeler over pending survey frames, then train."""
        if TRAIN_LOCK.exists():
            print(f'[AUTOTRAIN] training already in progress ({TRAIN_LOCK.read_text().strip()}) — skipping this cycle')
            return subprocess.CompletedProcess(args=[], returncode=1, stdout='', stderr='training lock held')

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

        # Force the training subprocess's own DataLoader workers to use the
        # 'spawn' start method rather than 'fork' — this process shares a GPU
        # (CUDA context) with the live capture/YOLO threads, and forked
        # workers inheriting that state is what tears down the pipe.
        train_env = {**os.environ, 'PYTORCH_MULTIPROCESSING_START_METHOD': 'spawn'}

        train_script = os.path.join(tools_dir, 'yolo_finetune.py')
        # Lockfile is written before we stop aksumael/mesh-llm so that if
        # aksumael's watcher tries to restart it mid-training, it sees the
        # lock and waits instead of racing the training subprocess for GPU.
        TRAIN_LOCK.write_text(str(os.getpid()))
        try:
            # Don't yank aksumael out from under an in-progress goal — give
            # it up to GOAL_WAIT_TIMEOUT_SEC to reach idle/explore on its
            # own, and if it doesn't, preserve the goal so it can be
            # re-injected on the next startup (see core.runtime).
            print('[AUTOTRAIN] checking for an active goal before stopping aksumael...')
            goal_state = _wait_for_goal_to_settle()
            if _goal_is_active(goal_state):
                print(f'[AUTOTRAIN] goal "{goal_state.get("current")}" still active '
                      f'after {GOAL_WAIT_TIMEOUT_SEC}s wait — preserving it before stopping')
                _preserve_goal_state(goal_state)
            else:
                print('[AUTOTRAIN] no active goal (idle/explore) — proceeding to stop')

            # Both services hold VRAM the training subprocess needs for its
            # own CUDA context — stop them before spawning training, then
            # restart once the lock is released below.
            print('[AUTOTRAIN] stopping aksumael and mesh-llm to free VRAM for training...')
            subprocess.run(['systemctl', '--user', 'stop', 'aksumael'],
                            timeout=15, capture_output=True)
            subprocess.run(['systemctl', '--user', 'stop', 'mesh-llm'],
                            timeout=15, capture_output=True)
            time.sleep(3)  # let VRAM clear

            return subprocess.run(
                [python, train_script, 'train'],
                capture_output=True, text=True, timeout=1800,  # 30 min max
                env=train_env,
            )
        finally:
            TRAIN_LOCK.unlink(missing_ok=True)
            subprocess.run(['systemctl', '--user', 'start', 'mesh-llm'],
                            timeout=15, capture_output=True)
            _wait_for_mesh_llm_healthy()  # only start aksumael once mesh-llm actually answers
            subprocess.run(['systemctl', '--user', 'start', 'aksumael'],
                            timeout=15, capture_output=True)

    def _train_thread(self):
        try:
            print('[AUTOTRAIN] training in background (this takes a few minutes)...')
            result = self.label_then_train()

            if result.returncode == 0:
                print('[AUTOTRAIN] training complete — hot-reloading weights')
                self._yolo.reload_weights()
                with self._lock:
                    self._frames_since_train = 0
                    self._last_train = time.time()
                print('[AUTOTRAIN] new weights active')
            else:
                print(f'[AUTOTRAIN] training failed:\n{result.stderr[-500:]}')
                with self._lock:
                    self._last_train = time.time() - config.AUTO_TRAIN_COOLDOWN_SEC + FAILURE_COOLDOWN_SEC
        except subprocess.TimeoutExpired:
            print('[AUTOTRAIN] training timed out after 30 minutes')
            with self._lock:
                self._last_train = time.time() - config.AUTO_TRAIN_COOLDOWN_SEC + FAILURE_COOLDOWN_SEC
        except Exception as e:
            print(f'[AUTOTRAIN] error: {e}')
            with self._lock:
                self._last_train = time.time() - config.AUTO_TRAIN_COOLDOWN_SEC + FAILURE_COOLDOWN_SEC
        finally:
            with self._lock:
                self._training = False
