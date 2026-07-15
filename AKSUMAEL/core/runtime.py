# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Main Runtime Loop                  ║
# ╚══════════════════════════════════════════════════════╝

import json
import os
import pathlib
import random
import re
import signal
import time
import cv2
import config

TRAIN_LOCK = pathlib.Path('/tmp/aksumael_training.lock')
PRESERVED_GOALS_PATH = pathlib.Path('data/preserved_goals.json')


def _train_lock_owner_alive() -> bool:
    """False if the lock is stale (owning process no longer exists)."""
    try:
        pid = int(TRAIN_LOCK.read_text().strip())
    except (ValueError, FileNotFoundError):
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _restore_preserved_goals():
    """Re-inject a goal (current + queued stack) that behaviors/auto_trainer.py
    saved before stopping this service mid-goal, via the same
    injected_goals.json queue mastermind/axon use — drained by
    GoalStack.check_injected_goals() on the next tick."""
    if not PRESERVED_GOALS_PATH.exists():
        return
    try:
        state = json.loads(PRESERVED_GOALS_PATH.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f'[STARTUP] preserved-goals read error: {e} — discarding')
        PRESERVED_GOALS_PATH.unlink(missing_ok=True)
        return

    restored = [g for g in list(state.get('stack', [])) + [state.get('current')] if g]
    if not restored:
        PRESERVED_GOALS_PATH.unlink(missing_ok=True)
        return

    queue = []
    if os.path.exists(INJECTED_GOALS_PATH):
        try:
            with open(INJECTED_GOALS_PATH) as f:
                queue = json.load(f).get('queue', [])
        except (OSError, json.JSONDecodeError):
            queue = []
    queue.extend({'goal': g, 'reason': 'restored after autotrain restart',
                  'received_at': time.time()} for g in restored)
    os.makedirs(os.path.dirname(INJECTED_GOALS_PATH) or '.', exist_ok=True)
    with open(INJECTED_GOALS_PATH, 'w') as f:
        json.dump({'queue': queue}, f)
    print(f'[STARTUP] restored preserved goal(s) from autotrain restart: {restored}')
    PRESERVED_GOALS_PATH.unlink(missing_ok=True)

from core.capture            import VideoCapturePipeline
from vision.color_detector   import detect_ores_by_color, merge_with_yolo
from vision.yolo             import YOLODetector
from vision.f3_reader        import read_f3
from core.vision_brain       import ask_vision, get_call_counts, get_last_provider
from core.world_model        import WorldModel
from core.cognitive          import CognitiveArchitecture
from core.planner            import Planner
from core.episode_memory     import EpisodeMemory
from core.curriculum         import CurriculumGenerator
from core                    import code_skill_generator
from core                    import feature_extractor
from core                    import policy_blender
from memory.reward           import RewardSystem
from memory.world_memory     import WorldMemory
from memory.inventory        import InventoryTracker
from memory.goals            import GoalStack, INJECTED_GOALS_PATH
from memory.goal_interpreter import GoalInterpreter
from memory.progression      import ProgressionTracker
from memory.minecraft_kb     import MinecraftKB
from memory.rl_policy        import RLPolicy
from actions.executor        import ActionExecutor
from input.controller_router import ControllerRouter
from audio.tts               import TTSEngine
from audio.game_ear          import GameEar
from skills.skill_system     import SkillSystem, SkillReplayer
from actions.aim_controller  import AimController
from ui.labeling             import LabelingUI

BANNER = """
                    ___
                   /   \\
                  ▔▔▔▔▔▔▔
              A k s ū m a e l
              ─────┬─────
                   │
              v 1 . 0 . 0
"""


