# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Item Sprite Template Matcher       ║
# ║  Identifies inventory slot icons by pixel template    ║
# ║  matching instead of asking a vision model.           ║
# ╚══════════════════════════════════════════════════════╝
#
# Minecraft item icons are deterministic pixel art: cobblestone is the same
# 16×16 sprite every single time it appears. That makes classification a
# lookup, not a perception problem — and the local vision model was a bad
# fit for it, returning roughly one usable label per 23 slots (see the
# mesh-llm GUI-bias note and behaviors/inventory_reader.py's history).
#
# The library is content-addressed: a template's filename is a hash of its
# own pixels, and index.json maps that hash to an item name. A sprite the
# bot has never seen is added on the spot under an `unknown_<hash>` name and
# can be named later — by the LLM, by tools/inv_templates.py, or by hand —
# without disturbing anything that already matches it. Identity is therefore
# stable from the first sighting, and the name catches up when it can.
#
# ── What is matched, and what is deliberately ignored ────────────────────
# A slot is not just its icon. Minecraft draws the stack size across the
# bottom of the cell and a durability bar under that, both of which change
# while the item does not. Matching the whole crop would score a stack of 64
# cobblestone against a stack of 12 as a different item, so the decision is
# made on the top half only — the one band no overlay can reach:
#
#     ┌────────────────┐
#     │       A        │   A: top half     — decides the match
#     ├──────┬─────────┤
#     │  B   │ /////// │   B: bottom-left  — breaks ties only
#     └──────┴─────────┘   ///: stack count + durability bar — ignored
#
# Region B is narrow because a two-digit stack count is 11 gui units wide
# and right-aligned, so it reaches most of the way across the cell. Folding
# B into the score outright measured *worse* than leaving it out (same-item
# margin +0.028 vs +0.039 on the reference frame) — for many sprites the
# bottom-left corner is plain background and only adds noise. It earns its
# place solely as a tie-break, for items that differ below the midline and
# nowhere above it: two potions, say, whose glass is identical and whose
# liquid is not.
#
# Both regions use TM_CCOEFF_NORMED, which subtracts the mean and
# normalises the variance. That is what makes a slot the mouse happens to be
# hovering still match: the hover highlight is an alpha blend, i.e. an
# affine change in intensity, and this metric is invariant to exactly that.

import hashlib
import json
import os
import time

import cv2
import numpy as np


# Canonical space: every crop and template is resampled to this before it is
# compared, so a template captured at one GUI scale still matches a crop
# taken at another.
#
# 48px for a 16-gui-unit icon oversamples the ~22px the capture card gives
# us, deliberately. The template slides in whole canonical pixels, so the
# canonical resolution sets how finely it can align — at 32px the step is
# half a gui unit and a half-pixel camera drift cost ~0.10 of score; at 48px
# the step is a third of a unit and the same drift costs ~0.04. 64px was
# measured too and barely improved on 48 while costing more per compare.
CANON_PX = 48                      # a 16-gui-unit icon at 3px per gui unit
PAD_GUI  = 2.0                     # slack cropped around the icon, in gui units
SEARCH_PX = int(CANON_PX + 2 * PAD_GUI * (CANON_PX / 16))   # 60
_SHIFT = (SEARCH_PX - CANON_PX) // 2                        # 6 — max px offset

# Region A — top half, full width.
_A_ROWS, _A_COLS = CANON_PX // 2, CANON_PX
# Region B — bottom-left. A two-digit stack count is 11 gui units wide and
# right-aligned, so only the leftmost ~3.5 units of the row are ever clear.
_B_ROW0, _B_COLS = CANON_PX // 2, int(CANON_PX * 3.5 / 16)

# Candidates whose region-A scores are within this of the best are treated
# as tied and re-ranked on region B.
_TIE_BAND = 0.02

# At or above this the crop and the template are the same sprite, and there
# is no point comparing the rest of the library. Same-item-different-count
# pairs measured 0.958-0.982, so this only short-circuits genuine identity.
_EXACT_MATCH = 0.995

