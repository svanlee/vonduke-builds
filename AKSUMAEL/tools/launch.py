#!/usr/bin/env python3
"""
launch.py — Fully detached launcher for AKSUMAEL main.py
Reads ANTHROPIC_API_KEY from ~/.config/anthropic/key, falling back to ~/.bashrc.
Usage: python3 tools/launch.py [--log /tmp/aksumael_live.log]
"""
import subprocess
import os
import sys
import re

def get_key_from_bashrc(var_name):
    bashrc = os.path.expanduser('~/.bashrc')
    try:
        with open(bashrc) as f:
            for line in f:
                m = re.match(rf'^export\s+{var_name}=(.+)', line.strip())
                if m:
                    return m.group(1).strip().strip('"').strip("'")
    except Exception:
        pass
    return ''

def get_anthropic_key():
    key_file = os.path.expanduser('~/.config/anthropic/key')
    try:
        with open(key_file) as f:
            val = f.read().strip()
            if val:
                return val
    except Exception:
        pass
    return get_key_from_bashrc('ANTHROPIC_API_KEY')

log_path = '/tmp/aksumael_live.log'
if '--log' in sys.argv:
    log_path = sys.argv[sys.argv.index('--log') + 1]

cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
python = os.path.join(cwd, 'venv/bin/python3')

env = os.environ.copy()

# Fill in any missing keys
if not env.get('ANTHROPIC_API_KEY'):
    val = get_anthropic_key()
    if val:
        env['ANTHROPIC_API_KEY'] = val

for key in ('GEMINI_API_KEY',):
    if not env.get(key):
        val = get_key_from_bashrc(key)
        if val:
            env[key] = val

if not env.get('ANTHROPIC_API_KEY'):
    print('ERROR: ANTHROPIC_API_KEY not found in environment or ~/.bashrc')
    sys.exit(1)

print(f'ANTHROPIC_API_KEY length: {len(env["ANTHROPIC_API_KEY"])}')

with open(log_path, 'w') as log, open('/dev/null', 'r') as devnull:
    proc = subprocess.Popen(
        [python, '-u', 'main.py'],
        cwd=cwd,
        stdout=log,
        stderr=log,
        stdin=devnull,
        start_new_session=True,
        env=env,
    )

print(f'AKSUMAEL started — PID {proc.pid}, logging to {log_path}')
