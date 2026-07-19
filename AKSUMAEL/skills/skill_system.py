# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Skill System                       ║
# ║  Mine → match → replay full sequences with timing   ║
# ╚══════════════════════════════════════════════════════╝

import json
import os
import tempfile
import time
import hashlib
import threading
import config
from core.fsm import MINE_TARGETS, TREE_TARGETS

# Minecraft-aware synonym groups for fuzzy trigger matching
LABEL_SYNONYMS = {
    'tree':    {'tree','oak','birch','spruce','jungle','acacia','wood','log','sapling'},
    'water':   {'water','lake','river','ocean','stream'},
    'stone':   {'stone','rock','cobblestone','gravel','granite','diorite','andesite'},
    'mob':     {'zombie','skeleton','creeper','spider','enderman','witch','mob','enemy'},
    'item':    {'item','drop','pickup','resource','material'},
    'chest':   {'chest','barrel','shulker','container','box'},
    'door':    {'door','gate','trapdoor'},
    'food':    {'food','apple','bread','carrot','meat','fish','berry'},
    'grass':   {'grass','dirt','farmland','podzol'},
    'ore':     {'ore','coal','iron','gold','diamond','emerald','lapis','redstone'},
    'player':  {'player','person','human','villager'},
    'animal':  {'cow','pig','sheep','chicken','horse','animal','passive'},
    'fire':    {'fire','lava','magma','flame'},
    'crafting':{'crafting','table','workbench','anvil','furnace','smoker'},
    'building':{'wall','roof','floor','building','structure','house'},
}

# HUD elements that are permanently on screen — never valid as a skill's
# *only* trigger, since a skill triggered solely by these fires every tick.
HUD_ALWAYS_VISIBLE = {'health_bar', 'armor_bar', 'hunger_bar', 'xp_bar', 'hotbar',
                      'crosshair'}

# Labels that are YOLO false positives in the current environment — don't
# create skills triggered by these (emerald_ore only spawns in mountain biomes).
# 'tree' is blocked because hand-crafted `chop_tree` already handles all
# log/birch/oak/leaves + tree combos — dynamically mined tree skills only
# capture approach/aim steps that press pickaxe hotkey on leaves instead of
# actually breaking logs, and they loop forever competing with chop_tree.
# 'leaves' is blocked for the same reason (it's always visible near any tree).
_BLOCKED_SKILL_TRIGGERS = frozenset({'emerald_ore', 'tree', 'leaves'})

def _canonical(label: str) -> str:
    """Map any label to its canonical group name, or itself."""
    l = label.lower().strip()
    for canon, synonyms in LABEL_SYNONYMS.items():
        if l in synonyms or l == canon:
            return canon
    return l

def _fuzzy_overlap(trigger_set: set, current_set: set) -> float:
    """
    Compute overlap score using canonical label groups.
    Partial synonym matches count as 0.5.
    """
    if not trigger_set:
        return 0.0
    trigger_canon  = {_canonical(t) for t in trigger_set}
    current_canon  = {_canonical(c) for c in current_set}
    exact    = len(trigger_canon & current_canon)
    # Partial: trigger word appears as substring in any current word
    partial  = sum(
        0.5 for t in trigger_canon
        if t not in current_canon
        and any(t in c or c in t for c in current_canon)
    )
    return min(1.0, (exact + partial) / len(trigger_canon))


class SkillStep:
    """One action within a skill sequence, with timing."""
    def __init__(self, action: dict, delay_after_ms: int = 150):
        self.action        = action          # {key, click, gamepad}
        self.delay_after_ms = delay_after_ms # ms to wait after this step

    def to_dict(self):
        return {'action': self.action, 'delay_after_ms': self.delay_after_ms}

    @classmethod
    def from_dict(cls, d):
        return cls(action=d.get('action', {}),
                   delay_after_ms=d.get('delay_after_ms', 150))


