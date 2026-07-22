# code skill: open_chest v2 — aim at chest, open GUI, transfer items via
# shift-click, then close.
#
# Direction is inferred from the goal string:
#   "store" / "deposit" / "put" / "stash"  → player inventory → chest
#   anything else (default)                 → chest → player inventory
#
# Slot coordinates match chest_manager.py exactly (single chest layout):
#   Chest grid:   rows 0-2, cols 0-8  (y≈28-44%, x≈31.5-67.5%)
#   Player main:  rows 0-2, cols 0-8  (y≈59.5-75.5%)
#   Player hotbar: col 0-8            (y≈84.5%)
def run_skill(executor, world_model, objects, goal, h):
    # ── 1. Aim at chest ───────────────────────────────────────────
    chest = h.find('chest')
    if chest:
        h.aim_at(chest)
        h.wait(0.15)
    else:
        h.look_level()
        h.wait(0.1)

    # ── 2. Open chest (right-click) ───────────────────────────────
    h.place()
    h.wait(0.7)   # let the chest GUI fully render

    # ── 3. Coordinate map (single chest) ──────────────────────────
    CHEST_X0,  CHEST_DX  = 31.5, 4.5
    CHEST_Y0,  CHEST_DY  = 28.0, 8.0
    PLAYER_X0, PLAYER_DX = 31.5, 4.5
    PLAYER_Y0, PLAYER_DY = 59.5, 8.0
    PLAYER_HOTBAR_Y      = 84.5

    goal_lower = (goal or '').lower()
    storing = any(w in goal_lower for w in ('store', 'deposit', 'put', 'stash'))

    # ── 4. Transfer ───────────────────────────────────────────────
    if storing:
        # Shift-click every player inventory slot to dump into chest
        for row in range(3):
            for col in range(9):
                x = PLAYER_X0 + col * PLAYER_DX
                y = PLAYER_Y0 + row * PLAYER_DY
                executor.execute({'key': 'shift', 'click': [x, y],
                                  'gamepad': None, 'source': 'chest'})
                h.wait(0.12)
        for col in range(9):
            x = PLAYER_X0 + col * PLAYER_DX
            executor.execute({'key': 'shift', 'click': [x, PLAYER_HOTBAR_Y],
                              'gamepad': None, 'source': 'chest'})
            h.wait(0.12)
        print('[SKILL] open_chest v2: deposited inventory into chest')
    else:
        # Shift-click every chest slot to pull into player inventory
        for row in range(3):
            for col in range(9):
                x = CHEST_X0 + col * CHEST_DX
                y = CHEST_Y0 + row * CHEST_DY
                executor.execute({'key': 'shift', 'click': [x, y],
                                  'gamepad': None, 'source': 'chest'})
                h.wait(0.12)
        print('[SKILL] open_chest v2: retrieved chest contents to inventory')

    # ── 5. Close GUI ──────────────────────────────────────────────
    h.wait(0.2)
    h.key('escape')
    return True
