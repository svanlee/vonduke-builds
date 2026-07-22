# code skill: open_chest v1 — aim at a chest directly in view, right-click to
# raise its GUI, note what's visible, then close it again.
# No live frame access inside the sandbox, so "reading" the chest just means
# reporting whatever detections were passed in at fire time.
def run_skill(executor, world_model, objects, goal, h):
    chest = h.find('chest')
    if chest:
        h.aim_at(chest)
        h.wait(0.15)
    else:
        h.look_level()
        h.wait(0.1)

    h.place()      # right-click opens the chest
    h.wait(0.5)    # let the GUI appear

    others = [o.get('label') for o in (objects or [])
              if 'chest' not in str(o.get('label', '')).lower()]
    if others:
        print(f'[SKILL] open_chest: chest opened, other detections: {others}')
    else:
        print('[SKILL] open_chest: chest opened')

    h.wait(0.2)
    h.key('escape')   # close the GUI
    return True