class Skill:
    """A named, timed action sequence with a fuzzy context trigger."""

    BLACKLIST_FAILURES = 3   # failed_count threshold to auto-blacklist

    def __init__(self, name, trigger_objects=None, steps=None,
                 avg_reward=0.0, uses=0, created=None,
                 preconditions=None, postconditions=None,
                 success_count=0, failed_count=0, blacklisted=False,
                 universal=False):
        self.name            = name
        self.trigger_objects = trigger_objects or []
        self.steps           = steps or []          # list of SkillStep
        self.avg_reward      = avg_reward
        self.uses            = uses
        self.created         = created or time.time()
        self.last_used       = None
        # ── Voyager-style verification (v1.1) ──────────────────
        # preconditions:  {"has_item": [...], "yolo_visible": [...]}
        # postconditions: {"inventory_gained": [...], "min_count": 1}
        self.preconditions   = preconditions or {}
        self.postconditions  = postconditions or {}
        self.success_count   = success_count
        self.failed_count    = failed_count
        self.blacklisted     = blacklisted
        # Cross-environment skill transfer (see core/skill_transfer.py):
        # universal skills (navigate_menu, click_button, read_text,
        # type_text, scroll) stay loaded regardless of which env_profile
        # is active; everything else is env-specific and gets filtered by
        # trigger_objects overlap with the active profile's yolo_classes.
        self.universal       = universal

    @property
    def actions(self):
        """Backward-compat: return raw action dicts."""
        return [s.action for s in self.steps]

    def to_dict(self):
        return {
            'name':            self.name,
            'trigger_objects': self.trigger_objects,
            'steps':           [s.to_dict() for s in self.steps],
            'avg_reward':      round(self.avg_reward, 3),
            'uses':            self.uses,
            'created':         self.created,
            'last_used':       self.last_used,
            'preconditions':   self.preconditions,
            'postconditions':  self.postconditions,
            'success_count':   self.success_count,
            'failed_count':    self.failed_count,
            'blacklisted':     self.blacklisted,
            'universal':       self.universal,
        }

    @classmethod
    def from_dict(cls, d):
        raw_steps = d.get('steps', [])
        # Handle old format (list of plain action dicts)
        if raw_steps and isinstance(raw_steps[0], dict) and 'action' not in raw_steps[0]:
            steps = [SkillStep(action=a) for a in raw_steps]
        else:
            steps = [SkillStep.from_dict(s) for s in raw_steps]
        s = cls(
            name=d['name'],
            trigger_objects=d.get('trigger_objects', []),
            steps=steps,
            avg_reward=d.get('avg_reward', 0.0),
            uses=d.get('uses', 0),
            created=d.get('created'),
            # Backfill: older skill files predate these fields — default to
            # empty/zero so they keep working exactly as before.
            preconditions=d.get('preconditions', {}),
            postconditions=d.get('postconditions', {}),
            success_count=d.get('success_count', 0),
            failed_count=d.get('failed_count', 0),
            blacklisted=d.get('blacklisted', False),
            universal=d.get('universal', False),
        )
        s.last_used = d.get('last_used')
        return s

    # ── Voyager-style verification ─────────────────────────────
    def check_preconditions(self, inventory: dict, objects: list) -> bool:
        """True if `inventory` ({item: count}) and current `objects` (YOLO
        detections) satisfy this skill's preconditions. Skills with no
        preconditions always pass (keeps old skills working unchanged)."""
        if not self.preconditions:
            return True
        has_item = self.preconditions.get('has_item') or []
        if has_item and not all(inventory.get(item, 0) > 0 for item in has_item):
            return False
        yolo_visible = self.preconditions.get('yolo_visible') or []
        if yolo_visible:
            current_labels = {o.get('label', '') for o in objects}
            if not (set(yolo_visible) & current_labels):
                return False
        return True

    def verify_postconditions(self, inv_before: dict, inv_after: dict) -> bool:
        """True if the inventory diff after replay satisfies this skill's
        postconditions. Skills with no postconditions are assumed to have
        succeeded (keeps old skills working unchanged)."""
        if not self.postconditions:
            return True
        gained = self.postconditions.get('inventory_gained') or []
        if not gained:
            return True
        min_count = self.postconditions.get('min_count', 1)
        for item in gained:
            delta = inv_after.get(item, 0) - inv_before.get(item, 0)
            if delta >= min_count:
                return True
        return False

    def record_outcome(self, success: bool):
        """Update success/failed counters and auto-blacklist on repeated failure."""
        if success:
            self.success_count += 1
        else:
            self.failed_count += 1
            if self.failed_count >= self.BLACKLIST_FAILURES:
                self.blacklisted = True

    def has_real_action(self) -> bool:
        """False if every step is a no-op (null key/click/look, zeroed gamepad)."""
        for s in self.steps:
            a = s.action
            if a.get('key') or a.get('click') or a.get('look'):
                return True
            gp = a.get('gamepad') or {}
            if any(gp.get(k) for k in ('lx', 'ly', 'rx', 'ry', 'lt', 'rt', 'buttons')):
                return True
        return False

    def matches(self, current_objects: list) -> float:
        if not self.trigger_objects:
            return 0.0
        # Strip HUD-only elements from trigger before scoring.
        # HUD labels (armor_bar, health_bar, hotbar, xp_bar, hunger_bar) are
        # always visible — including them inflates scores for junk skills.
        meaningful_trigger = set(self.trigger_objects) - HUD_ALWAYS_VISIBLE
        if not meaningful_trigger:
            return 0.0   # skill triggered only by HUD — never fire it
        current_labels = {o.get('label', '') for o in current_objects}
        return _fuzzy_overlap(meaningful_trigger, current_labels)

    def __repr__(self):
        return (f'<Skill {self.name} trig={self.trigger_objects} '
                f'steps={len(self.steps)} r={self.avg_reward:.2f} uses={self.uses}>')