# Below this, a crop is flat enough to be an empty slot rather than an item.
# Real slots on the live feed measure ≥21; empty ones ≤2 (including the
# brighter shade of a slot under the mouse cursor).
EMPTY_STD = 8.0

# Measured on the reference frame (data/debug_snapshot.jpg): a sprite scores
# 1.000 against its own template, the same item at a different stack count
# ≥0.958, and the closest unrelated pair — two differently-coloured blocks —
# 0.902. Perturbing the frame (re-encoding at JPEG q60, rescaling 0.8×–1.35×,
# shifting it half a pixel, brightening it) keeps every true match ≥0.923.
#
# Erring high is the cheap direction. An over-strict threshold files a second
# template for an item already in the library, which costs a little disk and
# one naming call; an over-loose one reports the wrong item to the crafting
# logic, which acts on it.
DEFAULT_THRESHOLD = 0.92
DEFAULT_ROOT = 'assets/inv_templates'

UNKNOWN_PREFIX = 'unknown_'

# Empty slots are deliberately NOT kept as templates. A bare slot is a flat
# patch of #8b8b8b, and a flat template has no variance for TM_CCOEFF_NORMED
# to normalise by, so its score is meaningless — in testing it came back at
# 0.94 against a red mushroom and outscored the mushroom's own template.
# `is_empty_crop` settles the question on its own, with a 10× margin between
# the two populations, so the matcher never has to consider the case.


def is_empty_crop(canon_crop) -> bool:
    """True when a canonical crop is a bare slot background.

    Median-blurred first so JPEG ringing off the capture card doesn't read
    as detail, then measured over the middle of the crop to stay clear of
    the slot's own bevelled edge.
    """
    if canon_crop is None or canon_crop.size == 0:
        return True
    med = cv2.medianBlur(canon_crop, 3)
    core = med[4:CANON_PX - 4, 4:CANON_PX - 4].astype(np.float32)
    return float(core.reshape(-1, core.shape[-1]).std(axis=0).mean()) < EMPTY_STD


