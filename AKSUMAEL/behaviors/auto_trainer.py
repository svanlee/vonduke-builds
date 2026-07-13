# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Auto Trainer                       ║
# ║  Background retraining triggered by frame accumulation║
# ╚══════════════════════════════════════════════════════╝
#
# AutoTrainer — background retraining triggered by frame accumulation.
# Spawns yolo_finetune.py train as a subprocess, then hot-reloads weights.

import subprocess, threading, time, os, sys
import config


def _dataset_size() -> int:
    """Count total saved images in the yolo training dataset."""
    from tools.yolo_finetune import IMAGES_DIR
    total = 0
    for split in ('train', 'val'):
        d = f'{IMAGES_DIR}/{split}'
        if os.path.exists(d):
            total += len([f for f in os.listdir(d) if f.endswith('.jpg')])
    return total


# Retry budget for subprocess calls that die with a reset/broken pipe —
# GPU contention between the live capture/YOLO threads and the training
# subprocess's DataLoader workers can tear down the stdout/stderr pipe
# mid-scan (ConnectionResetError) rather than the subprocess exiting cleanly.
SUBPROCESS_PIPE_RETRIES = 2


def _run_subprocess_with_retry(args, **kwargs):
    """subprocess.run() wrapper that retries on transient pipe failures
    instead of propagating them and killing the whole training thread."""
    last_err = None
    for attempt in range(1, SUBPROCESS_PIPE_RETRIES + 1):
        try:
            return subprocess.run(args, **kwargs)
        except (ConnectionResetError, BrokenPipeError) as e:
            last_err = e
            print(f'[AUTOTRAIN] ⚠ pipe to subprocess dropped ({e}) — '
                  f'retry {attempt}/{SUBPROCESS_PIPE_RETRIES}')
            time.sleep(2)
    raise last_err


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
        python = sys.executable
        tools_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'tools'))

        autolabel_script = os.path.join(tools_dir, 'claude_autolabel.py')
        print('[AUTOTRAIN] auto-labeling new survey frames with Claude...')
        label_result = _run_subprocess_with_retry(
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
        return _run_subprocess_with_retry(
            [python, train_script, 'train'],
            capture_output=True, text=True, timeout=1800,  # 30 min max
            env=train_env,
        )

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
        except subprocess.TimeoutExpired:
            print('[AUTOTRAIN] training timed out after 30 minutes')
        except (ConnectionResetError, BrokenPipeError) as e:
            # Pipe to the subprocess died even after retries — log and bail
            # cleanly so the next should_train() cycle can start a fresh run
            # instead of leaving _training stuck True or crashing the thread.
            print(f'[AUTOTRAIN] ⚠ training subprocess pipe lost, giving up '
                  f'this cycle (will retry next survey pass): {e}')
        except Exception as e:
            print(f'[AUTOTRAIN] error: {e}')
        finally:
            with self._lock:
                self._training = False