# ── Skill Replayer ────────────────────────────────────────────
class SkillReplayer:
    """
    Replays a skill's full step sequence in a background thread,
    sending each action through the executor with correct timing.
    The main loop keeps running during replay.
    """

    def __init__(self, executor):
        self.executor   = executor
        self._thread    = None
        self._active    = False
        self._current   = None   # Skill being replayed

    def start(self, skill: Skill, aim_box: list = None, aim_ctrl=None):
        if self._active:
            return   # don't interrupt ongoing replay
        self._current = skill
        self._active  = True
        self._thread  = threading.Thread(
            target=self._replay,
            args=(skill, aim_box, aim_ctrl),
            daemon=True,
            name=f'replay_{skill.name[:12]}'
        )
        self._thread.start()

    def _replay(self, skill: Skill, aim_box: list = None, aim_ctrl=None):
        # Aim phase: centre crosshair on the trigger object before acting
        if aim_box is not None and aim_ctrl is not None and self._active:
            print(f'[SKILL] aiming before {skill.name}')
            aim_ctrl.aim_until(aim_box, self.executor, max_ticks=12)

        print(f'[SKILL] replaying {skill.name} ({len(skill.steps)} steps)')
        for i, step in enumerate(skill.steps):
            if not self._active:
                break
            self.executor.execute(step.action)
            delay = step.delay_after_ms / 1000.0
            time.sleep(max(0.05, delay))
        self._active  = False
        self._current = None

    def is_active(self) -> bool:
        return self._active

    def stop(self):
        self._active = False


