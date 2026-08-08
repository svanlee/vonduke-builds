"""Unit tests for the Phase 2 skill registry (skills/registry.py).

Everything runs against mock belief-state dicts — no game, no YOLO, no disk
writes. The JSON-loading test asserts data/skills/ is left byte-identical,
since the registry is explicitly a read-only consumer of that directory.
"""

import os

import pytest

import config
from skills.registry import SkillRegistry, register_builtin_skills
from skills.skill_base import BeliefPrecondition, Skill, labels_match
from skills.skill_system import Skill as LegacySkill


# ── Fixtures ───────────────────────────────────────────────────

@pytest.fixture
def registry():
    """A fresh registry with only the three hand-authored skills."""
    r = SkillRegistry('test')
    register_builtin_skills(r)
    return r


def belief(objects=(), inventory=None, health=1.0, y_level=64,
           last_used=None, now=1000.0):
    """Build a mock belief state. Defaults: nothing visible, full health."""
    return {
        'yolo_detections': [{'label': o, 'conf': 0.9} for o in objects],
        'inventory':       dict(inventory or {}),
        'health':          health,
        'y_level':         y_level,
        'last_used_times': dict(last_used or {}),
        'now':             now,
    }


def names(skills):
    return [s.name for s in skills]


# ── find_applicable ────────────────────────────────────────────

def test_find_applicable_returns_chop_tree_when_tree_visible(registry):
    hits = registry.find_applicable(belief(objects=['tree', 'grass']))
    assert 'chop_tree' in names(hits)


def test_find_applicable_matches_specific_log_labels(registry):
    """YOLO reports 'oak_log', the precondition says 'tree' — must still match."""
    for label in ('oak_log', 'birch_log', 'log'):
        hits = registry.find_applicable(belief(objects=[label]))
        assert 'chop_tree' in names(hits), label


def test_chop_tree_absent_when_no_tree_visible(registry):
    hits = registry.find_applicable(belief(objects=['stone', 'zombie']))
    assert 'chop_tree' not in names(hits)


def test_mine_ore_applicable_on_any_ore_label(registry):
    for label in ('iron_ore', 'diamond_ore', 'deepslate_gold_ore'):
        hits = registry.find_applicable(belief(objects=[label], y_level=-20))
        assert 'mine_ore' in names(hits), label


def test_mine_ore_not_applicable_without_ore(registry):
    hits = registry.find_applicable(belief(objects=['oak_log', 'dirt']))
    assert 'mine_ore' not in names(hits)


def test_empty_belief_state_yields_no_gated_skills(registry):
    """A blank belief state must not make everything applicable."""
    assert registry.find_applicable({}) == []


def test_results_are_ordered_by_priority(registry):
    hits = registry.find_applicable(
        belief(objects=['tree', 'iron_ore'], inventory={'bread': 3}, health=0.5))
    # eat_food (3.0) > mine_ore (2.0) > chop_tree (1.0)
    assert names(hits) == ['eat_food', 'mine_ore', 'chop_tree']


# ── eat_food health gating ─────────────────────────────────────

def test_eat_food_filtered_out_when_health_is_full(registry):
    hits = registry.find_applicable(belief(inventory={'bread': 3}, health=1.0))
    assert 'eat_food' not in names(hits)


def test_eat_food_applicable_when_hurt_and_holding_food(registry):
    hits = registry.find_applicable(belief(inventory={'bread': 3}, health=0.4))
    assert 'eat_food' in names(hits)


def test_eat_food_accepts_food_synonyms(registry):
    for item in ('apple', 'golden_apple', 'carrot', 'cooked_fish'):
        hits = registry.find_applicable(belief(inventory={item: 1}, health=0.4))
        assert 'eat_food' in names(hits), item


def test_eat_food_filtered_out_with_no_food(registry):
    hits = registry.find_applicable(
        belief(inventory={'iron_pickaxe': 1, 'cobblestone': 40}, health=0.3))
    assert 'eat_food' not in names(hits)


def test_zero_count_item_does_not_satisfy_has_item(registry):
    hits = registry.find_applicable(belief(inventory={'bread': 0}, health=0.3))
    assert 'eat_food' not in names(hits)


