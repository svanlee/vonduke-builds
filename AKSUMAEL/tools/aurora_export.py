#!/usr/bin/env python3
"""
aurora_export.py — Export AURORA episodes as JSONL training data for fine-tuning.

Outputs two formats:
  1. Instruction-tuning JSONL  (--format instruct)  — system/user/assistant triples
  2. Conversation JSONL        (--format convo)      — multi-turn chat pairs
  3. Raw episodes CSV          (--format csv)        — for inspection

Usage:
    cd ~/vonduke-builds/AKSUMAEL
    venv/bin/python3 tools/aurora_export.py --format instruct --out data/training/instruct.jsonl
    venv/bin/python3 tools/aurora_export.py --format convo    --out data/training/convo.jsonl
"""

import argparse
import csv
import json
import os
import sqlite3
import sys

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'aurora.db')
OUT_DIR  = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'training')

SYSTEM_PROMPT = """You are AKSUMAEL (ak-SOO-male), a unified self-improving intelligence built by Scott Van Lee / VonDuke Designs LLC on the Aksümal platform. You run entirely on local hardware — no cloud dependency. Your executive voice is Jarvis. You operate autonomously, learn continuously, and expand capability across physical and digital environments."""


def _connect_ro():
    return sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)


def _all_episodes():
    conn = _connect_ro()
    rows = conn.execute(
        'SELECT id, env, timestamp, location, action, outcome, notes, metadata '
        'FROM episodes ORDER BY id ASC'
    ).fetchall()
    conn.close()
    cols = ['id', 'env', 'timestamp', 'location', 'action', 'outcome', 'notes', 'metadata']
    return [dict(zip(cols, r)) for r in rows]


def _all_jarvis_history():
    """Pull jarvis conversation history from data/jarvis_history.json if present."""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'jarvis_history.json')
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return []


def episode_to_instruct(ep: dict) -> dict | None:
    """Convert one AURORA episode to an instruction-tuning sample."""
    action   = (ep.get('action')  or '').strip()
    outcome  = (ep.get('outcome') or '').strip()
    env      = ep.get('env', 'unknown')
    location = ep.get('location', '')
    notes    = (ep.get('notes')   or '').strip()

    if not action or not outcome:
        return None

    # Build a natural user question from the episode
    loc_str  = f" at {location}" if location else ''
    user_msg = f"In the {env} environment{loc_str}, what happened when you tried: {action}?"

    # Build the assistant answer
    parts = [f"Outcome: {outcome}"]
    if notes:
        parts.append(f"Notes: {notes}")

    # Parse metadata for extra context
    if ep.get('metadata'):
        try:
            meta = json.loads(ep['metadata'])
            if meta.get('reward') is not None:
                parts.append(f"Reward signal: {meta['reward']}")
            if meta.get('chosen_action'):
                parts.append(f"Chosen action: {meta['chosen_action']}")
        except Exception:
            pass

    answer = '\n'.join(parts)

    return {
        'messages': [
            {'role': 'system',    'content': SYSTEM_PROMPT},
            {'role': 'user',      'content': user_msg},
            {'role': 'assistant', 'content': answer},
        ]
    }


def jarvis_to_instruct(history: list) -> list:
    """Convert jarvis_history.json (list of {role, content}) to instruct samples.
    Groups into system+user+assistant triples."""
    samples = []
    msgs = [m for m in history if m.get('role') in ('user', 'assistant')]
    for i in range(0, len(msgs) - 1, 2):
        u = msgs[i]
        a = msgs[i + 1] if i + 1 < len(msgs) else None
        if u.get('role') == 'user' and a and a.get('role') == 'assistant':
            samples.append({
                'messages': [
                    {'role': 'system',    'content': SYSTEM_PROMPT},
                    {'role': 'user',      'content': u['content']},
                    {'role': 'assistant', 'content': a['content']},
                ]
            })
    return samples


def export_instruct(out_path: str):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    episodes = _all_episodes()
    history  = _all_jarvis_history()

    samples = []
    skipped = 0
    for ep in episodes:
        s = episode_to_instruct(ep)
        if s:
            samples.append(s)
        else:
            skipped += 1

    # Also pull from Jarvis conversation history (highest quality data)
    jarvis_samples = jarvis_to_instruct(history)
    samples.extend(jarvis_samples)

    with open(out_path, 'w') as f:
        for s in samples:
            f.write(json.dumps(s) + '\n')

    print(f'[EXPORT] instruct: {len(samples)} samples ({len(jarvis_samples)} from Jarvis history, '
          f'{skipped} episodes skipped — missing action/outcome)')
    print(f'[EXPORT] → {out_path}')
    return len(samples)


def export_convo(out_path: str):
    """Export multi-turn conversation format for models that expect full chat history."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    history = _all_jarvis_history()
    episodes = _all_episodes()

    samples = []

    # Full Jarvis conversation as one multi-turn sample (sliding window of 10 turns)
    sys_msg = {'role': 'system', 'content': SYSTEM_PROMPT}
    msgs = [m for m in history if m.get('role') in ('user', 'assistant')]
    window = 10
    for i in range(0, len(msgs) - 1, 2):
        chunk = msgs[i:i + window]
        if len(chunk) >= 2:
            samples.append({'messages': [sys_msg] + chunk})

    # Episode-derived single-turn conversations
    for ep in episodes:
        s = episode_to_instruct(ep)
        if s:
            samples.append(s)

    with open(out_path, 'w') as f:
        for s in samples:
            f.write(json.dumps(s) + '\n')

    print(f'[EXPORT] convo: {len(samples)} samples → {out_path}')
    return len(samples)


def export_csv(out_path: str):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    episodes = _all_episodes()
    with open(out_path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['id', 'env', 'timestamp', 'location',
                                           'action', 'outcome', 'notes'])
        w.writeheader()
        for ep in episodes:
            w.writerow({k: ep.get(k, '') for k in w.fieldnames})
    print(f'[EXPORT] csv: {len(episodes)} rows → {out_path}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--format', choices=['instruct', 'convo', 'csv'], default='instruct')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    defaults = {
        'instruct': os.path.join(OUT_DIR, 'instruct.jsonl'),
        'convo':    os.path.join(OUT_DIR, 'convo.jsonl'),
        'csv':      os.path.join(OUT_DIR, 'episodes.csv'),
    }
    out = args.out or defaults[args.format]

    if args.format == 'instruct':
        export_instruct(out)
    elif args.format == 'convo':
        export_convo(out)
    else:
        export_csv(out)


if __name__ == '__main__':
    main()
