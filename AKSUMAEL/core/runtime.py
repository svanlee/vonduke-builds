# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Main Runtime Loop                  ║
# ╚══════════════════════════════════════════════════════╝

import time
import cv2
import config

from core.capture            import VideoCapturePipeline
from vision.color_detector   import detect_ores_by_color, merge_with_yolo
from vision.yolo             import YOLODetector
from vision.f3_reader        import read_f3
from core.vision_brain       import ask_vision
from core.world_model        import WorldModel
from core.cognitive          import CognitiveArchitecture
from memory.reward           import RewardSystem
from memory.world_memory     import WorldMemory
from memory.inventory        import InventoryTracker
from memory.goals            import GoalStack
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
    print(BANNER)
    print(f'  vision   : {config.VISION_PROVIDER} / capture card')
    print(f'  actions  : {config.ACTION_OUTPUT} → {config.PLATFORM_TARGET}')
    print(f'  blend    : {config.BLEND_MODE}')
    print(f'  tts      : {"on" if config.ENABLE_TTS else "off"}  '
          f'game_ear : {"on" if config.ENABLE_GAME_EAR else "off"}  '
          f'survey   : on (conf<{config.SURVEY_CONF_THRESH})')
    print()

    # ── Initialise all subsystems ──────────────────────────────
    yolo      = YOLODetector()
    world     = WorldModel()
    world_mem = WorldMemory()
    inventory = InventoryTracker()
    goals       = GoalStack()
    progression = ProgressionTracker()
    mc_kb       = MinecraftKB()
    cognitive   = CognitiveArchitecture()
    reward    = RewardSystem()
    executor  = ActionExecutor()
    router    = ControllerRouter()
    tts       = TTSEngine()
    ear       = GameEar()          # graceful if no audio device
    skills    = SkillSystem()
    rl        = RLPolicy()
    replayer  = SkillReplayer(executor)
    aim_ctrl  = AimController()          # uses YOLO-frame coords (640×360)
    ui        = LabelingUI(yolo, router, reward, skills=skills)

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
    from behaviors.crafting import CraftingBehavior
    from behaviors.scan import EnvironmentScanner
    from behaviors.launch_game import GameLauncher
    from core.fsm import GameFSM, State
    auto_trainer = AutoTrainer(yolo)
    surveyor = SurveyBehavior(collector, executor, auto_trainer=auto_trainer) if collector else None
    respawner = RespawnBehavior(executor)
    hunger_behavior = HungerBehavior(executor)
    crafting_behavior = CraftingBehavior(executor)
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
    SKILL_COOLDOWN_TICKS      = 20     # how long a spammed skill stays suppressed
    last_action      = {}    # most recent Claude (LLM) response dict
    prev_objects     = []    # last tick's YOLO detections (for HUD-delta reward)
    f3_countdown         = 50    # ticks until next F3 OCR read (offset from startup)
    _f3_open             = False  # True while F3 overlay is open
    _menu_stuck_since    = 0      # tick when menu was first detected open
    MENU_STUCK_TICKS     = 20     # close menu after this many ticks with no action
    fsm_state        = None  # updated each tick for console logging
    _llm_call_count  = 0     # total LLM calls this session
    _last_llm_frame  = None  # frame used in last LLM call (for frame-diff skip)
    _last_scan_tick  = -config.SCAN_COOLDOWN_TICKS   # fire scan on first EXPLORE tick
    _prev_replay_active = False  # replayer.is_active() as of last tick
    # Mining skills replay recorded look deltas that tend to walk the camera
    # pitch upward over a full replay. Once the skill ends, nudge the pitch
    # back down toward the horizon so EXPLORE/APPROACH aren't scanning sky.
    _MINE_PITCH_RESET_DY = config.LOOK_SENSITIVITY * 8   # ~120px — down, not to the ground

    # Init extended F3 fields on world_mem so context_summary() can use them
    world_mem.pos_x   = getattr(world_mem, 'pos_x',   None)
    world_mem.pos_z   = getattr(world_mem, 'pos_z',   None)
    world_mem.facing  = getattr(world_mem, 'facing',  'unknown')
    world_mem.fps     = getattr(world_mem, 'fps',     None)
    world_mem.chunk_x = getattr(world_mem, 'chunk_x', None)
    world_mem.chunk_z = getattr(world_mem, 'chunk_z', None)
    print('[AKSUMAEL] running — Ctrl+C or q in window to stop\n')

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

            # ── HUD / menu state ────────────────────────────────
            # Used to gate anything that would corrupt an open menu screen
            # (F3 debug overlay toggle, scan/pathfinder sweeps, skill execution).
            _hud_labels  = {o.get('label') for o in objects}
            _menu_open   = bool(_hud_labels & {'inventory', 'crafting_table', 'chest_row', 'furnace'})
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
            if launcher.should_trigger(objects, tick):
                print(f'[LAUNCH] HUD not detected at tick {tick} — triggering launch sequence')
                launcher.run(tick)
                continue

            world_mem.update(objects, action=last_action)
            goals.auto_update(world_mem, inventory)
            progression.auto_update(inventory, world_mem, tick)

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

            fsm_state, fsm_action = fsm.tick(objects, world_mem, _hunger_frac)

            # ── Scan / identify / pathfinder ──────────────────
            # Continuous watch: runs every SCAN_COOLDOWN_TICKS whenever not
            # mid-skill-replay. Sweep is fast (~4s); zoom+identify only fires
            # when YOLO actually spotted a danger label.
            _replay_active_now = replayer.is_active()
            if (_prev_replay_active and not _replay_active_now
                    and last_skill_name and (last_skill_name.startswith('mine_')
                                             or last_skill_name.startswith('coal_ore'))):
                print(f'[CAMERA] {last_skill_name} replay ended — '
                      f'resetting pitch toward horizon (dy={_MINE_PITCH_RESET_DY})')
                executor.execute({'look': {'dx': 0, 'dy': _MINE_PITCH_RESET_DY},
                                   'source': 'pitch_reset'})
            _prev_replay_active = _replay_active_now

            if (not replayer.is_active()
                    and not _menu_open
                    and not _f3_open
                    and (tick - _last_scan_tick) >= config.SCAN_COOLDOWN_TICKS):
                scanner.run(world_mem, target_bearing=0)
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

            else:
                # Try a learned skill first — if multiple candidates match,
                # let the RL policy pick among them instead of the naive best.
                candidates = skills.find_candidates(objects)
                if len(candidates) > 1:
                    names = [sk.name for sk, _ in candidates]
                    chosen_name = rl.choose_skill(names, objects)
                    by_name = {sk.name: (sk, m) for sk, m in candidates}
                    skill, match = by_name.get(chosen_name, (None, 0.0))
                else:
                    skill, match = skills.find_best(objects)

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

                if skill and match >= skills.MIN_MATCH_SCORE:
                    same_skill_count = same_skill_count + 1 if skill.name == last_skill_name else 1
                    last_skill_name  = skill.name
                    # Find the box of the trigger object so the aim phase
                    # can centre the crosshair on it before action steps run.
                    aim_box = None
                    for obj in objects:
                        olabel = obj.get('label', '').lower()
                        if any(olabel == t.lower() or
                               olabel in t.lower() or t.lower() in olabel
                               for t in skill.trigger_objects):
                            aim_box = obj.get('box')
                            break
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

                # FSM takes over when no skill matched and FSM is actively
                # targeting something (APPROACH / MINE / COMBAT / COLLECT /
                # INTERACT).  In pure EXPLORE or EAT we fall through to the
                # LLM so it can direct higher-level decisions.
                elif fsm_state not in (State.EXPLORE, State.EAT):
                    action_dict = fsm_action
                    src_tag = f'FSM:{fsm_state.value}'

                # Fall back to LLM — only in EXPLORE/EAT, only every N ticks
                elif tick % config.LLM_EVERY_N_TICKS == 0:
                    # Frame-diff gate: skip call if scene hasn't changed enough
                    _scene_changed = True
                    if _last_llm_frame is not None and frame is not None:
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
                        # Strategic tick: inject full phase mechanics + discoveries
                        # every 100 ticks or when Claude is uncertain
                        _is_strategic = (
                            tick % (config.LLM_EVERY_N_TICKS * 6) == 0
                            or last_action.get('confidence', 1.0) < 0.35
                        )
                        if _is_strategic:
                            history = (mc_kb.strategic_context(progression.phase)
                                       + '\n\n' + history)
                        if tick % (config.LLM_EVERY_N_TICKS * 10) == 0:
                            history = world.cross_session_summary() + '\n' + history
                        action_dict = ask_vision(frame, history, objects)
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

                # Between LLM ticks in EXPLORE: carry last movement forward
                # so AKSUMAEL keeps walking/acting instead of going idle for 14 ticks
                else:
                    if last_action.get('key') in ('w', 'a', 's', 'd'):
                        action_dict = {**last_action}
                        src_tag = 'CARRY'

            # ── Death/respawn detection ─────────────────────────
            if respawner.update(objects, last_observation=last_action.get('observation', '')):
                world_mem.record_death()
                continue

            # ── Hunger ───────────────────────────────────────────
            hunger_behavior.update(objects, world_mem=world_mem)

            # ── Crafting (pickaxe nearing durability limit) ─────
            if (world_mem.pickaxe_uses > config.PICKAXE_DURABILITY * 0.8
                    and not replayer.is_active()
                    and crafting_behavior.should_trigger(objects)):
                crafting_behavior.run()

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

            # ── World model ────────────────────────────────────
            world.update({
                'objects': objects,
                'action':  final.get('key') or action_dict.get('action', 'wait'),
            })

            # ── Reward ────────────────────────────────────────
            reward.add_hud_reward(_hud_reward(objects, prev_objects))
            r = reward.compute({'objects': objects}, action_dict)
            rl.update(r, objects)
            prev_objects = objects

            # ── RL policy bookkeeping ───────────────────────────
            if tick % 100 == 0:
                rl.save()
                print(f'[LLM] {_llm_call_count} calls so far this session')
            if tick % 200 == 0:
                print(f'[RL] {rl.stats()}')

            # ── Cognitive architecture ──────────────────────────
            cognitive.update(tick, objects, action_dict, r)

            # ── Skill mining ───────────────────────────────────
            if used_skill is None and not replayer.is_active():
                mined = skills.observe(objects, final, r)
                if mined:
                    tts.say_line('skill_learned')

            # ── Skill pruning ───────────────────────────────────
            if tick % 50 == 0:
                skills.prune_bad()

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
                    executor.execute({'key': 'f3'})                    # open
                    time.sleep(config.F3_KEY_WAIT_TICKS * 0.2)
                    f3_frame = pipeline.latest_raw_frame               # full-res frame
                    f3_data  = read_f3(f3_frame)
                    executor.execute({'key': 'f3'})                    # close
                    _f3_open = False
                    if f3_data['f3_active']:
                        world_mem.update_f3(f3_data)
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

            # ── Pace ──────────────────────────────────────────
            time.sleep(max(0, config.LOOP_INTERVAL_SEC - (time.time() - t0)))

    except KeyboardInterrupt:
        pass

    finally:
        print(f'\n[AKSUMAEL] stopped  ticks:{tick}  '
              f'avg_reward:{reward.average():.3f}  '
              f'skills:{len(skills.skills)}  '
              f'session:{world.session_num}')
        replayer.stop()
        tts.say_line('shutdown', priority=True)
        skills.save_all()
        rl.save()
        world.save()
        world_mem.save()
        inventory.save()
        goals.save()
        progression.save()
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