# ── Skill System ──────────────────────────────────────────────
class SkillSystem:
    MIN_REWARD_TO_MINE = 0.5
    SEQUENCE_LENGTH    = 4     # steps per mined skill (up from 3)
    MIN_MATCH_SCORE    = 0.5
    DEFAULT_STEP_DELAY = 150   # ms between steps

    def __init__(self):
        self.skills   = {}
        self.dir      = config.SKILLS_DIR
        self._buffer  = []
        self._buffer_max = 60
        # Session-only, never persisted: when set (via set_active_filter,
        # see core/skill_transfer.py), find_best()/find_candidates() only
        # consider skills whose name is in this set — lets an env_profile
        # narrow the library to universal + env-relevant skills without
        # touching any skill's on-disk blacklisted/universal flags. None
        # (the default) means "no filtering", i.e. exactly the old behavior.
        self._active_names = None
        self._load_all()

    def set_active_filter(self, names: set | None) -> None:
        self._active_names = names

    # ── Persistence ────────────────────────────────────────────
    def _load_all(self):
        os.makedirs(self.dir, exist_ok=True)
        count = 0
        purged = 0
        for fn in os.listdir(self.dir):
            if not fn.endswith('.json'):
                continue
            path = os.path.join(self.dir, fn)
            try:
                with open(path) as f:
                    data = json.load(f)
                triggers = set(data.get('trigger_objects', []))
                # Sanitise on load: delete any skill whose trigger is polluted
                # with HUD labels or blocked false-positive labels.
                # This catches stale junk files that survived earlier purges.
                contaminated = (triggers & HUD_ALWAYS_VISIBLE) or (triggers & _BLOCKED_SKILL_TRIGGERS)
                # Also purge pre-existing no-click mine/tree skills mined
                # before the 2026-07-15 fix to _mine_recent() — these were
                # captured mid-"aiming" (before the FSM's MINE state ever
                # reaches on-target and clicks) and can never break anything,
                # but were winning find_candidates() and blocking the FSM's
                # own MINE loop from getting a chance to actually click.
                clickless_mine_skill = (
                    bool(triggers & (MINE_TARGETS | TREE_TARGETS))
                    and not any(step.get('action', {}).get('click')
                                for step in data.get('steps', []))
                )
                if contaminated or clickless_mine_skill:
                    reason = 'contaminated trigger' if contaminated else 'no click in any step'
                    print(f'[SKILL] purging stale junk skill: {fn} (trigger={list(triggers)}, {reason})')
                    try:
                        os.remove(path)
                    except OSError as e:
                        print(f'[SKILL] could not delete {fn}: {e}')
                    purged += 1
                    continue
                skill = Skill.from_dict(data)
                self.skills[skill.name] = skill
                count += 1
            except Exception as e:
                print(f'[SKILL] load error {fn}: {e}')
        msg = f'[SKILL] loaded {count} skills from disk'
        if purged:
            msg += f', purged {purged} junk skills'
        print(msg)

    def save(self, skill: Skill):
        os.makedirs(self.dir, exist_ok=True)
        safe = ''.join(c if c.isalnum() or c in '-_' else '_'
                       for c in skill.name)
        path = os.path.join(self.dir, f'{safe}.json')
        try:
            # Write to a temp file in the same directory, then rename —
            # os.replace is atomic on Linux, so a SIGTERM mid-write can
            # never leave a truncated/corrupted skill file on disk.
            dir_path = os.path.dirname(path)
            with tempfile.NamedTemporaryFile('w', dir=dir_path, delete=False, suffix='.tmp') as tmp:
                json.dump(skill.to_dict(), tmp, indent=2)
                tmp_path = tmp.name
            os.replace(tmp_path, path)
        except Exception as e:
            print(f'[SKILL] save error: {e}')

    def save_all(self):
        for sk in self.skills.values():
            self.save(sk)

    # ── Mining ─────────────────────────────────────────────────
    def observe(self, objects: list, action: dict, reward: float):
        """Feed one tick into the mining buffer."""
        self._buffer.append({
            'objects': objects,
            'action':  action,
            'reward':  reward,
            'ts':      time.time(),
        })
        if len(self._buffer) > self._buffer_max:
            self._buffer.pop(0)
        if reward >= self.MIN_REWARD_TO_MINE:
            return self._mine_recent()
        return None

    def _mine_recent(self):
        if len(self._buffer) < self.SEQUENCE_LENGTH:
            return None

        window = self._buffer[-self.SEQUENCE_LENGTH:]

        # Trigger from objects at start of sequence — strip HUD elements so
        # they don't pollute skill names or trigger matching.
        trigger = list({
            _canonical(o.get('label', ''))
            for o in window[0]['objects']
            if o.get('label') and _canonical(o.get('label', '')) not in HUD_ALWAYS_VISIBLE
        })
        if not trigger:
            return None

        # Don't create skills for YOLO false-positive labels (e.g. emerald_ore
        # outside mountain biomes) — they produce junk skills that loop forever.
        if set(trigger) & _BLOCKED_SKILL_TRIGGERS:
            return None

        # Don't mine a "chop/mine" skill with no click in it — see 2026-07-15.
        # The FSM's MINE state assigns a high reward (~0.5-0.6) just for
        # confidently *aiming* at a valid target, before it ever reaches
        # on-target and starts clicking (see core/fsm.py's _do_mine — the
        # 'aiming' phase has no ad['click'] at all). Since observe() mines a
        # skill from the last SEQUENCE_LENGTH ticks the instant reward
        # crosses MIN_REWARD_TO_MINE, a pure aim-in-progress window got
        # captured and saved as a "successful" skill (confirmed: an existing
        # leaves_tree_*.json had click:null on all 4 steps, yet
        # success_count=17/failed_count=0) — one that only presses '2' and
        # never clicks. It then kept winning find_candidates() and replaying
        # instead of the FSM's own (already-fixed) MINE loop ever getting
        # enough uninterrupted ticks to actually converge and click.
        # A skill whose trigger requires an interaction to have any effect
        # (mining/chopping) but contains zero clicks across every step can
        # never accomplish anything — refuse to mine/reinforce it.
        if (set(trigger) & (MINE_TARGETS | TREE_TARGETS)
                and not any(step['action'].get('click') for step in window)):
            return None

        # Build timed steps — infer delay from actual tick timestamps
        steps = []
        for i, step in enumerate(window):
            if i + 1 < len(window):
                gap_ms = int((window[i+1]['ts'] - step['ts']) * 1000)
                gap_ms = max(50, min(500, gap_ms))  # clamp 50–500ms
            else:
                gap_ms = self.DEFAULT_STEP_DELAY
            a = step['action']
            steps.append(SkillStep(
                action={
                    'key':     a.get('key'),
                    'click':   a.get('click'),
                    'look':    a.get('look'),
                    'gamepad': a.get('gamepad'),
                    'source':  'skill',
                },
                delay_after_ms=gap_ms,
            ))

        avg_r = sum(s['reward'] for s in window) / len(window)

        trig_str = '_'.join(sorted(trigger))[:28]
        h = hashlib.md5((trig_str + str([s.action for s in steps])).encode()).hexdigest()[:6]
        name = f'{trig_str}_{h}'

        if name in self.skills:
            sk = self.skills[name]
            sk.avg_reward = (sk.avg_reward * sk.uses + avg_r) / (sk.uses + 1)
            sk.uses += 1
        else:
            sk = Skill(name=name, trigger_objects=trigger,
                       steps=steps, avg_reward=avg_r, uses=1)
            self.skills[name] = sk
            print(f'[SKILL] mined: {sk}')

        self.save(sk)
        return sk

    # ── Matching ──────────────────────────────────────────────
    def _is_active(self, sk) -> bool:
        return self._active_names is None or sk.name in self._active_names

    def find_best(self, current_objects: list):
        """Return (Skill, match_score) or (None, 0.0)."""
        best_sk    = None
        best_value = 0.0
        for sk in self.skills.values():
            if sk.blacklisted or not self._is_active(sk):
                continue
            match = sk.matches(current_objects)
            if match < self.MIN_MATCH_SCORE:
                continue
            value = match * max(0.1, sk.avg_reward)
            if value > best_value:
                best_value = value
                best_sk    = (sk, match)
        return best_sk if best_sk else (None, 0.0)

    def find_candidates(self, current_objects: list) -> list:
        """Return all non-blacklisted, active skills matching >=
        MIN_MATCH_SCORE, as (Skill, match_score) pairs."""
        return [(sk, match) for sk in self.skills.values()
                if not sk.blacklisted and self._is_active(sk)
                and (match := sk.matches(current_objects)) >= self.MIN_MATCH_SCORE]

    def mark_used(self, skill: Skill):
        skill.last_used = time.time()
        self.save(skill)

    # ── Verification (Voyager-style) ────────────────────────────
    def check_preconditions(self, skill: Skill, inventory: dict, objects: list) -> bool:
        """Convenience wrapper — see Skill.check_preconditions."""
        return skill.check_preconditions(inventory, objects)

    def verify_replay(self, skill: Skill, inv_before: dict, inv_after: dict):
        """Call once after a skill replay finishes. Updates success/failed
        counters, auto-blacklists on repeated failure, and persists."""
        success = skill.verify_postconditions(inv_before, inv_after)
        skill.record_outcome(success)
        self.save(skill)
        if not success:
            tag = 'BLACKLISTED' if skill.blacklisted else 'failed'
            print(f'[SKILL] {skill.name} postcondition {tag} '
                  f'(failed={skill.failed_count})')
        return success

    # ── Skill Evolution (periodic self-improvement pass) ───────
    def evolve_skills(self) -> dict:
        """Periodic skill improvement pass (called every
        config.SKILL_EVOLVE_TICKS from the runtime loop):
          1. success_count > SKILL_PROVEN_USES -> mark 'proven'
          2. failed_count > SKILL_BLACKLIST_FAILURES -> blacklist
          3. Among proven skills sharing the same trigger set, keep only
             the one with the shortest step sequence (fewest steps to
             replay); delete the rest as redundant duplicates.
        Logs a summary to data/memory/skill_evolution.jsonl.
        """
        import config as _config
        proven, blacklisted_now, merged = [], [], []

        for sk in self.skills.values():
            if sk.success_count > _config.SKILL_PROVEN_USES and not sk.blacklisted:
                proven.append(sk.name)
            if sk.failed_count > _config.SKILL_BLACKLIST_FAILURES and not sk.blacklisted:
                sk.blacklisted = True
                blacklisted_now.append(sk.name)

        # Group proven skills by canonical trigger set; keep the shortest.
        groups: dict = {}
        for sk in self.skills.values():
            if sk.name not in proven:
                continue
            key = tuple(sorted(_canonical(t) for t in sk.trigger_objects))
            groups.setdefault(key, []).append(sk)

        for key, group in groups.items():
            if len(group) < 2:
                continue
            group.sort(key=lambda s: len(s.steps))
            keep = group[0]
            for dup in group[1:]:
                merged.append({'kept': keep.name, 'removed': dup.name})
                self.delete(dup.name)

        for sk in self.skills.values():
            self.save(sk)

        summary = {
            'ts': time.time(),
            'proven': proven,
            'blacklisted': blacklisted_now,
            'merged': merged,
            'total_skills': len(self.skills),
        }
        try:
            import os as _os
            _os.makedirs(_config.MEMORY_DIR, exist_ok=True)
            with open(_os.path.join(_config.MEMORY_DIR, 'skill_evolution.jsonl'), 'a') as f:
                f.write(json.dumps(summary) + '\n')
        except Exception as e:
            print(f'[SKILL] evolution log error: {e}')

        print(f'[SKILL] evolve: proven={len(proven)} '
              f'blacklisted={len(blacklisted_now)} merged={len(merged)}')
        return summary

    # ── Management ────────────────────────────────────────────
    def list_skills(self) -> list:
        return sorted(self.skills.values(),
                      key=lambda s: s.avg_reward, reverse=True)

    def delete(self, name: str) -> bool:
        if name not in self.skills:
            return False
        del self.skills[name]
        safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in name)
        path = os.path.join(self.dir, f'{safe}.json')
        if os.path.exists(path):
            os.remove(path)
        print(f'[SKILL] deleted: {name}')
        return True

    def prune_bad(self, min_reward: float = 0.0) -> int:
        to_remove = [n for n, s in self.skills.items()
                     if (s.avg_reward < min_reward and s.uses >= 3)
                     or set(s.trigger_objects) <= HUD_ALWAYS_VISIBLE
                     or not s.has_real_action()]
        for n in to_remove:
            self.delete(n)
        return len(to_remove)

    def stats(self) -> dict:
        n = len(self.skills)
        return {
            'total':      n,
            'avg_reward': round(
                sum(s.avg_reward for s in self.skills.values()) / max(1, n), 3),
            'total_uses': sum(s.uses for s in self.skills.values()),
            'best':       max((s.name for s in self.skills.values()),
                              key=lambda n: self.skills[n].avg_reward,
                              default='none'),
        }


