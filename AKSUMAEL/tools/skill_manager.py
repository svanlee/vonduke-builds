#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Skill Manager CLI                  ║
# ╚══════════════════════════════════════════════════════╝
#
# Usage:
#   python3 tools/skill_manager.py list
#   python3 tools/skill_manager.py show <name>
#   python3 tools/skill_manager.py delete <name>
#   python3 tools/skill_manager.py prune [min_reward]
#   python3 tools/skill_manager.py stats

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from skills.skill_system import SkillSystem


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    ss  = SkillSystem()
    cmd = sys.argv[1]

    if cmd == 'list':
        skills = ss.list_skills()
        if not skills:
            print('No skills learned yet.')
            return
        print(f'{"NAME":<30} {"REWARD":>8} {"USES":>5} {"STEPS":>6} TRIGGER')
        print('-' * 80)
        for s in skills:
            trig = ','.join(s.trigger_objects[:3])
            print(f'{s.name:<30} {s.avg_reward:>8.3f} {s.uses:>5} '
                  f'{len(s.actions):>6} {trig}')

    elif cmd == 'show' and len(sys.argv) > 2:
        name = sys.argv[2]
        if name in ss.skills:
            s = ss.skills[name]
            print(f'Skill: {s.name}')
            print(f'  Trigger objects: {s.trigger_objects}')
            print(f'  Avg reward:      {s.avg_reward:.3f}')
            print(f'  Uses:            {s.uses}')
            print(f'  Action sequence:')
            for i, a in enumerate(s.actions):
                print(f'    {i+1}. key={a.get("key")} '
                      f'click={a.get("click")} gp={a.get("gamepad")}')
        else:
            print(f'Skill not found: {name}')

    elif cmd == 'delete' and len(sys.argv) > 2:
        name = sys.argv[2]
        if ss.delete(name):
            print(f'Deleted: {name}')
        else:
            print(f'Not found: {name}')

    elif cmd == 'prune':
        min_r = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
        n = ss.prune_bad(min_r)
        print(f'Pruned {n} skills below reward {min_r}')

    elif cmd == 'stats':
        print('Skill stats:', ss.stats())

    else:
        print(__doc__)


if __name__ == '__main__':
    main()