def test_health_accepted_on_the_0_to_20_scale(registry):
    """20/20 hearts is full health, same as 1.0."""
    hits = registry.find_applicable(belief(inventory={'bread': 3}, health=20))
    assert 'eat_food' not in names(hits)
    hits = registry.find_applicable(belief(inventory={'bread': 3}, health=8))
    assert 'eat_food' in names(hits)


def test_unknown_health_does_not_block(registry):
    """Partial perception (hud_reader returning nothing) must not disable skills."""
    bs = belief(inventory={'bread': 3})
    bs.pop('health')
    assert 'eat_food' in names(registry.find_applicable(bs))


# ── describe_all ───────────────────────────────────────────────

def test_describe_all_returns_non_empty_list(registry):
    described = registry.describe_all()
    assert isinstance(described, list)
    assert len(described) == 3


def test_describe_all_entries_are_planner_readable(registry):
    by_name = {d['name']: d for d in registry.describe_all()}
    assert set(by_name) == {'chop_tree', 'eat_food', 'mine_ore'}
    for d in by_name.values():
        assert d['description']
        assert isinstance(d['requires'], dict) and d['requires']
    assert by_name['chop_tree']['requires']['yolo_visible'] == ['tree', 'log']
    assert by_name['eat_food']['requires']['max_health'] == 0.9
    # JSON-serialisable — it goes straight into an LLM prompt.
    import json
    assert json.dumps(registry.describe_all())


def test_describe_all_hides_blacklisted_by_default(registry):
    registry.get('mine_ore').blacklisted = True
    assert 'mine_ore' not in [d['name'] for d in registry.describe_all()]
    assert 'mine_ore' in [d['name'] for d in registry.describe_all(True)]


# ── cooldowns ──────────────────────────────────────────────────

def test_cooldown_blocks_recently_used_skill(registry):
    bs = belief(objects=['tree'], last_used={'chop_tree': 998.0}, now=1000.0)
    assert 'chop_tree' not in names(registry.find_applicable(bs))


def test_cooldown_expires(registry):
    bs = belief(objects=['tree'], last_used={'chop_tree': 990.0}, now=1000.0)
    assert 'chop_tree' in names(registry.find_applicable(bs))


def test_never_used_skill_is_not_on_cooldown(registry):
    assert 'chop_tree' in names(registry.find_applicable(belief(objects=['tree'])))


# ── y_level ────────────────────────────────────────────────────

def test_y_level_max_gates_skill(registry):
    registry.register(Skill(name='dig_up', description='Tunnel back to the surface.',
                            preconditions=BeliefPrecondition(y_level_max=50)))
    assert 'dig_up' in names(registry.find_applicable(belief(y_level=12)))
    assert 'dig_up' not in names(registry.find_applicable(belief(y_level=70)))


# ── registration mechanics ─────────────────────────────────────

def test_register_and_get_roundtrip(registry):
    s = Skill(name='sleep_in_bed', description='Sleep through the night.')
    assert registry.register(s) is s
    assert registry.get('sleep_in_bed') is s
    assert 'sleep_in_bed' in registry
    assert len(registry) == 4


def test_register_overwrites_by_name_and_can_refuse(registry):
    replacement = Skill(name='chop_tree', description='v2')
    registry.register(replacement)
    assert registry.get('chop_tree').description == 'v2'
    assert registry.register(Skill(name='chop_tree', description='v3'),
                             overwrite=False) is None
    assert registry.get('chop_tree').description == 'v2'


def test_register_rejects_bad_input(registry):
    with pytest.raises(TypeError):
        registry.register(LegacySkill(name='raw'))     # legacy class, not wrapped
    with pytest.raises(ValueError):
        registry.register(Skill(name=''))


def test_blacklisted_skill_is_never_applicable(registry):
    registry.get('chop_tree').blacklisted = True
    hits = registry.find_applicable(belief(objects=['tree']))
    assert 'chop_tree' not in names(hits)


def test_explain_gives_reasons_for_rejection(registry):
    reasons = registry.explain(belief(objects=['stone'], health=1.0))
    assert reasons['chop_tree'] and 'visible' in reasons['chop_tree'][0]
    assert reasons['eat_food']


# ── JSON loading (must not mutate data/skills/) ────────────────

def _dir_snapshot(path):
    return {fn: (os.path.getsize(os.path.join(path, fn)),
                 os.path.getmtime(os.path.join(path, fn)))
            for fn in sorted(os.listdir(path))}