# ── Self test ─────────────────────────────────────────────────
if __name__ == '__main__':
    import shutil
    config.SKILLS_DIR = '/tmp/aksumael_skills_test'
    shutil.rmtree(config.SKILLS_DIR, ignore_errors=True)

    print('Skill System self-test')
    ss = SkillSystem()

    tree_objs = [{'label': 'oak', 'conf': 0.9, 'box': [10,10,50,50]}]
    wood_objs = [{'label': 'log', 'conf': 0.8, 'box': [20,20,40,40]}]

    # Feed a sequence — oak → log both canonicalise to 'tree'
    base_ts = time.time()
    ss._buffer = [
        {'objects': tree_objs, 'action': {'key':'w'}, 'reward':0.2, 'ts': base_ts},
        {'objects': tree_objs, 'action': {'key':'space'}, 'reward':0.3, 'ts': base_ts+0.15},
        {'objects': tree_objs, 'action': {'key':'e'}, 'reward':0.6, 'ts': base_ts+0.30},
        {'objects': wood_objs, 'action': {'key':'e'}, 'reward':0.8, 'ts': base_ts+0.45},
    ]
    sk = ss._mine_recent()
    print(f'\nMined: {sk}')
    print(f'Steps: {[(s.action.get("key"), s.delay_after_ms) for s in sk.steps]}')

    # Fuzzy match: 'birch' should match skill triggered by 'tree' (via synonym)
    birch_objs = [{'label': 'birch', 'conf': 0.85, 'box': [5,5,60,60]}]
    skill, score = ss.find_best(birch_objs)
    print(f'\nFuzzy match on "birch": {skill.name if skill else None} score={score:.2f}')
    assert skill is not None, 'fuzzy match failed'
    assert score >= 0.5, f'score too low: {score}'

    print(f'\nStats: {ss.stats()}')
    shutil.rmtree(config.SKILLS_DIR, ignore_errors=True)
    print('\nAll tests passed.')