def crop_key(canon_crop) -> str:
    """Content hash naming a template file. Quantised so that two captures
    of the same sprite that differ only by capture-card noise still land on
    the same key rather than spawning a near-duplicate template."""
    q = (canon_crop.astype(np.uint16) // 8 * 8).astype(np.uint8)
    return hashlib.sha1(q.tobytes()).hexdigest()[:12]


def _match_a(search, template):
    """Best region-A score and the offset that produced it.

    The template slides over ±_SHIFT canonical pixels, which is what absorbs
    a sub-pixel error in the grid fit. Returns (score, (row, col)).
    """
    try:
        smap = cv2.matchTemplate(
            search[0:_A_ROWS + 2 * _SHIFT, 0:_A_COLS + 2 * _SHIFT],
            template[0:_A_ROWS, 0:_A_COLS], cv2.TM_CCOEFF_NORMED)
    except cv2.error:
        return -2.0, (0, 0)
    _, score, _, loc = cv2.minMaxLoc(smap)
    return float(score), (loc[1], loc[0])


def _score_b(search, template, offset) -> float:
    """Region-B score at the alignment region A settled on.

    Reusing A's offset rather than searching again keeps the tie-break
    honest: it asks "given this placement, does the bottom-left agree too?"
    instead of letting B find some unrelated position where it happens to.
    """
    row, col = offset
    win = search[_B_ROW0 + row:_B_ROW0 + row + (CANON_PX - _B_ROW0),
                 col:col + _B_COLS]
    tmpl = template[_B_ROW0:CANON_PX, 0:_B_COLS]
    if win.shape != tmpl.shape:
        return -2.0
    try:
        return float(cv2.matchTemplate(win, tmpl, cv2.TM_CCOEFF_NORMED)[0, 0])
    except cv2.error:
        return -2.0


class SpriteLibrary:
    """A growing, on-disk library of item sprites keyed by their pixels."""

    def __init__(self, root: str = DEFAULT_ROOT,
                 threshold: float = DEFAULT_THRESHOLD):
        self.root = root
        self.threshold = threshold
        self._index: dict[str, dict] = {}     # key → {label, source, added, hits}
        self._tmpl:  dict[str, np.ndarray] = {}
        # float32 copies, kept because match() runs every template against
        # every filled slot and the conversion would otherwise dominate.
        self._f32:   dict[str, np.ndarray] = {}
        self._index_mtime = 0.0
        self._dirty = False
        self.load()

    # ── Disk ──────────────────────────────────────────────────────

    @property
    def index_path(self) -> str:
        return os.path.join(self.root, 'index.json')

    def load(self):
        """(Re)read index.json and every template PNG it names."""
        self._index, self._tmpl, self._f32 = {}, {}, {}
        self._index_mtime = 0.0
        try:
            with open(self.index_path) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        self._index = data.get('templates', {}) or {}
        self._index_mtime = self._mtime()
        for key in list(self._index):
            img = cv2.imread(os.path.join(self.root, f'{key}.png'), cv2.IMREAD_COLOR)
            if img is None:
                # Indexed but the PNG is gone — drop it rather than let a
                # phantom entry claim a label nothing can ever match.
                self._index.pop(key, None)
                continue
            if img.shape[:2] != (CANON_PX, CANON_PX):
                img = cv2.resize(img, (CANON_PX, CANON_PX),
                                 interpolation=cv2.INTER_AREA)
            self._tmpl[key] = img
            self._f32[key] = img.astype(np.float32)

    def _mtime(self) -> float:
        try:
            return os.path.getmtime(self.index_path)
        except OSError:
            return 0.0

    def reload_if_changed(self):
        """Pick up labels edited on disk without restarting the bot."""
        if self._mtime() != self._index_mtime:
            self.load()

    def save(self):
        """Write index.json. Templates themselves are written by `add`."""
        if not self._dirty:
            return
        os.makedirs(self.root, exist_ok=True)
        tmp = self.index_path + '.tmp'
        try:
            with open(tmp, 'w') as fh:
                json.dump({'version': 1, 'templates': self._index}, fh,
                          indent=1, sort_keys=True)
            os.replace(tmp, self.index_path)
            self._index_mtime = self._mtime()
            self._dirty = False
        except OSError as exc:
            print(f'[SPRITE] could not save index: {exc}')
            try:
                os.remove(tmp)
            except OSError:
                pass

    # ── Contents ──────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._tmpl)

    @property
    def named_count(self) -> int:
        """Templates that carry a real item name rather than a placeholder."""
        return sum(1 for e in self._index.values()
                   if not str(e.get('label', '')).startswith(UNKNOWN_PREFIX))

    def _hits_of(self, key: str) -> int:
        return int(self._index.get(key, {}).get('hits', 0))

    def keys(self) -> list[str]:
        """Template keys, ordered by label so listings group by item."""
        return sorted(self._tmpl, key=lambda k: self.label_of(k) or '')

    def template(self, key: str):
        """The stored 48×48 canonical crop for `key`, or None."""
        return self._tmpl.get(key)

    def entry(self, key: str) -> dict:
        """The index record for `key` — label, source, added, hits."""
        return dict(self._index.get(key, {}))

    def label_of(self, key: str) -> str | None:
        entry = self._index.get(key)
        return entry.get('label') if entry else None

    def unknown_keys(self) -> list[str]:
        return [k for k, e in self._index.items()
                if str(e.get('label', '')).startswith(UNKNOWN_PREFIX)]

    def label_taken_by(self, label: str, exclude: str = '') -> str | None:
        """The key already carrying `label`, if some other template does.

        Two distinct sprites cannot be the same item, so a name that is
        already spoken for is evidence the new one is a guess.
        """
        for key, entry in self._index.items():
            if key != exclude and entry.get('label') == label:
                return key
        return None

    def set_label(self, key: str, label: str, source: str = 'manual') -> bool:
        """Name (or rename) a template. Returns False for an unknown key."""
        if key not in self._index:
            return False
        self._index[key]['label']  = label
        self._index[key]['source'] = source
        self._dirty = True
        return True

    # ── Matching ──────────────────────────────────────────────────

    def match(self, search_crop) -> tuple[str, str, float] | None:
        """Identify a padded slot crop.

        `search_crop` is SEARCH_PX square — the icon plus PAD_GUI of margin
        on each side, which is what gives the template room to slide and
        absorb a sub-pixel error in the grid fit.

        Returns (key, label, score) for the best template above threshold,
        or None if nothing matches well enough.
        """
        if search_crop is None or not self._tmpl:
            return None
        if search_crop.shape[:2] != (SEARCH_PX, SEARCH_PX):
            search_crop = cv2.resize(search_crop, (SEARCH_PX, SEARCH_PX),
                                     interpolation=cv2.INTER_AREA)
        search = search_crop.astype(np.float32)

        # Test the templates that have been matching lately first. Every
        # comparison is exact, so order changes nothing about the answer —
        # it just means the early exit below usually fires within the first
        # few candidates instead of after the whole library.
        scored = []
        for key in sorted(self._tmpl, key=self._hits_of, reverse=True):
            score, offset = _match_a(search, self._f32[key])
            if score >= self.threshold:
                scored.append((score, offset, key))
                if score >= _EXACT_MATCH:
                    # Pixel-for-pixel the same sprite. Nothing else in the
                    # library can beat it, and the tie-break has nothing
                    # left to decide.
                    scored = [scored[-1]]
                    break

        if not scored:
            return None

        if len(scored) > 1:
            # Several templates clear the bar on the top half alone. Anything
            # essentially tied with the leader gets re-ranked on region B,
            # which is the only place items identical above the midline can
            # still differ.
            top = max(s for s, _, _ in scored)
            tied = [c for c in scored if c[0] >= top - _TIE_BAND]
            if len(tied) > 1:
                tied.sort(key=lambda c: _score_b(search, self._f32[c[2]], c[1]),
                          reverse=True)
                scored = tied
            else:
                scored.sort(reverse=True)

        best_score, _, best_key = scored[0]
        entry = self._index.get(best_key, {})
        entry['hits'] = int(entry.get('hits', 0)) + 1
        self._dirty = True
        return best_key, entry.get('label', UNKNOWN_PREFIX + best_key), best_score

    def match_or_add(self, search_crop, canon_crop,
                     source: str = 'auto') -> tuple | None:
        """Identify a slot, filing it as a new sprite if nothing matches.

        Returns (key, label, score, is_new), or None for an empty slot. This
        is the path that makes the library self-growing: a sprite the bot
        has never seen becomes a template the moment it is seen, so the
        second sighting is a hit rather than another miss.
        """
        if canon_crop is None or is_empty_crop(canon_crop):
            return None
        found = self.match(search_crop)
        if found is not None:
            key, label, score = found
            return key, label, score, False
        key = self.add(canon_crop, source=source)
        if key is None:
            return None
        return key, self.label_of(key), 1.0, True

    def add(self, canon_crop, label: str | None = None,
            source: str = 'auto') -> str | None:
        """Store a new template and return its key.

        Called with a crop nothing in the library matched, so the sprite is
        new by construction — but the key is still checked, because the same
        unmatched sprite can show up twice in one read.
        """
        if canon_crop is None or canon_crop.size == 0:
            return None
        if is_empty_crop(canon_crop):
            return None            # see the EMPTY note at the top of this file
        if canon_crop.shape[:2] != (CANON_PX, CANON_PX):
            canon_crop = cv2.resize(canon_crop, (CANON_PX, CANON_PX),
                                    interpolation=cv2.INTER_AREA)
        key = crop_key(canon_crop)
        if key in self._tmpl:
            if label:
                self.set_label(key, label, source)
            return key

        os.makedirs(self.root, exist_ok=True)
        path = os.path.join(self.root, f'{key}.png')
        if not cv2.imwrite(path, canon_crop):
            print(f'[SPRITE] could not write template {path}')
            return None

        self._tmpl[key] = canon_crop
        self._f32[key]  = canon_crop.astype(np.float32)
        self._index[key] = {
            'label':  label or (UNKNOWN_PREFIX + key),
            'source': source if label else 'auto',
            'added':  round(time.time(), 1),
            'hits':   0,
        }
        self._dirty = True
        return key
