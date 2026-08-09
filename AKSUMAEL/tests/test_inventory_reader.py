"""Unit tests for the sprite-template inventory reader.

Everything runs against data/debug_snapshot.jpg — a real 1920×1080 capture
of an open chest screen — plus frames known to contain no GUI at all. No
game, no LLM, and no writes to the committed library: the template tests
build their own throwaway library in tmp_path.
"""

import os

import cv2
import numpy as np
import pytest

import config
from behaviors import inventory_reader
from behaviors.inventory_reader import InventoryReader, _clean_label, _read_failed
from vision.inventory_grid import locate_grid
from vision.sprite_matcher import (CANON_PX, PAD_GUI, SEARCH_PX, SpriteLibrary,
                                   is_empty_crop)


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI_FRAME = os.path.join(REPO, 'data', 'debug_snapshot.jpg')
# Real captures with no GUI on screen — a dark cave, a desktop, a lit scene.
NO_GUI_FRAMES = ['mc_screen.png', 'live_frame_latest.jpg', 'live_check.jpg',
                 'capture_final.jpg', 'data/hotbar_check.png']

# Slots that are bare (or bare-but-cursor-highlighted) on the reference frame.
EMPTY_SLOTS = {0, 1, 2, 3, 7, 8, 9}
FILLED_SLOTS = set(range(36)) - EMPTY_SLOTS


# ── Fixtures ───────────────────────────────────────────────────

@pytest.fixture(scope='module')
def gui_frame():
    frame = cv2.imread(GUI_FRAME)
    if frame is None:
        pytest.skip(f'reference frame missing: {GUI_FRAME}')
    return frame


@pytest.fixture(scope='module')
def grid(gui_frame):
    g = locate_grid(gui_frame)
    assert g is not None, 'reference frame must contain a locatable GUI'
    return g


@pytest.fixture
def seeded_lib(tmp_path, gui_frame, grid):
    """A library built from the reference frame, written to tmp_path."""
    lib = SpriteLibrary(root=str(tmp_path / 'templates'))
    for slot in range(36):
        canon = grid.crop(gui_frame, slot, out_px=CANON_PX)
        search = grid.crop(gui_frame, slot, pad_gui=PAD_GUI, out_px=SEARCH_PX)
        lib.match_or_add(search, canon, source='test')
    return lib


@pytest.fixture
def reader_lib(monkeypatch, seeded_lib):
    """Point the reader's process-wide library at a throwaway one.

    InventoryReader goes through a module-level singleton rooted at the
    committed assets/inv_templates. Without this the reader tests write
    their hit counts straight into that checked-in index.
    """
    monkeypatch.setattr(inventory_reader, '_LIBRARY', seeded_lib)
    monkeypatch.setattr(config, 'INVENTORY_LLM_NAMING', False)
    return seeded_lib


class FakeExecutor:
    def __init__(self):
        self.keys = []

    def execute(self, action):
        self.keys.append(action['key'])


# ── Grid location ──────────────────────────────────────────────

def test_grid_found_on_real_gui(grid):
    # The capture card delivers a ~25px slot pitch, not the 36px the old
    # hardcoded constants assumed.
    assert 20.0 < grid.pitch < 32.0
    assert grid.contrast > 40.0


@pytest.mark.parametrize('name', NO_GUI_FRAMES)
def test_no_grid_without_a_gui(name):
    frame = cv2.imread(os.path.join(REPO, name))
    if frame is None:
        pytest.skip(f'frame missing: {name}')
    assert locate_grid(frame) is None


def test_flat_pale_wall_is_not_a_panel():
    """A blank light-gray field is panel-coloured and panel-shaped but has
    no slot lattice — the case that made webcam frames read as inventories."""
    frame = np.full((1080, 1920, 3), 60, np.uint8)
    frame[400:640, 800:1050] = 198
    assert locate_grid(frame) is None


def test_slot_boxes_are_inside_the_panel(grid):
    px, py, pw, ph = grid.panel
    for slot in range(36):
        x, y, w, h = grid.slot_box(slot)
        assert px - 4 <= x and x + w <= px + pw + 4
        assert py - 4 <= y and y + h <= py + ph + 4


# ── Empty-slot detection ───────────────────────────────────────

def test_empty_and_filled_slots_are_separated(gui_frame, grid):
    empty = {s for s in range(36)
             if is_empty_crop(grid.crop(gui_frame, s, out_px=CANON_PX))}
    assert empty == EMPTY_SLOTS


# ── Template matching ──────────────────────────────────────────

def test_every_filled_slot_is_identified(gui_frame, grid, seeded_lib):
    """The whole point: 36/36 coverage, versus ~1 slot in 23 before."""
    for slot in FILLED_SLOTS:
        search = grid.crop(gui_frame, slot, pad_gui=PAD_GUI, out_px=SEARCH_PX)
        assert seeded_lib.match(search) is not None, f'slot {slot} unmatched'


def test_identical_items_share_one_template(gui_frame, grid, seeded_lib):
    """Slots 21 and 22 hold the same item, so they must report the same id."""
    def key_of(slot):
        return seeded_lib.match(
            grid.crop(gui_frame, slot, pad_gui=PAD_GUI, out_px=SEARCH_PX))[0]

    assert key_of(21) == key_of(22)