def run():
    if TRAIN_LOCK.exists():
        waited = 0
        print(f'[STARTUP] Training in progress ({TRAIN_LOCK.read_text().strip()}) — waiting for it to finish...')
        while TRAIN_LOCK.exists():
            if not _train_lock_owner_alive():
                print('[STARTUP] Lock owner process is gone — treating lock as stale, removing it.')
                TRAIN_LOCK.unlink(missing_ok=True)
                break
            time.sleep(10)
            waited += 10
            if waited % 60 == 0:
                print(f'[STARTUP] ...still waiting ({waited}s)')
        print(f'[STARTUP] continuing startup after {waited}s wait.' if waited else '[STARTUP] no training lock — continuing startup.')

    print(BANNER)
    print(f'  vision   : {config.VISION_PROVIDER} / capture card')
    print(f'  actions  : {config.ACTION_OUTPUT} → {config.PLATFORM_TARGET}')
    print(f'  blend    : {config.BLEND_MODE}')
    print(f'  tts      : {"on" if config.ENABLE_TTS else "off"}  '
          f'game_ear : {"on" if config.ENABLE_GAME_EAR else "off"}  '
          f'survey   : on (conf<{config.SURVEY_CONF_THRESH})')
    print()

    # ── Environment detection (opt-in — see config.ENV_PROFILE_ENABLED) ──
    # Runs before YOLODetector so a matched/created profile's
    # yolo_model_path (if any) can override config.YOLO_MODEL before it's
    # read at construction time. env_profile_obj stays None when disabled,
    # which is the only state every check below treats as "behave exactly
    # as before this existed".
    env_profile_obj = None
    if getattr(config, 'ENV_PROFILE_ENABLED', False):
        from core import env_detector
        env_profile_obj = env_detector.detect(fallback_env_id=config.ACTIVE_ENV)
        config.YOLO_MODEL = env_profile_obj.effective_yolo_model(config.YOLO_MODEL)
        print(f'[ENV_PROFILE] active: {env_profile_obj.env_id} '
              f'(bootstrap={env_profile_obj.bootstrap}, '
              f'yolo_model={config.YOLO_MODEL})')

    # ── Initialise all subsystems ──────────────────────────────
    yolo      = YOLODetector()
    world     = WorldModel()
    world_mem = WorldMemory()
    inventory = InventoryTracker()
    goals       = GoalStack()
    _restore_preserved_goals()
    progression = ProgressionTracker()
    mc_kb       = MinecraftKB()
    cognitive   = CognitiveArchitecture()
    reward    = RewardSystem()
    executor  = ActionExecutor()

    # systemd sends SIGTERM on every `systemctl --user stop aksumael`
    # (autotrain retraining cycles do this routinely). Python's default
    # SIGTERM disposition kills the process immediately without running
    # any except/finally cleanup — unlike SIGINT, which Python auto-
    # converts to KeyboardInterrupt. If a SIGTERM lands mid F3-toggle
    # (see the F3 debug overlay OCR block below), the closing keypress
    # never fires and Minecraft's F3 overlay is left open indefinitely,
    # since _f3_open is in-memory only and the next process has no idea
    # it happened. Route SIGTERM through the same KeyboardInterrupt path
    # so the existing finally-block cleanup (and the F3 force-close
    # added there) actually runs.
    signal.signal(signal.SIGTERM, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt))

    router    = ControllerRouter()
    tts       = TTSEngine()
    ear       = GameEar()          # graceful if no audio device
    skills    = SkillSystem()
    planner     = Planner()
    episodes    = EpisodeMemory()
    curriculum  = CurriculumGenerator(planner)
    rl        = RLPolicy()
    replayer  = SkillReplayer(executor)

    # Cross-environment skill filter + adaptive self-labeling (opt-in — see
    # env_profile_obj above). Both no-op when env_profile_obj is None.
    label_queue_obj = None
    if env_profile_obj is not None:
        from core import skill_transfer
        _active_skills = skill_transfer.apply_profile(skills, env_profile_obj)
        print(f'[ENV_PROFILE] skill filter: '
              f'{"unfiltered (no yolo_classes yet)" if _active_skills is None else f"{len(_active_skills)} active skill(s)"}')
        if getattr(config, 'LABEL_QUEUE_ENABLED', False):
            from core.label_queue import LabelQueue
            label_queue_obj = LabelQueue()

    # Neural policy backbone (opt-in) — low-level learned action policy
    # that blends with the skill/FSM/LLM decision (core/policy_blender.py)
    # when no skill fires. Off by default; requires PyTorch at runtime.
    neural_policy = None
    rl_trainer    = None
    if config.NEURAL_POLICY_ENABLED:
        try:
            from core.neural_policy import NeuralPolicy
            from core.rl_trainer    import RLTrainer
            neural_policy = NeuralPolicy(obs_dim=feature_extractor.OBS_DIM,
                                          goal_dim=feature_extractor.GOAL_DIM)
            rl_trainer    = RLTrainer(neural_policy)
            print('[NEURAL_POLICY] enabled — PPO trainer running in background')
        except Exception as e:
            print(f'[NEURAL_POLICY] init failed: {e} — falling back to rule-based only')
            neural_policy = None
            rl_trainer    = None
    aim_ctrl  = AimController()          # uses YOLO-frame coords (640×360)
    ui        = LabelingUI(yolo, router, reward, skills=skills)

    # ── Mastermind hive (opt-in) ────────────────────────────────
    mastermind_client = None
    if getattr(config, 'MASTERMIND_ENABLED', False):
        try:
            from mastermind.agent_client import AgentClient
            mastermind_client = AgentClient(
                host=config.MASTERMIND_HOST,
                port=getattr(config, 'MASTERMIND_PORT', 1883),
                agent_id=getattr(config, 'MASTERMIND_AGENT_ID', None),
                env_name=config.ACTIVE_ENV,
            )
        except Exception as e:
            print(f'[MASTERMIND] disabled for this run — client init failed: {e}')

    # ── Threaded capture / YOLO / display pipeline ─────────────
    # CaptureThread reads /dev/video2 at full speed (MJPEG, CAP_PROP_BUFFERSIZE=1)
    # YOLOThread runs inference on the latest frame at GPU speed
    # DisplayThread calls cv2.imshow via LabelingUI at ~30 fps
    pipeline  = VideoCapturePipeline(yolo, ui, device_index=config.CAMERA_INDEX)

    # Frame collector for YOLO fine-tuning — kept around for force_save()
    # even though the old timer-based COLLECT_FRAMES auto-save is disabled;
    # the survey behavior below drives frame saving now.
    collector = None
    try:
        from tools.yolo_finetune import FrameCollector
        collector = FrameCollector()
        print('[COLLECT] frame collector ready (survey-driven)')
    except Exception as e:
        print(f'[COLLECT] could not start collector: {e}')

    from behaviors.survey import SurveyBehavior
    from behaviors.auto_trainer import AutoTrainer
    from behaviors.respawn import RespawnBehavior
    from behaviors.hunger import HungerBehavior
    from behaviors.night_survival import NightSurvivalBehavior
    from behaviors.torch_placement import TorchBehavior
    from behaviors.crafting import CraftingBehavior
    from behaviors.inventory_reader import InventoryReader
    from behaviors.chest_manager import ChestManager
    from behaviors.scan import EnvironmentScanner
    from behaviors.launch_game import GameLauncher
    from core.fsm import GameFSM, State, ORE_TARGETS, TREE_TARGETS
    auto_trainer = AutoTrainer(yolo)
    surveyor = SurveyBehavior(collector, executor, auto_trainer=auto_trainer) if collector else None
    respawner = RespawnBehavior(executor, goals)
    hunger_behavior = HungerBehavior(executor, goals)
    night_survival  = NightSurvivalBehavior(executor, goals)
    torch_behavior  = TorchBehavior(executor)
    inv_reader = InventoryReader(executor, capture_fn=lambda: pipeline.latest_raw_frame)
    crafting_behavior = CraftingBehavior(executor, inventory_reader=inv_reader)
    chest_mgr = ChestManager()
    goal_interp = GoalInterpreter(goals, crafting_behavior)
    scanner     = EnvironmentScanner(executor, aim_ctrl, pipeline, ask_vision)
    launcher    = GameLauncher(executor, game='minecraft')
    fsm = GameFSM()

    # Start background threads
    pipeline.start()   # CaptureThread + YOLOThread + DisplayThread
    router.start()
    if ear.enabled:
        ear.start()

    tts.say_line('startup')

    tick = 0
    last_skill_name  = None
    same_skill_count = 0
    SAME_SKILL_LIMIT = 3
    skill_cooldown_name       = None   # skill name currently suppressed
    skill_cooldown_until_tick = 0      # tick at which suppression lifts
    SKILL_COOLDOWN_TICKS      = 30     # how long a spammed skill stays suppressed
    # Skill-replay escape: a skill that keeps matching but scores near-zero
    # confidence (avg_reward ~0) isn't accomplishing anything — track
    # consecutive low-confidence fires of the SAME skill so we can give up
    # on it and force a real LLM re-plan instead of looping forever.
    low_conf_skill_name    = None
    low_conf_skill_streak  = 0
    LOW_CONF_SKILL_THRESH  = 0.1   # confidence at/below this counts as "not working"
    LOW_CONF_SKILL_TICKS   = 4     # consecutive low-confidence fires before giving up
    force_llm_reconsider   = False # set when a stuck/mismatched skill is aborted —
                                    # bypasses FSM control + LLM cadence gating this tick
    last_action      = {}    # most recent Claude (LLM) response dict
    prev_objects     = []    # last tick's YOLO detections (for HUD-delta reward)
    _pending_neural_transition = None   # (obs, goal, action_idx, log_prob, value) awaiting this tick's reward
    # Anti-stuck: count consecutive low-reward ticks
    _low_reward_streak  = 0
    _LOW_REWARD_THRESH  = 0.05   # reward below this = unproductive
    _STUCK_TICKS        = 150    # consecutive low-reward ticks before intervention
    # Secondary anti-stuck: catches cases the reward signal misses (e.g.
    # wandering with near-zero-but-not-quite-zero reward) by watching for a
    # long stretch where inventory contents AND the active goal never change.
    _INVENTORY_STUCK_TICKS  = 300
    _last_inv_snapshot      = None   # {item: count} as of _last_inv_change_tick
    _last_inv_change_tick   = 0
    _last_goal_for_stuck    = None
    _last_goal_change_tick  = 0
    f3_countdown         = 50    # ticks until next F3 OCR read (offset from startup)
    _f3_open             = False  # True while F3 overlay is open
    _menu_stuck_since    = 0      # tick when menu was first detected open
    MENU_STUCK_TICKS     = 20     # close menu after this many ticks with no action
    fsm_state        = None  # updated each tick for console logging
    _llm_call_count  = 0     # total LLM calls this session
    _last_llm_frame  = None  # frame used in last LLM call (for frame-diff skip)
    _last_scan_tick  = -config.SCAN_COOLDOWN_TICKS   # fire scan on first EXPLORE tick
    _last_tree_fallback_tick = -config.SCAN_COOLDOWN_TICKS  # tree-goal walk+pan fallback
    _last_chest_tick    = -1000   # tick of last chest interaction
    CHEST_COOLDOWN_TICKS = 200    # min ticks between chest opens (avoid spamming Claude)
    BASE_X, BASE_Z        = -6, -3   # spawn / base coordinates
    _prev_replay_active = False  # replayer.is_active() as of last tick
    # Skill pre/postcondition verification (v1.1) — snapshot inventory when a
    # skill replay starts, diff it against the post-replay inventory to
    # decide whether the skill's postconditions were actually met.
    _verify_skill      = None   # Skill instance currently being verified
    _verify_inv_before = {}
    # Goal-episode tracking (v1.1) — snapshot inventory the first tick a goal
    # becomes current, so a retirement can be logged as a JARVIS-1-style
    # episode (goal, outcome, inventory before/after) once it changes again.
    _goal_inv_snapshots = {}
    # Mining skills replay recorded look deltas that tend to walk the camera
    # pitch upward over a full replay. Once the skill ends, nudge the pitch
    # back down toward the horizon so EXPLORE/APPROACH aren't scanning sky.
    _MINE_PITCH_RESET_DY    = config.LOOK_SENSITIVITY * 8   # ~120px — down, not to the ground
    _PITCH_RESET_TICKS      = 3   # spread the correction over N ticks instead of one big jerk
    _pitch_reset_ticks_left = 0
    _pitch_reset_dy_step    = 0

    # Soft clamp on cumulative look-pitch drift accumulated over the session —
    # prevents slow camera walk (up or down) that individual per-skill resets
    # don't fully correct from compounding into a permanently skewed pitch.
    _PITCH_CLAMP_LIMIT = 200
    _PITCH_CLAMP_NUDGE = config.LOOK_SENSITIVITY * 4

    # Init extended F3 fields on world_mem so context_summary() can use them
    world_mem.pos_x   = getattr(world_mem, 'pos_x',   None)
    world_mem.pos_z   = getattr(world_mem, 'pos_z',   None)
    world_mem.facing  = getattr(world_mem, 'facing',  'unknown')
    world_mem.fps     = getattr(world_mem, 'fps',     None)
    world_mem.chunk_x = getattr(world_mem, 'chunk_x', None)
    world_mem.chunk_z = getattr(world_mem, 'chunk_z', None)
    world_mem.chest_inv = getattr(world_mem, 'chest_inv', {})
    print('[AKSUMAEL] running — Ctrl+C or q in window to stop\n')

    def _open_read_close_f3():
        """Press F3, OCR the overlay, press F3 again to close — the shared
        primitive behind both the periodic F3 read and TREE-FALLBACK's
        on-demand position check. Returns the f3_data dict from read_f3()
        (f3_active is False if OCR never found the XYZ line).

        The KB2040 talks to the target PC over a physical UART link, which
        can drop a keypress packet — see 2026-07-15 incident where the
        'open' press silently failed to register (confirmed via a raw-frame
        dump: plain gameplay, no debug text at all) and every subsequent
        read kept returning facing=None/biome=None. If the first attempt
        finds no XYZ line, press F3 again and retry once — if the first
        press actually landed just before the game rendered it, or if it
        dropped and the overlay is still closed, this second press either
        catches the render or opens it fresh. Either way the close press
        below still balances out to an even number of toggles, so a failed
        retry can't leave the overlay stuck open for the rest of the
        session."""
        f3_data = {'f3_active': False}
        for attempt in range(2):
            executor.execute({'key': 'f3'})
            time.sleep(config.F3_KEY_WAIT_TICKS * 0.2)
            f3_frame = pipeline.latest_raw_frame
            f3_data  = read_f3(f3_frame) if f3_frame is not None else {'f3_active': False}
            if f3_data['f3_active']:
                break
            if attempt == 0:
                print('[F3] overlay did not render on first press — retrying once')
                executor.execute({'key': 'f3'})   # undo the failed toggle before retrying
                time.sleep(0.3)
        executor.execute({'key': 'f3'})   # close
        return f3_data

    def _f3_says_moved(old_x, old_z, new_x, new_z) -> bool:
        """True unless we have two successful, agreeing F3 reads proving the
        character DIDN'T move. A None on either side means OCR failed —
        treat that as "unknown, assume moved" rather than stuck, since
        None == None is not a valid comparison and was making
        TREE-FALLBACK report "stuck" on every single attempt whenever F3
        OCR was broken (see 2026-07-15 — position never actually confirmed
        either way, but the fallback kept declaring itself stuck and
        escalating to jump/dig-out regardless of real movement). Only a
        confirmed pair of matching reads should hold the walk back."""
        if old_x is None or old_z is None or new_x is None or new_z is None:
            return True
        return ((new_x - old_x) ** 2 + (new_z - old_z) ** 2) ** 0.5 > 0.5

    def _read_f3_position_now():
        """Blocking open/OCR/close F3 cycle — used by TREE-FALLBACK to check
        whether walking actually moved the player (stuck-in-building check).
        Returns (x, z), either of which may be None if OCR found nothing.

        Guarded the same way as the periodic F3 read below (only open F3
        when the HUD is visible and no menu is open) — see 2026-07-15
        incident where this ran unguarded while a menu was open, so F3
        never actually rendered and OCR garbled the raw gameplay frame
        into noise (facing=None biome=None on every attempt)."""
        nonlocal _f3_open
        if not _hud_present or _menu_open or _f3_open:
            return None, None
        _f3_open = True
        f3_data = _open_read_close_f3()
        _f3_open = False
        if f3_data['f3_active']:
            world_mem.update_f3(f3_data)
        return f3_data.get('x'), f3_data.get('z')

    try:
        while True:
            tick += 1
            t0 = time.time()

            # ── Joystick physical buttons ──────────────────────
            h = router.human_state
            _handle_joystick(h, ui, router, reward, tts)

            # ── Capture frame (from CaptureThread) ────────────
            # CaptureThread continuously reads /dev/video2; we take the
            # freshest 640-wide frame without blocking.
            frame = pipeline.latest_small_frame
            if frame is None:
                tts.say_line('no_frame', priority=True)
                time.sleep(1)
                continue

            # ── YOLO detection (from YOLOThread) ───────────────
            # YOLOThread runs YOLO at GPU speed; we read its latest results.
            objects = pipeline.latest_objects

            # ── Color-based ore detection ───────────────────────
            # Catches diamond/emerald/gold ore that YOLO misses by scanning
            # for their distinctive HSV color signatures. Color detections
            # are only injected when YOLO has no match for that ore type.
            _color_dets = detect_ores_by_color(frame)
            if _color_dets:
                _new = [d for d in _color_dets if d['label'] not in {o.get('label') for o in objects}]
                if _new:
                    for d in _new:
                        print(f'[COLOR] {d["label"]} detected by color (conf={d["conf"]:.2f})')
                objects = merge_with_yolo(objects, _color_dets)

            # ── Adaptive self-labeling queue (opt-in — see config.LABEL_QUEUE_ENABLED) ──
            if label_queue_obj is not None:
                label_queue_obj.maybe_queue(frame, objects, env_profile_obj.env_id)
                if tick % config.LABEL_QUEUE_EVERY_N_TICKS == 0:
                    label_queue_obj.label_pending(env_profile_obj.env_id)
                    if label_queue_obj.should_retrain(env_profile_obj.env_id):
                        label_queue_obj.trigger_retrain(env_profile_obj.env_id)

            # ── HUD / menu state ────────────────────────────────
            # Used to gate anything that would corrupt an open menu screen
            # (F3 debug overlay toggle, scan/pathfinder sweeps, skill execution).
            # NOTE: 'crafting_table', 'chest_row', 'furnace' are WORLD BLOCKS —
            # YOLO detects them constantly during normal mining/exploring, not
            # just when their UI is open. Only 'inventory' (an actual open-menu
            # class) belongs here; the others caused _menu_open false-positives
            # that idled the bot and triggered spurious Escape presses.
            _hud_labels  = {o.get('label') for o in objects}
            _menu_open   = bool(_hud_labels & {'inventory'})
            _hud_present = bool(_hud_labels & {'hotbar', 'health_bar', 'hunger_bar'})

            # ── Escape stuck menu ────────────────────────────────
            # If a menu has been open for too long with no escape, close it.
            if _menu_open:
                if _menu_stuck_since == 0:
                    _menu_stuck_since = tick
                elif (tick - _menu_stuck_since) >= MENU_STUCK_TICKS:
                    print(f'[MENU] stuck for {tick - _menu_stuck_since} ticks — pressing Escape')
                    executor.execute({'key': 'esc'})
                    _menu_stuck_since = 0
            else:
                _menu_stuck_since = 0

            # ── Game launcher — runs before anything else ──────
            # If no HUD detected, AKSUMAEL isn't in-game yet.
            # Execute the launch sequence, then skip this tick.
            # Grace period: skip launch checks for the first 30 ticks so
            # YOLO and capture have time to warm up (otherwise HUD detection
            # fails on every restart and triggers a spurious launch).
            if tick >= 30 and launcher.should_trigger(objects, tick):
                print(f'[LAUNCH] HUD not detected at tick {tick} — triggering launch sequence')
                launcher.run(tick)
                continue

            world_mem.update(objects, action=last_action)
            goals.auto_update(world_mem, inventory, tick)

            # ── Mastermind hive — drain assigned goals, publish status ──
            try:
                goals.check_injected_goals()
            except Exception as e:
                print(f'[MASTERMIND] injected-goals check error: {e}')
            if mastermind_client is not None:
                mastermind_client.tick(tick, status='running',
                                        current_goal=goals.current_goal(),
                                        world_model=world)

            # ── Goal retirement + episode memory (v1.1) ─────────
            # Snapshot inventory the first tick we see a given goal, so a
            # retirement (success or timeout) can be recorded as an episode.
            _goal_now = goals.current_goal()
            if _goal_now not in _goal_inv_snapshots:
                _goal_inv_snapshots[_goal_now] = dict(inventory.items)
            try:
                goals.check_retirement(tick, world, inventory)
            except Exception as e:
                print(f'[GOALS] retirement check error: {e}')
            if goals.current_goal() != _goal_now and goals.last_retirement:
                _ret = goals.last_retirement
                _outcome = 'success' if _ret['reason'].startswith('success') else 'timeout'
                episodes.record(
                    goal=_ret['goal'], plan=[], outcome=_outcome,
                    inv_before=_goal_inv_snapshots.pop(_goal_now, {}),
                    inv_after=dict(inventory.items),
                    position=getattr(world, 'position', None), tick=tick,
                )
                goals.last_retirement = None

            # ── Curriculum: suggest a new goal when idle (v1.1) ─
            try:
                curriculum.run_every_n_ticks(tick, goals, inventory, world)
            except Exception as e:
                print(f'[CURRICULUM] error: {e}')

            progression.auto_update(inventory, world_mem, tick)

            # ── Proximity to base (used by chest interaction + LLM context) ─
            _near_base = (
                world_mem.pos_x is not None and world_mem.pos_z is not None
                and ((world_mem.pos_x - BASE_X) ** 2
                     + (world_mem.pos_z - BASE_Z) ** 2) ** 0.5 <= 20
            )

            # Made it back after a respawn — resume normal exploration.
            if _near_base and goals.current_goal() == 'return_to_base':
                print('[GOALS] reached base — clearing return_to_base')
                goals.current = 'explore'
                goals.save()

            # ── Craft goal auto-push (every 60 ticks if inv cache is warm) ─
            if tick % 60 == 0 and inv_reader._cache_ts > 0:
                goals.suggest_craft_goal(inv_reader.read(force=False), world_mem.chest_inv)

            if tick % 50 == 0:
                inventory.save()
                goals.save()

            # ── Display (must run on main thread for Qt/OpenCV) ───
            # poll_display() calls cv2.imshow() here on the main thread,
            # avoiding the Qt "No such method GuiReceiver::showImage" spam.
            if not pipeline.poll_display():
                break
            if pipeline.quit:
                break
            if ui.enabled:
                ui_r = ui.consume_reward()
                if ui_r > 0:
                    reward.add_manual(+1.0)
                    tts.say_line('good_reward')
                elif ui_r < 0:
                    reward.add_manual(-1.0)
                    tts.say_line('bad_reward')

            if ui.paused:
                time.sleep(0.05)
                continue

            # ── Game audio events ──────────────────────────────
            if ear.enabled:
                audio_ev = ear.poll()
                if audio_ev:
                    reward.add_audio_reward(audio_ev['reward'])
                    if audio_ev.get('persona'):
                        tts.say_line(audio_ev['persona'])

            # ── Unknown object prompt ──────────────────────────
            if yolo.has_unknowns():
                unknown = yolo.pop_unknown()
                if unknown:
                    tts.say_line('unknown_object')
                    print(f'[YOLO] unknown at {unknown["box"]} '
                          f'conf={unknown["conf"]:.2f} '
                          f'— click in window to label')

            # ── FSM tick (runs every tick; drives core gameplay) ──────
            # Compute hunger fraction from the detected hunger_bar bbox width.
            _hbar = next((o for o in objects if o.get('label') == 'hunger_bar'), None)
            if _hbar and _hbar.get('box') and len(_hbar['box']) == 4:
                _hw = _hbar['box'][2] - _hbar['box'][0]
                _hmax = hunger_behavior._max_width if hunger_behavior._max_width > 0 else max(_hw, 1)
                _hunger_frac = _hw / _hmax
            else:
                _hunger_frac = 1.0   # assume full when not visible

            # Goal-aware FSM gating: the FSM has no notion of the active
            # goal and always prefers ore over trees (see core/fsm.py
            # priority order) — that's what let AKSUMAEL keep mining ore
            # underground while its goal was find_and_chop_tree. When the
            # goal wants a tree and none is in view this tick, hide ore
            # detections from the FSM so it falls through to EXPLORE
            # (walk + scan) instead of committing to an ore target.
            _fsm_objects = objects
            if _goal_category(goals.current_goal()) == 'tree':
                _tree_in_view = any(
                    o.get('label', '').lower() in TREE_TARGETS for o in objects)
                if not _tree_in_view:
                    _fsm_objects = [
                        o for o in objects
                        if o.get('label', '').lower() not in ORE_TARGETS
                    ]

            fsm_state, fsm_action = fsm.tick(_fsm_objects, world_mem, _hunger_frac)

            # Mining/chopping is driven per-tick by the FSM (continuous aim
            # correction) so the LLM doesn't need to think as often there —
            # check in every LLM_EVERY_N_TICKS_MINE ticks instead of the
            # faster EXPLORE/EAT cadence. Saves API cost and keeps mining
            # ticks fast (no LLM round-trip most ticks).
            _llm_interval = (config.LLM_EVERY_N_TICKS_MINE if fsm_state == State.MINE
                              else config.LLM_EVERY_N_TICKS)

            # ── Scan / identify / pathfinder ──────────────────
            # Continuous watch: runs every SCAN_COOLDOWN_TICKS whenever not
            # mid-skill-replay. Sweep is fast (~4s); zoom+identify only fires
            # when YOLO actually spotted a danger label.
            _replay_active_now = replayer.is_active()
            if _prev_replay_active and not _replay_active_now:
                # Skill postcondition verification — inventory diff vs the
                # snapshot taken when this replay started (see skill-firing
                # block below). Skills with no postconditions always pass.
                if _verify_skill is not None:
                    try:
                        skills.verify_replay(_verify_skill, _verify_inv_before, dict(inventory.items))
                    except Exception as e:
                        print(f'[SKILL] verify error: {e}')
                    _verify_skill = None

                if _is_mining_skill(last_skill_name):
                    print(f'[CAMERA] {last_skill_name} replay ended — '
                          f'resetting pitch toward horizon over {_PITCH_RESET_TICKS} ticks '
                          f'(total dy={_MINE_PITCH_RESET_DY})')
                    _pitch_reset_ticks_left = _PITCH_RESET_TICKS
                    _pitch_reset_dy_step    = _MINE_PITCH_RESET_DY // _PITCH_RESET_TICKS
            _prev_replay_active = _replay_active_now

            # Spread the post-mining pitch correction over several ticks
            # instead of one big jerk that overshoots past the horizon.
            if _pitch_reset_ticks_left > 0 and not replayer.is_active():
                executor.execute({'look': {'dx': 0, 'dy': _pitch_reset_dy_step},
                                   'source': 'pitch_reset'})
                world_mem.cumulative_pitch_dy = (
                    getattr(world_mem, 'cumulative_pitch_dy', 0) + _pitch_reset_dy_step
                )
                _pitch_reset_ticks_left -= 1

            if (not replayer.is_active()
                    and not _menu_open
                    and not _f3_open
                    and (tick - _last_scan_tick) >= config.SCAN_COOLDOWN_TICKS):
                # Moving forward ('w' held) and not stuck → narrower 270°
                # sweep centered on the heading. Standing still, moving in
                # any other direction, or stuck (low-reward streak building)
                # → full 360° sweep since a threat could close in from any side.
                _moving_forward = last_action.get('key') == 'w'
                _getting_stuck  = _low_reward_streak >= (_STUCK_TICKS // 2)
                _scan_result = scanner.run(world_mem, target_bearing=0,
                                           full_sweep=not _moving_forward or _getting_stuck)
                for _t in (_scan_result or {}).get('threats', []):
                    _ident = _t.get('identified', {})
                    if _ident.get('threat'):
                        world.mark_threat(_t.get('label', 'unknown'),
                                          bearing=_t.get('bearing', 0),
                                          confidence=_t.get('conf', 0.0))
                _last_scan_tick = tick

            # ── Decision ──────────────────────────────────────
            action_dict = _idle()
            used_skill  = None
            src_tag     = 'idle'

            if replayer.is_active():
                name = replayer._current.name[:10] if replayer._current else '?'
                src_tag = f'REPLAY:{name}'

            elif _menu_open:
                # Menu/inventory is open — any skill or FSM action would corrupt
                # the UI. Stay idle until the menu is closed.
                src_tag = 'MENU'

            elif (_night_ad := night_survival.update(
                    world_mem,
                    inv_reader.read(force=False) if inv_reader._cache_ts > 0 else {},
                    tick)) is not None:
                # Dusk/night handling (pillar up / dig in / wait for dawn /
                # descend) pre-empts skills/FSM/LLM entirely — it already
                # dispatched its own key/click actions directly.
                action_dict = _night_ad
                src_tag = f'NIGHT:{night_survival._state}'

            else:
                # Try a learned skill first — if multiple candidates match,
                # let the RL policy pick among them instead of the naive best.
                candidates = skills.find_candidates(objects)

                # ── Goal-aware skill gating ───────────────────────
                # When the active goal is crafting-related, suppress mining
                # and pure-movement skills so AKSUMAEL doesn't keep digging
                # instead of seeking a crafting table.
                _cur_goal = goals.current_goal()
                _crafting_goal = goals.is_craft_goal(_cur_goal) or _cur_goal == 'find_crafting_table'
                if _crafting_goal and candidates:
                    _pre = len(candidates)
                    candidates = [
                        (sk, m) for sk, m in candidates
                        if not (sk.name.startswith('mine_')
                                or sk.name.startswith('coal_ore')
                                or sk.name.startswith('iron_ore'))
                    ]
                    if len(candidates) < _pre:
                        print(f'[SKILL] goal={_cur_goal}: suppressed '
                              f'{_pre - len(candidates)} mining skill(s)')

                # ── Goal-skill allow-list gating ──────────────────
                # Drop any candidate that isn't positively tied to the
                # active goal's category (e.g. an ore-mining skill while
                # the goal is find_and_chop_tree) — see
                # _skill_allowed_for_goal for why this matters.
                _goal_cat = _goal_category(_cur_goal)
                if _goal_cat and candidates:
                    _pre_gc = len(candidates)
                    candidates = [
                        (sk, m) for sk, m in candidates
                        if _skill_allowed_for_goal(_cur_goal, sk)
                    ]
                    if len(candidates) < _pre_gc:
                        print(f'[SKILL] goal={_cur_goal} (cat={_goal_cat}): dropped '
                              f'{_pre_gc - len(candidates)} unrelated skill(s)')

                # ── Tree-goal fallback: forward momentum while LLM thinks ──
                # No tree skills learned yet + no candidates left standing
                # still every tick waiting on mesh-llm (which can be slow
                # or produce nothing usable — see 2026-07-15 "all LLM tiers
                # failed" idle loop). Walk forward and pan the camera to
                # scan for trees instead of freezing.
                #
                # Walking straight ahead is useless if AKSUMAEL is stuck
                # inside a village building — see 2026-07-15 incident where
                # it wedged itself in a house and kept walking into the same
                # wall. Check F3 position before/after each walk attempt; if
                # it hasn't moved, rotate 90° and try again before falling
                # back to a jump (steps over a 1-block-high lip/slab).
                if (_goal_cat == 'tree' and not candidates
                        and not replayer.is_active()
                        and (tick - _last_tree_fallback_tick) >= config.SCAN_COOLDOWN_TICKS):
                    print('[TREE-FALLBACK] no tree skills/candidates — '
                          'walking forward (rotating if stuck) to scan for trees')
                    _fb_px, _fb_pz = world_mem.pos_x, world_mem.pos_z
                    _fb_unstuck = False
                    for _fb_attempt in range(3):
                        if _fb_attempt > 0:
                            executor.execute({'look': {'dx': 90, 'dy': 0}, 'source': 'tree_fallback'})
                        executor.execute({'key': 'w', 'delay_ms': 2000, 'source': 'tree_fallback'})
                        _new_px, _new_pz = _read_f3_position_now()
                        if _f3_says_moved(_fb_px, _fb_pz, _new_px, _new_pz):
                            print(f'[TREE-FALLBACK] position changed (or F3 read '
                                  f'unavailable — assuming moved) after '
                                  f'{_fb_attempt + 1} attempt(s)')
                            _fb_unstuck = True
                            break
                        print(f'[TREE-FALLBACK] stuck (position unchanged) '
                              f'after attempt {_fb_attempt + 1}')
                        _fb_px, _fb_pz = _new_px, _new_pz
                    if not _fb_unstuck:
                        print('[TREE-FALLBACK] still stuck — jumping + forward')
                        executor.execute({'key': 'space', 'delay_ms': 300, 'source': 'tree_fallback'})
                        executor.execute({'key': 'w', 'delay_ms': 2000, 'source': 'tree_fallback'})
                        _new_px, _new_pz = _read_f3_position_now()
                        if _f3_says_moved(_fb_px, _fb_pz, _new_px, _new_pz):
                            print('[TREE-FALLBACK] jump + forward unstuck it '
                                  '(or F3 read unavailable — assuming moved)')
                        else:
                            # Jump+forward didn't clear it either — likely wedged
                            # against a wall/door inside a building (see
                            # 2026-07-15 incident, stuck at a fixed position with
                            # W/rotate/jump all failing). Dig straight through
                            # whatever's blocking by holding left-click (break)
                            # at the crosshair, then try walking again.
                            print('[TREE-FALLBACK] still stuck after jump — '
                                  'digging out (holding left-click ~1.5s to '
                                  'break block ahead)')
                            executor.execute({'click': [50.0, 50.0], 'button': 'left',
                                              'delay_ms': 1500, 'source': 'tree_fallback'})
                            executor.execute({'key': 'w', 'delay_ms': 2000, 'source': 'tree_fallback'})
                    executor.execute({'look': {'dx': -40, 'dy': 0}, 'source': 'tree_fallback'})
                    executor.execute({'look': {'dx': 80, 'dy': 0}, 'source': 'tree_fallback'})
                    executor.execute({'look': {'dx': -40, 'dy': 0}, 'source': 'tree_fallback'})
                    _last_tree_fallback_tick = tick

                # ── Precondition gating (Voyager-style verification) ──
                # Skills without preconditions always pass (old skills keep
                # working unchanged). Uses the cached inventory read — never
                # forces a fresh (expensive) inventory open just to gate.
                if candidates:
                    _precond_inv = (inv_reader.read(force=False)
                                     if inv_reader._cache_ts > 0 else dict(inventory.items))
                    _pre2 = len(candidates)
                    candidates = [
                        (sk, m) for sk, m in candidates
                        if sk.check_preconditions(_precond_inv, objects)
                    ]
                    if len(candidates) < _pre2:
                        print(f'[SKILL] preconditions filtered out '
                              f'{_pre2 - len(candidates)} candidate(s)')

                if len(candidates) > 1:
                    names = [sk.name for sk, _ in candidates]
                    chosen_name = rl.choose_skill(names, objects)
                    by_name = {sk.name: (sk, m) for sk, m in candidates}
                    skill, match = by_name.get(chosen_name, (None, 0.0))
                elif candidates:
                    skill, match = candidates[0]
                elif not _crafting_goal:
                    # No candidates after goal filter — fall through to FSM/LLM
                    skill, match = skills.find_best(objects)
                    if skill and not _skill_allowed_for_goal(_cur_goal, skill):
                        print(f'[SKILL] goal={_cur_goal}: {skill.name} is unrelated '
                              f'to this goal — skipping, asking LLM to reconsider')
                        skill, match = None, 0.0
                        force_llm_reconsider = True
                else:
                    skill, match = None, 0.0

                # ── Skill-replay escape ───────────────────────────
                # A skill that keeps matching but scores near-zero
                # confidence (avg_reward ~0) isn't accomplishing anything —
                # blindly replaying it in a loop is what left AKSUMAEL
                # underground repeating a gold_ore skill forever. Track
                # consecutive low-confidence fires of the SAME skill and
                # give up on it after LOW_CONF_SKILL_TICKS, forcing a real
                # LLM re-plan instead of repeating it again.
                if skill:
                    _fire_conf = min(0.95, skill.avg_reward)
                    if skill.name == low_conf_skill_name and _fire_conf <= LOW_CONF_SKILL_THRESH:
                        low_conf_skill_streak += 1
                    elif _fire_conf <= LOW_CONF_SKILL_THRESH:
                        low_conf_skill_name   = skill.name
                        low_conf_skill_streak = 1
                    else:
                        low_conf_skill_name   = None
                        low_conf_skill_streak = 0

                    if low_conf_skill_streak >= LOW_CONF_SKILL_TICKS:
                        print(f'[SKILL] {skill.name} stuck at <= {LOW_CONF_SKILL_THRESH} confidence '
                              f'for {low_conf_skill_streak} ticks — aborting replay, '
                              f'asking LLM to reconsider')
                        skill_cooldown_name       = skill.name
                        skill_cooldown_until_tick = tick + SKILL_COOLDOWN_TICKS
                        skill, match          = None, 0.0
                        low_conf_skill_name   = None
                        low_conf_skill_streak = 0
                        last_skill_name       = None
                        same_skill_count      = 0
                        force_llm_reconsider  = True

                # A skill already serving a cooldown stays suppressed until
                # skill_cooldown_until_tick, regardless of last_skill_name —
                # previously the cooldown reset last_skill_name to None on the
                # very tick it fired, so the skill could (and did) re-fire on
                # the next tick since the "== last_skill_name" check no longer
                # matched. That made the cooldown last exactly one tick.
                if skill and skill.name == skill_cooldown_name and tick < skill_cooldown_until_tick:
                    skill = None
                elif skill and skill.name == last_skill_name and same_skill_count >= SAME_SKILL_LIMIT:
                    # Mining skills get a higher repeat limit — ore takes many clicks to break.
                    # Non-mining skills cool down after SAME_SKILL_LIMIT fires.
                    _is_mining = skill.name.startswith('mine_')
                    _limit = 12 if _is_mining else SAME_SKILL_LIMIT
                    if same_skill_count >= _limit:
                        print(f'[SKILL] cooldown: {skill.name} fired {same_skill_count}x in a row, '
                              f'suppressing for {SKILL_COOLDOWN_TICKS} ticks')
                        skill_cooldown_name       = skill.name
                        skill_cooldown_until_tick = tick + SKILL_COOLDOWN_TICKS
                        skill = None
                        last_skill_name  = None
                        same_skill_count = 0

                # ── Neural policy (opt-in) ─────────────────────────
                # No skill has fired at this point — before falling through
                # to FSM/LLM, let the low-level neural policy propose an
                # action and blend it with "no rule fired" via
                # core/policy_blender.py. Only overrides when the net is
                # confident (see NEURAL_CONF_THRESHOLD); otherwise
                # _neural_action_dict stays None and the FSM/LLM chain below
                # runs exactly as it did before this feature existed.
                _neural_action_dict = None
                _neural_src_tag     = None
                if (config.NEURAL_POLICY_ENABLED and neural_policy is not None
                        and not (skill and match >= skills.MIN_MATCH_SCORE)):
                    try:
                        _obs_feat  = feature_extractor.extract_obs_features(objects, last_action)
                        _goal_feat = feature_extractor.extract_goal_embedding(goals.current_goal())
                        _neural    = neural_policy.select_action(_obs_feat, _goal_feat)
                        _blended   = policy_blender.blend(
                            neural_action=_neural['action_dict'], rule_action=None,
                            confidence=_neural['confidence'], skill_active=False,
                            episode_count=rl_trainer.episode_count if rl_trainer else 0)
                        if _blended is not None:
                            _neural_action_dict = {**_blended,
                                                    'action':     f"neural:{_neural['action_name']}",
                                                    'confidence': _neural['confidence']}
                            _neural_src_tag = f"NEURAL:{_neural['action_name']}"
                            if rl_trainer is not None:
                                _value = float(neural_policy.value(_obs_feat, _goal_feat).item())
                                _pending_neural_transition = (
                                    _obs_feat, _goal_feat, _neural['action_idx'],
                                    _neural['log_prob'], _value)
                    except Exception as e:
                        print(f'[NEURAL_POLICY] tick error: {e}')

                # Hard gate, re-checked immediately before execution — every
                # path above that can set `skill` (candidates, RL choice,
                # find_best fallback) already filters on the goal, but this
                # is the one check that actually blocks a replay/code-skill
                # from firing, rather than just warning about it. This is
                # what closes the "warns then executes anyway" gap: any
                # future selection path that forgets to goal-filter still
                # can't get a mismatched skill onto the executor.
                if skill and not _skill_allowed_for_goal(_cur_goal, skill):
                    print(f'[SKILL] goal={_cur_goal}: blocking replay of '
                          f'{skill.name} — fails goal-skill hard gate')
                    skill, match = None, 0.0

                if skill and match >= skills.MIN_MATCH_SCORE:
                    same_skill_count = same_skill_count + 1 if skill.name == last_skill_name else 1
                    last_skill_name  = skill.name
                    # Find the box of the trigger object so the aim phase
                    # can centre the crosshair on it before action steps run.
                    # When several detections match the trigger labels (e.g. an
                    # ore box plus a lower-confidence duplicate), prefer the
                    # highest-confidence one so the skill actually aims at the
                    # real ore instead of whichever box happened to come first.
                    aim_box = None
                    _aim_candidates = [
                        obj for obj in objects
                        if any(obj.get('label', '').lower() == t.lower() or
                               obj.get('label', '').lower() in t.lower() or
                               t.lower() in obj.get('label', '').lower()
                               for t in skill.trigger_objects)
                    ]
                    if _aim_candidates:
                        aim_box = max(_aim_candidates,
                                      key=lambda o: o.get('conf', 0.0)).get('box')

                    # Code skills (LLM-generated Python, more robust than a
                    # fixed key sequence) are tried first when enabled;
                    # recorded-sequence replay is always the fallback.
                    _code_ran = False
                    if config.ENABLE_CODE_SKILLS:
                        try:
                            _code_ran = code_skill_generator.run_code_skill(
                                skill.name, executor, world, objects)
                        except Exception as e:
                            print(f'[CODE_SKILL] error running {skill.name}: {e}')
                        if _code_ran:
                            skill.record_outcome(True)
                            skills.save(skill)
                            print(f'[SKILL] {skill.name} executed via code skill')

                    if not _code_ran:
                        _verify_skill      = skill
                        _verify_inv_before = dict(inventory.items)
                        replayer.start(skill, aim_box=aim_box, aim_ctrl=aim_ctrl)
                    skills.mark_used(skill)
                    inventory.on_skill_fired(skill.name)
                    if skill.name.startswith('mine_'):
                        world_mem.record_pickaxe_use()
                    used_skill = skill
                    src_tag    = f'SK:{skill.name[:12]}'
                    action_dict.update({
                        'action':      f'skill:{skill.name}',
                        'confidence':  min(0.95, skill.avg_reward),
                        'observation': f'skill match {match:.2f} {skill.name}',
                    })

                # Neural policy override — only reached when no skill fired
                # and the blender (called above) decided the net was
                # confident enough to act on its own this tick. Skipped when
                # a skill/goal mismatch just forced an LLM reconsideration —
                # that needs an actual fresh decision, not a low-level
                # policy override.
                elif not force_llm_reconsider and _neural_action_dict is not None:
                    action_dict = _neural_action_dict
                    src_tag = _neural_src_tag

                # FSM takes over when no skill matched and FSM is actively
                # targeting something (APPROACH / COMBAT / COLLECT / INTERACT /
                # FISH / HUNT / FARM) — these need continuous per-tick control
                # and never consult the LLM. MINE also gets continuous FSM
                # control (per-tick aim+click) EXCEPT on the slower
                # LLM_EVERY_N_TICKS_MINE cadence, when it falls through to the
                # LLM as an occasional strategic check-in. In pure EXPLORE or
                # EAT we fall through to the LLM every LLM_EVERY_N_TICKS so it
                # can direct higher-level decisions.
                elif not force_llm_reconsider and fsm_state not in (State.EXPLORE, State.EAT) and (
                        fsm_state != State.MINE or tick % _llm_interval != 0):
                    action_dict = fsm_action
                    src_tag = f'FSM:{fsm_state.value}'

                # Fall back to LLM — EXPLORE/EAT every LLM_EVERY_N_TICKS, or
                # MINE every LLM_EVERY_N_TICKS_MINE (see _llm_interval above).
                # force_llm_reconsider (skill-replay escape) bypasses both the
                # cadence gate and the frame-diff skip below — a skill/goal
                # mismatch was just aborted and needs an actual fresh decision,
                # not the cached last_action reused verbatim.
                elif force_llm_reconsider or tick % _llm_interval == 0:
                    _reconsider = force_llm_reconsider
                    force_llm_reconsider = False   # consume the one-shot flag
                    # Frame-diff gate: skip call if scene hasn't changed enough
                    _scene_changed = True
                    if (not _reconsider and _last_llm_frame is not None
                            and frame is not None
                            and frame.shape == _last_llm_frame.shape):
                        _diff = cv2.absdiff(frame, _last_llm_frame)
                        if _diff.mean() < 8.0:
                            _scene_changed = False
                            action_dict = last_action   # reuse previous response
                            src_tag = 'LLM-SKIP'

                    if _scene_changed:
                        _last_llm_frame = frame.copy() if frame is not None else None
                        # Base history — always injected (cheap, 3-4 lines)
                        history = (progression.context_summary() + '\n'
                                   + world_mem.context_summary() + '\n'
                                   + world_mem.scan_summary() + '\n'
                                   + inventory.context_summary() + '\n'
                                   + goals.context_summary() + '\n'
                                   + world.recent_summary(n=3))

                        if _reconsider:
                            history += (
                                '\nIMPORTANT: a learned skill was just aborted because it '
                                'was stuck at ~0 confidence or unrelated to the current goal '
                                '— whatever was just being repeated is not working. '
                                'Reconsider your approach from scratch for the current goal.')

                        # Inner monologue (last real thought) — cheap, no
                        # extra API call here, just replays the last LLM
                        # thought generated on its own 50-tick cadence.
                        _thought = cognitive.monologue.recent(n=1)
                        if _thought:
                            history += f'\n[THOUGHT] {_thought}'

                        # Episode memory — past attempts at a similar goal,
                        # JARVIS-1 style ("last time you tried X..."). Local
                        # lookup only, no API cost.
                        _episode_hint = episodes.context_snippet(
                            goals.current_goal(), dict(inventory.items))
                        if _episode_hint:
                            history += f'\n{_episode_hint}'

                        # Chest contents at base — cheap, keeps Claude aware
                        # of stored materials without re-opening the chest.
                        if _near_base and world_mem.chest_inv:
                            _chest_summary = ', '.join(
                                f"{k}:{(v.get('count', 0) if isinstance(v, dict) else v)}"
                                for k, v in list(world_mem.chest_inv.items())[:8]
                            )
                            history += f'\nChest at base contains: {_chest_summary}'

                        # Goal-specific navigation hint injected into prompt
                        _g = goals.current_goal()
                        if goals.is_craft_goal(_g):
                            _table_visible = any(
                                o.get('label') == 'crafting_table' for o in objects)
                            if _table_visible:
                                history += ('\nIMPORTANT: crafting table is visible — '
                                            'walk up to it (W) and right-click to open it.')
                            else:
                                history += ('\nIMPORTANT: goal is to craft a pickaxe. '
                                            'No crafting table in view. Explore (W/turn) '
                                            'to find one. Spawn is near X=-6 Z=-3.')
                        elif _g == 'explore':
                            _px, _pz = world_mem.pos_x, world_mem.pos_z
                            if _px is not None and _pz is not None:
                                _dx = (world_mem.pos_x or 0) - BASE_X
                                _dz = (world_mem.pos_z or 0) - BASE_Z
                                _dist_from_spawn = (_dx ** 2 + _dz ** 2) ** 0.5
                                if _dist_from_spawn > 50:
                                    history += (
                                        f'\nNavigate toward base: you are at X={world_mem.pos_x:.0f} '
                                        f'Z={world_mem.pos_z:.0f}, base is at X=-6 Z=-3. '
                                        f'Face the direction that decreases your distance and walk (w).')
                            else:
                                history += ('\nPosition unknown — press F3 briefly to get '
                                            'coordinates, then navigate.')
                            if 50 < _low_reward_streak < 150:
                                history += ('\nYou seem stuck. Try turning sharply '
                                            '(look dx=60) then sprint (ctrl+w).')
                        elif _g == 'return_to_base':
                            # Just respawned — dropped items are back where we died,
                            # but base (chest/crafting table/known resources) is the
                            # safest place to re-equip before heading out again.
                            if world_mem.pos_x is not None and world_mem.pos_z is not None:
                                history += (
                                    f'\nIMPORTANT: you just respawned. Return to base: '
                                    f'you are at X={world_mem.pos_x:.0f} Z={world_mem.pos_z:.0f}, '
                                    f'base is at X=-6 Z=-3. Face the direction that decreases '
                                    f'your distance and walk (w). Press F3 if position is stale.')
                            else:
                                history += ('\nIMPORTANT: you just respawned and must return to '
                                            'base at X=-6 Z=-3. Press F3 briefly to get '
                                            'coordinates, then navigate there.')
                        # Strategic tick: inject full phase mechanics + discoveries
                        # every 100 ticks or when Claude is uncertain
                        _is_strategic = (
                            tick % (_llm_interval * 6) == 0
                            or last_action.get('confidence', 1.0) < 0.35
                        )
                        if _is_strategic:
                            history = (mc_kb.strategic_context(progression.phase)
                                       + '\n\n' + history)
                        if tick % (_llm_interval * 10) == 0:
                            history = world.cross_session_summary() + '\n' + history
                        action_dict = ask_vision(frame, history, objects, phase=progression.phase)
                        _llm_call_count += 1
                        world_mem.record_llm_call()
                        src_tag = 'LLM'
                        last_action = action_dict
                        if action_dict.get('goal'):
                            behavior = goal_interp.interpret(action_dict['goal'], objects)
                            if behavior:
                                goal_interp.execute_behavior(behavior, executor, objects)
                        if action_dict.get('discovery'):
                            mc_kb.add_discovery(action_dict['discovery'], tick)
                        if tick % (config.LLM_EVERY_N_TICKS * 5) == 0:
                            tts.say_observation(
                                action_dict.get('observation', ''),
                                action_dict.get('confidence', 0))

                        # Claude-vision skill override: Claude's own text
                        # says it sees diamond ore, but YOLO produced no
                        # diamond_ore box this tick — force a mine action
                        # rather than trust the (missing) detection.
                        if config.VISION_SKILL_OVERRIDE:
                            obs_text = (action_dict.get('observation', '') + ' '
                                        + action_dict.get('action', '')).lower()
                            yolo_labels = {o.get('label') for o in objects}
                            if 'diamond' in obs_text and 'diamond_ore' not in yolo_labels:
                                print('[OVERRIDE] Claude sees diamond ore, YOLO missed it — forcing mine action')
                                action_dict.update({
                                    'key':    'w',
                                    'click':  'left',
                                    'action': 'mine diamond ore (vision override)',
                                })
                                src_tag = 'VISION_OVERRIDE'

                # Between LLM ticks in EXPLORE: carry last action forward
                # so AKSUMAEL keeps walking/acting instead of going idle for 14 ticks.
                # Carry movement keys AND look deltas (but not one-shot actions like
                # 'e', 'f3', or 'esc' which should only fire once).
                else:
                    _carry_key = last_action.get('key')
                    _no_carry  = {'e', 'f3', 'esc', 'escape', 'f', 'q', None}
                    if _carry_key not in _no_carry or last_action.get('look') or last_action.get('click'):
                        action_dict = {**last_action}
                        # One-shot keys (esc/e/f3/...) must never repeat across
                        # carry ticks even when look/click is what let this
                        # branch through — strip it so only look/click carry.
                        if _carry_key in _no_carry:
                            action_dict['key'] = None
                        # Dampen look delta on carry ticks to avoid spin
                        if action_dict.get('look'):
                            lk = action_dict['look']
                            action_dict['look'] = {'dx': lk.get('dx', 0) // 2,
                                                   'dy': lk.get('dy', 0) // 2}
                        src_tag = 'CARRY'

            # ── Death/respawn detection ─────────────────────────
            if respawner.update(objects, last_observation=last_action.get('observation', '')):
                world_mem.record_death()
                continue

            # ── Hunger ───────────────────────────────────────────
            hunger_behavior.update(objects, world_mem=world_mem)

            # ── Crafting ─────────────────────────────────────────
            # 3x3: trigger when (a) pickaxe nearing end of life, OR
            #                   (b) a craft_* goal is active —
            #      in either case only when a crafting table is visible.
            _craft_goal_active = goals.has_craft_goal()
            _craft_condition   = (
                world_mem.pickaxe_uses > config.PICKAXE_DURABILITY * 0.8
                or _craft_goal_active
            )
            if (_craft_condition
                    and not replayer.is_active()
                    and crafting_behavior.should_trigger(objects)):
                crafting_behavior.run(objects=objects)
            # 2x2: trigger proactively (no table needed) — e.g. turn logs→planks
            # or planks→sticks so we're ready when a table appears.
            elif (not replayer.is_active()
                    and crafting_behavior.should_trigger_2x2()):
                crafting_behavior.run(objects=objects)

            # ── Chest interaction at base ────────────────────────
            # Only in EXPLORE, only near base, only when a chest is actually
            # visible — reading it costs a Claude call, so cooldown-gate it.
            _chest_visible = any(o.get('label') == 'chest' for o in objects)
            if (fsm_state == State.EXPLORE and _chest_visible and _near_base
                    and not replayer.is_active() and not _menu_open
                    and (tick - _last_chest_tick) >= CHEST_COOLDOWN_TICKS):
                print('[CHEST] chest detected near base — interacting')
                chest_frame = chest_mgr.open(executor, capture_fn=lambda: pipeline.latest_raw_frame)
                chest_items = chest_mgr.read_contents(chest_frame, force=True)
                if chest_items:
                    world_mem.chest_inv = {**world_mem.chest_inv, **chest_items}
                    print(f'[CHEST] merged contents: {chest_items}')
                chest_mgr.close(executor)
                _last_chest_tick = tick

            # ── Torch placement ──────────────────────────────────
            # Dark area (night or below TORCH_DARK_Y_LEVEL) — place a torch
            # every TORCH_COOLDOWN_SEC to keep mobs from spawning nearby.
            # Only in EXPLORE, never mid-skill-replay or while sheltering.
            if (fsm_state == State.EXPLORE and not replayer.is_active()
                    and not _menu_open and not night_survival.is_active()
                    and torch_behavior.should_trigger(world_mem)):
                torch_behavior.place()

            # ── Curiosity survey ────────────────────────────────
            # Only survey in EXPLORE/EAT — never interrupt MINE, COMBAT, FISH, etc.
            if surveyor and fsm_state in (State.EXPLORE, State.EAT, None):
                llm_conf = last_action.get('confidence', 1.0)
                if surveyor.should_trigger(objects, llm_conf):
                    surveyor.run(frame, objects)
                    world_mem.record_survey()

            # ── Controller blend ───────────────────────────────
            if not replayer.is_active():
                router.update_aksumael(action_dict)
                final = router.resolve()
                executor.execute(final)
            else:
                router.update_aksumael(action_dict)
                final = action_dict
                final['source'] = src_tag

            # ── Pitch drift clamp ──────────────────────────────
            # Track every look-dy actually dispatched this tick (FSM aim,
            # skill-fire, LLM, carry) so slow pitch walk over a long session
            # gets nudged back before it compounds into a stuck-looking-at-sky
            # or stuck-looking-at-feet camera that per-skill resets alone miss.
            _final_look = final.get('look') if isinstance(final, dict) else None
            if _final_look and _final_look.get('dy'):
                world_mem.cumulative_pitch_dy = (
                    getattr(world_mem, 'cumulative_pitch_dy', 0) + _final_look['dy']
                )
            _cum_pitch_dy = getattr(world_mem, 'cumulative_pitch_dy', 0)
            if abs(_cum_pitch_dy) > _PITCH_CLAMP_LIMIT and not replayer.is_active():
                _pitch_correction = -_PITCH_CLAMP_NUDGE if _cum_pitch_dy > 0 else _PITCH_CLAMP_NUDGE
                print(f'[CAMERA] cumulative pitch dy={_cum_pitch_dy} past clamp '
                      f'±{_PITCH_CLAMP_LIMIT} — nudging back toward centre '
                      f'({_pitch_correction})')
                executor.execute({'look': {'dx': 0, 'dy': _pitch_correction},
                                   'source': 'pitch_clamp'})
                world_mem.cumulative_pitch_dy = _cum_pitch_dy + _pitch_correction

            # ── World model ────────────────────────────────────
            # Spatial memory: remember where ores were spotted (needs a
            # known position — F3 reads are periodic, so this is a no-op
            # most ticks between reads) and expire stale scanned threats.
            if world.position is not None:
                for o in objects:
                    _lbl = o.get('label', '')
                    if _lbl in ORE_TARGETS:
                        world.mark_ore(_lbl, world.position)
            world.retire_stale_threats(tick)

            world.update({
                'objects': objects,
                'action':  final.get('key') or action_dict.get('action', 'wait'),
            })

            # ── Reward ────────────────────────────────────────
            reward.add_hud_reward(_hud_reward(objects, prev_objects))
            r = reward.compute({'objects': objects}, action_dict)
            rl.update(r, objects)
            prev_objects = objects

            # ── Neural policy training (opt-in) ─────────────────
            # Pairs this tick's action (if the neural policy produced one
            # above) with this tick's reward and hands it to the background
            # PPO trainer; tick() advances its own counter toward the next
            # config.RL_TRAIN_EVERY_N_TICKS update.
            if rl_trainer is not None:
                if _pending_neural_transition is not None:
                    rl_trainer.record(*_pending_neural_transition, r)
                    _pending_neural_transition = None
                rl_trainer.tick()

            # ── Anti-stuck ────────────────────────────────────
            # If reward has been near zero for too long, force a goal reset
            # and cancel any active skill replay so AKSUMAEL tries something new.
            if r < _LOW_REWARD_THRESH:
                _low_reward_streak += 1
            else:
                _low_reward_streak = 0
            if _low_reward_streak >= _STUCK_TICKS and not replayer.is_active():
                print(f'[STUCK] {_low_reward_streak} ticks below {_LOW_REWARD_THRESH:.2f} '
                      f'— resetting goal to explore and clearing skill cooldown')
                goals.current = 'explore'
                goals.save()
                skill_cooldown_name       = None
                skill_cooldown_until_tick = 0
                last_skill_name           = None
                same_skill_count          = 0
                _low_reward_streak        = 0
                # Give a random look to unstick the camera
                executor.execute({'look': {'dx': 45, 'dy': 20}, 'source': 'unstuck'})

            # ── Secondary anti-stuck: inventory + goal frozen ───────────
            # The reward-streak check above can miss slow-grind loops (e.g.
            # bumping into the same wall) that still generate occasional
            # positive reward ticks. This catches those by watching cached
            # inventory contents (no extra I/O — reads the existing cache,
            # never forces a fresh inventory-open) and the active goal: if
            # neither has changed in _INVENTORY_STUCK_TICKS, something is
            # wrong even though reward looked fine.
            _cur_inv_snapshot = {
                k: (v.get('count', 0) if isinstance(v, dict) else v)
                for k, v in inv_reader._cache.items()
            }
            if _last_inv_snapshot is None or _cur_inv_snapshot != _last_inv_snapshot:
                _last_inv_snapshot    = _cur_inv_snapshot
                _last_inv_change_tick = tick

            _cur_goal_for_stuck = goals.current_goal()
            if _last_goal_for_stuck is None or _cur_goal_for_stuck != _last_goal_for_stuck:
                _last_goal_for_stuck   = _cur_goal_for_stuck
                _last_goal_change_tick = tick

            if (tick - _last_inv_change_tick >= _INVENTORY_STUCK_TICKS
                    and tick - _last_goal_change_tick >= _INVENTORY_STUCK_TICKS
                    and not replayer.is_active()):
                print(f'[STUCK] inventory+goal unchanged for '
                      f'{_INVENTORY_STUCK_TICKS}+ ticks (goal={_cur_goal_for_stuck}) '
                      f'— forcing direction change + goal reassessment')
                _turn_dir = random.choice((-1, 1))
                executor.execute({
                    'key': random.choice(('a', 'd')),
                    'look': {'dx': _turn_dir * random.randint(60, 120), 'dy': 0},
                    'source': 'inventory_unstuck',
                })
                goals.current = 'explore'
                goals.save()
                skill_cooldown_name       = None
                skill_cooldown_until_tick = 0
                last_skill_name           = None
                same_skill_count          = 0
                _low_reward_streak        = 0
                # Reset the trackers so this doesn't re-fire every tick
                # until real progress (or a real goal change) happens again.
                _last_inv_change_tick  = tick
                _last_goal_change_tick = tick

            # ── RL policy bookkeeping + status summary ──────────
            if tick % 100 == 0:
                rl.save()
                _active_goal = goals.current_goal() or 'none'
                _inv_snap    = inv_reader.read(force=False) if inv_reader._cache_ts > 0 else {}
                _inv_str     = ', '.join(f'{k}:{v}' for k, v in list(_inv_snap.items())[:6]) or 'unknown'
                _skill_count = len(skills.skills)
                print(f'[STATUS] tick={tick} | goal={_active_goal} | '
                      f'inv=[{_inv_str}] | skills={_skill_count} | '
                      f'llm_calls={_llm_call_count} | pickaxe_uses={world_mem.pickaxe_uses} | '
                      f'phase={progression.phase}')
            if tick % 200 == 0:
                print(f'[RL] {rl.stats()}')

            # ── Cognitive architecture ──────────────────────────
            cognitive.update(tick, objects, action_dict, r,
                              goal=goals.current_goal(), recent_episodes=episodes.episodes[-5:])

            # ── Skill mining ───────────────────────────────────
            if used_skill is None and not replayer.is_active():
                mined = skills.observe(objects, final, r)
                if mined:
                    tts.say_line('skill_learned')
                    if config.ENABLE_CODE_SKILLS and mined.uses <= 1:
                        try:
                            _code = code_skill_generator.generate_code_skill(
                                mined.name, [s.action for s in mined.steps],
                                context=f'triggers on {mined.trigger_objects}')
                            if _code:
                                code_skill_generator.save_code_skill(mined.name, _code)
                                print(f'[CODE_SKILL] generated for {mined.name}')
                        except Exception as e:
                            print(f'[CODE_SKILL] generation error: {e}')

            # ── Skill pruning ───────────────────────────────────
            if tick % 50 == 0:
                skills.prune_bad()

            # ── Skill evolution (proven/blacklist/merge pass) ───
            if tick % config.SKILL_EVOLVE_TICKS == 0:
                try:
                    skills.evolve_skills()
                except Exception as e:
                    print(f'[SKILL] evolve error: {e}')

            # ── F3 debug overlay OCR ─────────────────────────────
            # Guard: only open F3 when HUD is present and no menu is open.
            # Track _f3_open so we never send inputs while it's up.
            f3_countdown -= 1
            if f3_countdown <= 0 and _hud_present and not _menu_open and not _f3_open:
                # Always reset to full interval first — prevents fast retry loop
                # even if OCR fails to read anything
                f3_countdown = max(config.F3_READ_EVERY_N_TICKS, 60)
                if frame is not None:
                    _f3_open = True
                    f3_data  = _open_read_close_f3()
                    _f3_open = False
                    if f3_data['f3_active']:
                        world_mem.update_f3(f3_data)
                        _px = getattr(world_mem, 'pos_x', None)
                        _pz = getattr(world_mem, 'pos_z', None)
                        if _px is not None and _pz is not None:
                            world.update_position((_px, world_mem.y_level, _pz))
                    else:
                        print('[F3] OCR found no XYZ — closed, will retry in 60 ticks')
            elif f3_countdown <= 0:
                # Not safe (menu open / no HUD) — wait at least 60 ticks before next try
                f3_countdown = 60

            # ── Console log ────────────────────────────────────
            elapsed = round(time.time() - t0, 2)
            obs  = action_dict.get('observation', '')[:45]
            conf = action_dict.get('confidence', 0)
            ear_state = '🔊' if ear.enabled else '🔇'
            fsm_tag = fsm_state.value[:6] if fsm_state else '?'
            print(f'[{tick:04d}] {elapsed}s {ear_state} | '
                  f'yolo:{len(objects):2d} | {src_tag:<18} | fsm:{fsm_tag:<7} | '
                  f'conf:{conf:.2f} | r:{r:+.3f} | avg:{reward.average():+.3f} | '
                  f'{obs}')

            # ── Health log ──────────────────────────────────────
            # Cheap unattended-monitoring file so Scott can check status
            # without tailing raw logs — see _write_health_log() below.
            if tick % 60 == 0:
                _write_health_log(tick, goals.current_goal(), r, cognitive)

            # ── Pace ──────────────────────────────────────────
            time.sleep(max(0, config.LOOP_INTERVAL_SEC - (time.time() - t0)))

    except KeyboardInterrupt:
        pass

    finally:
        print(f'\n[AKSUMAEL] stopped  ticks:{tick}  '
              f'avg_reward:{reward.average():.3f}  '
              f'skills:{len(skills.skills)}  '
              f'session:{world.session_num}')
        if _f3_open:
            # Shutting down mid F3-toggle (open sent, close not yet sent) —
            # force the close now so the overlay doesn't stay stuck open
            # in-game across the restart. See SIGTERM handler above.
            print('[F3] was left open at shutdown — force-closing')
            executor.execute({'key': 'f3'})
        replayer.stop()
        if rl_trainer is not None:
            rl_trainer.stop()
            neural_policy.save_checkpoint()
        tts.say_line('shutdown', priority=True)
        skills.save_all()
        rl.save()
        world.save()
        world_mem.save()
        inventory.save()
        goals.save()
        progression.save()
        if mastermind_client is not None:
            mastermind_client.shutdown()
        time.sleep(1.5)
        executor.close()
        router.stop()
        if ear.enabled:
            ear.stop()
        tts.stop()
        pipeline.stop()   # signals CaptureThread, YOLOThread, DisplayThread
        ui.close()


def _idle() -> dict:
    return {'observation': '', 'action': 'wait',
            'key': None, 'click': None,
            'gamepad': None, 'confidence': 0.0}


HEALTH_LOG_PATH = '/tmp/aksumael_health.txt'


def _write_health_log(tick: int, goal: str, last_reward: float, cognitive) -> None:
    """Plain-text status snapshot for unattended checks (no log tailing needed)."""
    video_present = os.path.exists(f'/dev/video{config.CAMERA_INDEX}')
    tty_present   = os.path.exists('/dev/ttyUSB0')
    vision_calls  = get_call_counts()
    claude_calls  = vision_calls['claude'] + cognitive.monologue.claude_call_count
    lines = [
        f'updated:      {time.strftime("%Y-%m-%d %H:%M:%S")}',
        f'tick:         {tick}',
        f'goal:         {goal or "none"}',
        f'last_reward:  {last_reward:+.3f}',
        f'video2:       {"present" if video_present else "ABSENT"}',
        f'ttyUSB0:      {"present" if tty_present else "ABSENT"}',
        f'vision_route: {get_last_provider() or "none"} (last tick)',
        f'local_calls:  {vision_calls["local"]}',
        f'gemini_calls: {vision_calls["gemini"]}',
        f'claude_calls: {claude_calls}',
    ]
    try:
        with open(HEALTH_LOG_PATH, 'w') as f:
            f.write('\n'.join(lines) + '\n')
    except Exception as e:
        print(f'[HEALTH] write failed: {e}')


# Learned skill names are built from their trigger objects (see
# skills/skill_system.py _mine_recent), e.g. "diamond_ore_3da5b4",
# "oak_log_9f2c11", "cobblestone_88ab41" — never a "mine_" or "coal_ore_"
# prefix. Match on the trigger-label substrings mining/chopping actually use.
_MINE_SKILL_MARKERS = (
    'ore', 'log', 'wood', 'tree', 'stone', 'cobblestone', 'gravel', 'debris',
)


def _is_mining_skill(name: str) -> bool:
    """True if a learned skill name looks like it mines/chops a block."""
    return bool(name) and any(marker in name for marker in _MINE_SKILL_MARKERS)


# ── Goal-skill mismatch detection (skill-replay escape) ─────────────────
# Coarse keyword categories so a skill whose trigger objects are clearly
# unrelated to the active goal (e.g. a gold_ore mining skill firing while
# the goal is find_and_chop_tree) never fires — matching skills purely on
# YOLO objects with no goal awareness is what let AKSUMAEL replay ore
# skills indefinitely underground while its goal needed a tree found on
# the surface. Intentionally keyword-based rather than importing
# core.fsm's label sets — this only needs to catch that class of mismatch,
# not do full label classification.
_GOAL_CATEGORY_KEYWORDS = {
    'tree': ('tree', 'chop', 'wood', 'log', 'axe', 'lumber'),
    'ore':  ('ore', 'mine', 'diamond', 'coal', 'iron', 'gold',
             'redstone', 'lapis', 'copper', 'emerald', 'stone'),
}

_SKILL_CATEGORY_KEYWORDS = {
    'tree': ('tree', 'log', 'wood', 'oak', 'spruce', 'birch', 'jungle',
             'acacia', 'sapling', 'chop', 'axe', 'lumber'),
    'ore':  ('ore', 'diamond', 'coal', 'iron', 'gold', 'redstone',
             'lapis', 'copper', 'emerald', 'debris', 'cobblestone',
             'gravel'),
}


def _tokens(text: str) -> list:
    """Split on non-alphanumeric boundaries, e.g. 'find_and_chop_tree' ->
    ['find', 'and', 'chop', 'tree']. Used instead of raw substring matching
    so e.g. the goal "explore" doesn't spuriously match the "ore" keyword
    (it contains "ore" as a literal substring: expl-ORE)."""
    return [t for t in re.split(r'[^a-z0-9]+', (text or '').lower()) if t]


def _keyword_hits(tokens: list, keywords: tuple) -> bool:
    """True if any token equals a keyword, or is a plural/suffixed form of
    one (token startswith keyword, e.g. token 'diamonds' vs keyword
    'diamond'). Never matches on a keyword being a mere substring of an
    unrelated token (that's the "explore" vs "ore" trap)."""
    return any(tok == kw or tok.startswith(kw) for tok in tokens for kw in keywords)


def _goal_category(goal: str) -> str | None:
    """Coarse category ('tree' | 'ore' | None) for a goal string, based on
    whole-token keyword match. None means "no strong opinion" — never
    treated as a mismatch against any skill."""
    tokens = _tokens(goal)
    for cat, keywords in _GOAL_CATEGORY_KEYWORDS.items():
        if _keyword_hits(tokens, keywords):
            return cat
    return None


def _skill_category(skill) -> str | None:
    """Coarse category ('tree' | 'ore' | None) for a skill, based on its
    trigger objects and name."""
    tokens = []
    for label in list(skill.trigger_objects) + [skill.name]:
        tokens.extend(_tokens(label))
    for cat, keywords in _SKILL_CATEGORY_KEYWORDS.items():
        if _keyword_hits(tokens, keywords):
            return cat
    return None


def _skill_allowed_for_goal(goal: str, skill) -> bool:
    """Hard allow/deny gate — the single source of truth for whether `skill`
    may be selected or replayed while `goal` is active. This is an allow-list,
    not a mismatch check: once the goal has a known category (tree/ore), a
    skill must positively match that category (or be a cross-goal `universal`
    skill) to run. A skill with NO recognized category (e.g. it hits neither
    the tree nor ore keyword sets) is blocked too — the previous "only block
    on a clear category conflict" logic let uncategorized skills (and, once a
    goal's category flipped mid-episode, stale-category skills) slip through
    the candidates filter, warn, and still fire on replay (e.g. a gold_ore
    skill replaying while goal=find_and_chop_tree). Goals with no recognized
    category (None/idle/explore/craft_*/etc.) impose no restriction — every
    skill passes through unchanged, so the fallback for goal=None/idle never
    breaks."""
    if skill is None:
        return True
    if getattr(skill, 'universal', False):
        return True
    gc = _goal_category(goal)
    if gc is None:
        return True
    return _skill_category(skill) == gc


def _hud_reward(objects: list, prev_objects: list) -> float:
    """Estimate reward delta from tick-over-tick HUD/object bbox changes."""
    def _find(objs, label):
        return next((o for o in objs if o.get('label') == label), None)

    def _width(o):
        box = o.get('box') if o else None
        return (box[2] - box[0]) if box and len(box) == 4 else 0.0

    reward = 0.0
    EPS = 2.0   # px — ignore jitter below this

    curr_xp, prev_xp = _find(objects, 'xp_bar'), _find(prev_objects, 'xp_bar')
    if curr_xp and prev_xp and _width(curr_xp) > _width(prev_xp) + EPS:
        reward += 0.3   # XP bar grew → XP gained

    curr_hp, prev_hp = _find(objects, 'health_bar'), _find(prev_objects, 'health_bar')
    if curr_hp and prev_hp:
        curr_w, prev_w = _width(curr_hp), _width(prev_hp)
        if curr_w < prev_w - EPS:
            reward -= 0.4   # health bar shrank → took damage
        elif curr_w > prev_w + EPS:
            reward += 0.1   # health bar grew → healed

    curr_labels = {o.get('label') for o in objects}
    prev_labels = {o.get('label') for o in prev_objects}
    valuable = {'diamond_ore', 'emerald_ore', 'chest_row', 'furnace'}
    new_valuable = (curr_labels & valuable) - prev_labels
    if new_valuable:
        reward += 0.5 * len(new_valuable)   # new valuable object spotted → exploration reward

    return reward


def _handle_joystick(h, ui, router, reward, tts):
    if h.buttons & 0x0001:
        reward.add_manual(+1.0)
        tts.say_line('good_reward')
    if h.buttons & 0x0002:
        reward.add_manual(-1.0)
        tts.say_line('bad_reward')
    if h.buttons & 0x0004:
        ui.paused = not ui.paused
        tts.say_line('pause' if ui.paused else 'resume', priority=True)
        time.sleep(0.3)
    if h.buttons & 0x0008:
        mode = router.cycle_blend_mode()
        tts.say_line(f'mode_{mode}', priority=True)
        time.sleep(0.3)
