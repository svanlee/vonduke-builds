# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Skill System                       ║
# ║  Mine → match → replay full sequences with timing   ║
# ╚══════════════════════════════════════════════════════╝

import json
import os
import time
import hashlib
import threading
import config

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
_BLOCKED_SKILL_TRIGGERS = frozenset({'emerald_ore'})

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

    def __init__(self, name, trigger_objects=None, steps=None,
                 avg_reward=0.0, uses=0, created=None):
        self.name            = name
        self.trigger_objects = trigger_objects or []
        self.steps           = steps or []          # list of SkillStep
        self.avg_reward      = avg_reward
        self.uses            = uses
        self.created         = created or time.time()
        self.last_used       = None

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
        )
        s.last_used = d.get('last_used')
        return s

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
        self._load_all()

    # ── Persistence ────────────────────────────────────────────
    def _load_all(self):
        os.makedirs(self.dir, exist_ok=True)
        count = 0
        for fn in os.listdir(self.dir):
            if fn.endswith('.json'):
                try:
                    with open(os.path.join(self.dir, fn)) as f:
                        skill = Skill.from_dict(json.load(f))
                    self.skills[skill.name] = skill
                    count += 1
                except Exception as e:
                    print(f'[SKILL] load error {fn}: {e}')
        if count:
            print(f'[SKILL] loaded {count} skills from disk')

    def save(self, skill: Skill):
        os.makedirs(self.dir, exist_ok=True)
        safe = ''.join(c if c.isalnum() or c in '-_' else '_'
                       for c in skill.name)
        path = os.path.join(self.dir, f'{safe}.json')
        try:
            with open(path, 'w') as f:
                json.dump(skill.to_dict(), f, indent=2)
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

        # Trigger from objects at start of sequence
        trigger = list({
            _canonical(o.get('label', ''))
            for o in window[0]['objects']
            if o.get('label')
        })
        if not trigger:
            return None

        # HUD elements (health/armor/hunger/xp bars, hotbar) are visible on
        # every frame — a skill triggered solely by these would fire on
        # every tick. Drop HUD-only triggers rather than mining a skill
        # that can never stop looping.
        if set(trigger) <= HUD_ALWAYS_VISIBLE:
            return None

        # Don't create skills for YOLO false-positive labels (e.g. emerald_ore
        # outside mountain biomes) — they produce junk skills that loop forever.
        if set(trigger) & _BLOCKED_SKILL_TRIGGERS:
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
    def find_best(self, current_objects: list):
        """Return (Skill, match_score) or (None, 0.0)."""
        best_sk    = None
        best_value = 0.0
        for sk in self.skills.values():
            match = sk.matches(current_objects)
            if match < self.MIN_MATCH_SCORE:
                continue
            value = match * max(0.1, sk.avg_reward)
            if value > best_value:
                best_value = value
                best_sk    = (sk, match)
        return best_sk if best_sk else (None, 0.0)

    def find_candidates(self, current_objects: list) -> list:
        """Return all skills matching >= MIN_MATCH_SCORE, as (Skill, match_score) pairs."""
        return [(sk, match) for sk in self.skills.values()
                if (match := sk.matches(current_objects)) >= self.MIN_MATCH_SCORE]

    def mark_used(self, skill: Skill):
        skill.last_used = time.time()
        self.save(skill)

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