def test_different_items_get_different_templates(gui_frame, grid, seeded_lib):
    """Two blocks that correlate at 0.90 must still not be merged."""
    def key_of(slot):
        return seeded_lib.match(
            grid.crop(gui_frame, slot, pad_gui=PAD_GUI, out_px=SEARCH_PX))[0]

    assert key_of(10) != key_of(23)
    assert key_of(18) != key_of(35)


def test_matching_survives_brightness_change(gui_frame, grid, seeded_lib):
    """A hovered slot is an alpha blend, which TM_CCOEFF_NORMED ignores."""
    brighter = np.clip(gui_frame.astype(np.int16) * 1.15 + 8, 0, 255).astype(np.uint8)
    bright_grid = locate_grid(brighter)
    assert bright_grid is not None
    for slot in FILLED_SLOTS:
        search = bright_grid.crop(brighter, slot, pad_gui=PAD_GUI,
                                  out_px=SEARCH_PX)
        assert seeded_lib.match(search) is not None, f'slot {slot} lost'


def test_empty_slots_are_never_stored_as_templates(tmp_path, gui_frame, grid):
    """A flat template has no variance for a normalised correlation, and one
    once outscored a red mushroom's own template at 0.94."""
    lib = SpriteLibrary(root=str(tmp_path / 'templates'))
    for slot in EMPTY_SLOTS:
        canon = grid.crop(gui_frame, slot, out_px=CANON_PX)
        assert lib.add(canon) is None
    assert len(lib) == 0


def test_library_round_trips_through_disk(tmp_path, seeded_lib, gui_frame, grid):
    seeded_lib.set_label(seeded_lib.keys()[0], 'cobblestone')
    seeded_lib.save()

    reloaded = SpriteLibrary(root=seeded_lib.root)
    assert len(reloaded) == len(seeded_lib)
    assert reloaded.named_count == 1
    search = grid.crop(gui_frame, 20, pad_gui=PAD_GUI, out_px=SEARCH_PX)
    assert reloaded.match(search) is not None


def test_duplicate_label_is_detectable(seeded_lib):
    keys = seeded_lib.keys()
    seeded_lib.set_label(keys[0], 'oak_log')
    assert seeded_lib.label_taken_by('oak_log', exclude=keys[1]) == keys[0]
    assert seeded_lib.label_taken_by('oak_log', exclude=keys[0]) is None


# ── Reader behaviour ───────────────────────────────────────────

def test_read_reports_every_filled_slot(reader_lib, gui_frame):
    ex = FakeExecutor()
    reader = InventoryReader(ex, capture_fn=lambda: gui_frame)
    result = reader.read(force=True)
    # Counts are slots-per-item, so they sum back to the filled-slot count.
    assert sum(result.values()) == len(FILLED_SLOTS)
    assert ex.keys == ['e', 'escape']


def test_read_with_slots_gives_positions(reader_lib, gui_frame):
    reader = InventoryReader(FakeExecutor(), capture_fn=lambda: gui_frame)
    slots = reader.read_with_slots(force=True)
    for item, info in slots.items():
        assert 0 <= info['slot'] < 36
        assert reader.slot_of(item) == info['slot']
        assert reader.has(item)


def test_failed_read_keeps_the_previous_cache(reader_lib):
    """A frame with no GUI must not erase what the crafting logic knows."""
    frame = cv2.imread(os.path.join(REPO, 'mc_screen.png'))
    if frame is None:
        pytest.skip('mc_screen.png missing')
    ex = FakeExecutor()
    reader = InventoryReader(ex, capture_fn=lambda: frame)
    reader._cache = {'cobblestone': {'count': 3, 'slot': 5}}

    assert reader.read(force=True) == {'cobblestone': 3}
    # Never opened, so it toggles 'e' back rather than firing Escape.
    assert ex.keys == ['e', 'e']


def test_no_camera_keeps_the_cache_and_presses_nothing(reader_lib):
    ex = FakeExecutor()
    reader = InventoryReader(ex, capture_fn=lambda: None)
    reader._cache = {'stick': {'count': 1, 'slot': 0}}

    assert reader.read(force=True) == {'stick': 1}
    assert ex.keys == []


def test_genuinely_empty_inventory_clears_the_cache(reader_lib, gui_frame, grid):
    """An empty inventory is a real answer, unlike a failed read."""
    frame = gui_frame.copy()
    for slot in range(36):
        x, y, w, h = grid.slot_box(slot)
        frame[int(y):int(y + h), int(x):int(x + w)] = (139, 139, 139)

    reader = InventoryReader(FakeExecutor(), capture_fn=lambda: frame)
    reader._cache = {'stale': {'count': 9, 'slot': 1}}
    assert reader.read(force=True) == {}


def test_read_failed_is_distinguishable_from_empty():
    assert _read_failed().get('parse_error') is True
    assert {}.get('parse_error') is None


# ── Label cleaning ─────────────────────────────────────────────

@pytest.mark.parametrize('raw,expected', [
    ('oak_log',                      'oak_log'),
    ('minecraft:cobblestone',        'cobblestone'),
    ('"minecraft:iron_pickaxe"',     'iron_pickaxe'),      # quoted replies
    ("['iron_pickaxe' 'pickaxe']",   'iron_pickaxe'),      # numpy repr
    ('  Torch \n',                   'torch'),
    ('This is a diamond sword',      None),                # a sentence, not an id
    ('',                             None),
    (None,                           None),
])
def test_clean_label(raw, expected):
    assert _clean_label(raw) == expected