def test_load_from_json_dir_registers_learned_skills(registry):
    before = len(registry)
    added = registry.load_from_json_dir()
    assert added > 0
    assert len(registry) == before + added
    loaded = [s for s in registry if s.source == 'json']
    assert loaded and all(s.handler == 'skill_system:replay' for s in loaded)


def test_load_from_json_dir_does_not_touch_the_directory(registry):
    """Hard constraint: the registry only reads data/skills/, never writes it."""
    before = _dir_snapshot(config.SKILLS_DIR)
    registry.load_from_json_dir()
    assert _dir_snapshot(config.SKILLS_DIR) == before


def test_hand_authored_skill_wins_over_same_named_json(registry):
    registry.load_from_json_dir()
    assert registry.get('chop_tree').source == 'hand'
    assert registry.get('chop_tree').handler == 'fsm:chop_tree'


def test_json_skill_gains_a_visibility_gate_from_its_trigger(registry):
    """A learned skill with no explicit preconditions must not be
    unconditionally applicable — its trigger becomes the gate."""
    legacy = LegacySkill(name='mine_thing', trigger_objects=['iron_ore'])
    registry.register(Skill.from_legacy(legacy))
    assert 'mine_thing' in names(registry.find_applicable(belief(objects=['iron_ore'])))
    assert 'mine_thing' not in names(registry.find_applicable(belief(objects=['grass'])))


def test_missing_json_dir_is_not_an_error(registry, tmp_path):
    assert registry.load_from_json_dir(str(tmp_path / 'nope')) == 0


def test_unreadable_json_file_is_skipped(registry, tmp_path):
    (tmp_path / 'broken.json').write_text('{not json')
    (tmp_path / 'ok.json').write_text('{"name": "fine", "trigger_objects": ["tree"]}')
    assert registry.load_from_json_dir(str(tmp_path)) == 1
    assert 'fine' in registry


# ── legacy interop (nothing above may break the old system) ────

def test_legacy_skill_class_is_unchanged():
    legacy = LegacySkill(name='mine_iron_ore', trigger_objects=['iron_ore'],
                         preconditions={'has_item': ['pickaxe'],
                                        'yolo_visible': ['iron_ore']})
    assert legacy.check_preconditions({'pickaxe': 1}, [{'label': 'iron_ore'}])
    assert not legacy.check_preconditions({}, [{'label': 'iron_ore'}])
    assert legacy.matches([{'label': 'iron_ore'}]) > 0


def test_from_legacy_preserves_all_of_semantics_for_items():
    """Legacy has_item is ALL-of; the wrapper must not silently loosen it."""
    legacy = LegacySkill(name='craft', preconditions={'has_item': ['oak_planks',
                                                                  'stick']})
    wrapped = Skill.from_legacy(legacy)
    assert wrapped.preconditions.has_item_mode == 'all'
    assert not wrapped.is_applicable(belief(inventory={'stick': 4}))
    assert wrapped.is_applicable(belief(inventory={'stick': 4, 'oak_planks': 8}))


def test_from_legacy_preserves_max_y_level_semantics():
    """Legacy fails when y_level >= max_y_level; the wrapper must match."""
    legacy = LegacySkill(name='dig_up', preconditions={'max_y_level': 60})
    wrapped = Skill.from_legacy(legacy)
    for y in (59, 60, 61):
        assert (wrapped.is_applicable(belief(y_level=y))
                == legacy.check_preconditions({}, [], y_level=y)), y


def test_from_legacy_does_not_mutate_the_wrapped_skill():
    legacy = LegacySkill(name='x', trigger_objects=['tree'], preconditions={})
    Skill.from_legacy(legacy)
    assert legacy.preconditions == {}
    assert legacy.trigger_objects == ['tree']


# ── label matching ─────────────────────────────────────────────

@pytest.mark.parametrize('required,present,expected', [
    ('tree',       'oak_log',        True),
    ('log',        'birch_log',      True),
    ('ore',        'diamond_ore',    True),
    ('food',       'bread',          True),
    ('stone',      'cobblestone',    True),
    ('apple',      'golden_apple',   True),
    ('iron_ore',   'gold_ore',       False),   # sibling ores must not match
    ('iron_ingot', 'gold_ingot',     False),   # naive token overlap would pass
    ('tree',       'stone',          False),
    ('food',       'iron_pickaxe',   False),
])
def test_labels_match(required, present, expected):
    assert labels_match(required, present) is expected
