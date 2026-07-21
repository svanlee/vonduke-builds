# code skill: dig_up v7 — mine straight up with pickaxe (no blocks needed)
# Each step: look up, break block above, jump into gap. Repeat until surface.
# Surface branch: if Y≥58 OR YOLO sees surface clues, just sprint clear.
def run_skill(executor, world_model, objects, goal, h):
    try:
        start_y = world_model.y_level or 16
    except:
        start_y = 16

    # YOLO-based surface detection: if we see animals or leaves, we're above ground.
    # Catches OCR Y errors (e.g. Y=3 misread from surface Y=63).
    _surface_labels = ('cow', 'sheep', 'pig', 'chicken', 'leaves', 'birch_log')
    _yolo_surface = any(
        str((o or {}).get('label', '')).lower() in _surface_labels
        for o in (objects or [])
    )

    if start_y >= 58 or _yolo_surface:
        # Already on surface — turn 90° right and sprint clear of shaft
        h.look_level()
        h.wait(0.2)
        executor.execute({'look': {'dx': 3000, 'dy': 0}, 'delay_ms': 20, 'source': 'code_skill'})
        h.wait(0.2)
        executor.execute({'key': 'w', 'click': None, 'delay_ms': 5000, 'source': 'code_skill'})
        h.wait(0.5)
        h.jump()
        h.wait(0.3)
        executor.execute({'key': 'w', 'click': None, 'delay_ms': 3000, 'source': 'code_skill'})
        return True

    # Mine shaft upward to surface.
    # 48 iterations × ~3.2s = ~153s well under 200s timeout.
    blocks_needed = min(48, max(46, 66 - start_y))
    h.select_slot(2)   # pickaxe slot
    h.wait(0.1)
    for i in range(blocks_needed):
        h.look_up()        # target block directly above head
        h.wait(0.05)
        h.mine(ticks=5)    # 2.25s hold — breaks stone/dirt/gravel with any pickaxe
        h.wait(0.1)
        h.jump()           # move up into the newly cleared space
        h.wait(0.6)        # wait for full jump + landing (~524ms in Minecraft)

    # Turn 90° right and sprint away from shaft opening
    h.look_level()
    h.wait(0.2)
    executor.execute({'look': {'dx': 3000, 'dy': 0}, 'delay_ms': 20, 'source': 'code_skill'})
    h.wait(0.2)
    executor.execute({'key': 'w', 'click': None, 'delay_ms': 5000, 'source': 'code_skill'})
    h.wait(0.5)
    h.jump()
    h.wait(0.3)
    executor.execute({'key': 'w', 'click': None, 'delay_ms': 3000, 'source': 'code_skill'})
    return True
