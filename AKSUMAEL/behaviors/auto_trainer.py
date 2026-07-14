# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Auto Trainer                       ║
# ║  Background retraining triggered by frame accumulation║
# ╚══════════════════════════════════════════════════════╝
#
# AutoTrainer — background retraining triggered by frame accumulation.
# Spawns yolo_finetune.py train as a subprocess, then hot-reloads weights.

import subprocess, threading, time, os, sys, pathlib
import config

TRAIN_LOCK = pathlib.Path('/tmp/aksumael_training.lock')


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

        # mesh-llm is idle for now and was eating VRAM the training subprocess
        # needs for its own CUDA context — stop it before spawning training,
        # then restart it once the lock is released (see finally: below).
        print('[AUTOTRAIN] stopping mesh-llm to free VRAM for training...')
        subprocess.run(['systemctl', '--user', 'stop', 'mesh-llm'],
                        timeout=10, capture_output=True)

        # Force the training subprocess's own DataLoader workers to use the
        # 'spawn' start method rather than 'fork' — this process shares a GPU
        # (CUDA context) with the live capture/YOLO threads, and forked
        # workers inheriting that state is what tears down the pipe.
        train_env = {**os.environ, 'PYTORCH_MULTIPROCESSING_START_METHOD': 'spawn'}

        train_script = os.path.join(tools_dir, 'yolo_finetune.py')
        # Lockfile blocks a concurrent manually-launched training run (or a
        # second AutoTrainer instance after a restart) from competing for
        # the same GPU/CUDA context.
        TRAIN_LOCK.write_text(str(os.getpid()))
        try:
            return subprocess.run(
                [python, train_script, 'train'],
                capture_output=True, text=True, timeout=1800,  # 30 min max
                env=train_env,
            )
        finally:
            TRAIN_LOCK.unlink(missing_ok=True)
            import subprocess as _sp
            _sp.run(["systemctl", "--user", "start", "mesh-llm"], timeout=15, capture_output=True)

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
