#!/usr/bin/env python3
"""Manage the inventory sprite template library (assets/inv_templates).

The library is what behaviors/inventory_reader.py matches slot crops
against. It grows on its own while the bot plays — every sprite it has
never seen is filed under `unknown_<hash>` — so the job here is mostly
seeding it from a screenshot and putting real item names on those hashes.

    tools/inv_templates.py seed data/debug_snapshot.jpg
    tools/inv_templates.py list
    tools/inv_templates.py sheet /tmp/library.png
    tools/inv_templates.py label 3f2a91c40b1e cobblestone
    tools/inv_templates.py test data/debug_snapshot.jpg

`sheet` writes a contact sheet with each template's key printed next to it,
which is the practical way to read off the hashes you want to `label`.
"""

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vision.inventory_grid import locate_grid                    # noqa: E402
from vision.sprite_matcher import (CANON_PX, PAD_GUI, SEARCH_PX,  # noqa: E402
                                   SpriteLibrary, is_empty_crop)


def _load(path):
    frame = cv2.imread(path)
    if frame is None:
        sys.exit(f'cannot read image: {path}')
    return frame


def cmd_seed(args):
    """Add every non-empty slot of each screenshot to the library."""
    lib = SpriteLibrary()
    added = skipped = 0
    for path in args:
        frame = _load(path)
        grid = locate_grid(frame)
        if grid is None:
            print(f'{path}: no inventory GUI found — skipped')
            continue
        print(f'{path}: grid at ({grid.x0:.1f}, {grid.y0:.1f}) '
              f'pitch {grid.pitch:.2f}px')
        for slot in range(36):
            canon  = grid.crop(frame, slot, out_px=CANON_PX)
            search = grid.crop(frame, slot, pad_gui=PAD_GUI, out_px=SEARCH_PX)
            res = lib.match_or_add(search, canon, source='seed')
            if res is None:
                continue                       # empty slot
            key, label, score, is_new = res
            if is_new:
                added += 1
                print(f'  slot {slot:2d} → new template {key}')
            else:
                skipped += 1
                print(f'  slot {slot:2d} → {label} ({score:.3f}, already known)')
    lib.save()
    print(f'library now holds {len(lib)} templates '
          f'({lib.named_count} named); {added} new, {skipped} already known')


def cmd_list(_args):
    lib = SpriteLibrary()
    keys = lib.keys()
    for key in keys:
        entry = lib.entry(key)
        print(f"{key}  {entry.get('label',''):<28} "
              f"src={entry.get('source','?'):<8} hits={entry.get('hits',0)}")
    print(f'\n{len(keys)} templates, {lib.named_count} named')


def cmd_label(args):
    if len(args) != 2:
        sys.exit('usage: label <key> <item_name>')
    key, name = args
    lib = SpriteLibrary()
    if not lib.set_label(key, name):
        sys.exit(f'no such template: {key}')
    lib.save()
    print(f'{key} → {name}')


def cmd_sheet(args):
    out = args[0] if args else '/tmp/inv_templates.png'
    lib = SpriteLibrary()
    keys = lib.keys()
    if not keys:
        sys.exit('library is empty — run `seed` first')
    cols, cell, gap = 8, 96, 30
    rows = (len(keys) + cols - 1) // cols
    sheet = np.full((rows * (cell + gap), cols * cell, 3), 35, np.uint8)
    for i, key in enumerate(keys):
        r, c = divmod(i, cols)
        y = r * (cell + gap)
        big = cv2.resize(lib.template(key), (cell, cell),
                         interpolation=cv2.INTER_NEAREST)
        sheet[y + gap:y + gap + cell, c * cell:(c + 1) * cell] = big
        cv2.putText(sheet, key, (c * cell + 2, y + 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.34, (0, 255, 255), 1)
        cv2.putText(sheet, (lib.label_of(key) or '')[:16], (c * cell + 2, y + 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.34, (255, 255, 255), 1)
    cv2.imwrite(out, sheet)
    print(f'wrote {out} ({len(keys)} templates)')


def cmd_test(args):
    """Report what the library makes of every slot in a screenshot."""
    if not args:
        sys.exit('usage: test <image>')
    lib = SpriteLibrary()
    frame = _load(args[0])
    grid = locate_grid(frame)
    if grid is None:
        sys.exit('no inventory GUI found on that frame')
    hit = miss = empty = 0
    for slot in range(36):
        canon = grid.crop(frame, slot, out_px=CANON_PX)
        if canon is None:
            continue
        if is_empty_crop(canon):
            empty += 1
            continue
        search = grid.crop(frame, slot, pad_gui=PAD_GUI, out_px=SEARCH_PX)
        res = lib.match(search)
        if res is None:
            miss += 1
            print(f'  slot {slot:2d}  NO MATCH')
        else:
            hit += 1
            print(f'  slot {slot:2d}  {res[1]:<28} {res[2]:.3f}')
    total = hit + miss
    print(f'\n{hit}/{total} filled slots identified, {empty} empty')


_COMMANDS = {
    'seed': cmd_seed, 'list': cmd_list, 'label': cmd_label,
    'sheet': cmd_sheet, 'test': cmd_test,
}

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in _COMMANDS:
        sys.exit(__doc__)
    _COMMANDS[sys.argv[1]](sys.argv[2:])
