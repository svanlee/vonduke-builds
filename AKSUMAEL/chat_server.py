#!/usr/bin/env python3
"""
AKSUMAEL Chat Web UI — port 7684
Serves a dark-theme chat interface and routes messages through AKSUMAEL's LLM.
Run directly or via systemd: aksumael-chat.service
"""

import sys
import os
import threading
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, request, jsonify, make_response

from core.training_handler import _build_prompt, MAX_TOKENS, LLM_TIMEOUT
from core.llm_router import route_llm_call, _strip_fences

app = Flask(__name__)
logging.getLogger('werkzeug').setLevel(logging.ERROR)

_REPO = os.path.dirname(os.path.abspath(__file__))
_HTML_PATH = os.path.join(_REPO, 'ui', 'chat.html')

_history = []
_history_lock = threading.Lock()
MAX_HISTORY_TURNS = 8  # keep last 8 exchanges in context


def _ask(user_message: str) -> str:
    """Build AKSUMAEL prompt with conversation context, call LLM, return answer."""
    with _history_lock:
        recent = list(_history[-(MAX_HISTORY_TURNS * 2):])

    if recent:
        lines = []
        for turn in recent:
            prefix = "Scott" if turn['role'] == 'user' else "AKSUMAEL"
            lines.append(f"{prefix}: {turn['content']}")
        history_block = '\n'.join(lines)
        objective = (
            f"[Recent conversation]\n{history_block}\n\n"
            f"Scott (current): {user_message}"
        )
    else:
        objective = user_message

    try:
        prompt = _build_prompt(objective)
    except Exception as e:
        print(f'[CHAT] _build_prompt error: {e}')
        from core.identity import AKSUMAEL_IDENTITY
        prompt = f"{AKSUMAEL_IDENTITY}\n\n{objective}"

    raw = None
    try:
        raw, provider = route_llm_call(prompt, max_tokens=MAX_TOKENS, timeout=LLM_TIMEOUT)
        print(f'[CHAT] answered via {provider}')
    except Exception as e:
        print(f'[CHAT] LLM error: {e}')
        return f'(LLM error: {e})'

    if not raw:
        return '(no response from LLM)'

    try:
        answer = _strip_fences(raw).strip()
    except Exception:
        answer = (raw or '').strip()

    return answer or '(empty response)'


@app.get('/')
def index():
    try:
        html = open(_HTML_PATH, encoding='utf-8').read()
    except Exception as e:
        return f'<pre>Error loading UI: {e}</pre>', 500
    resp = make_response(html)
    resp.content_type = 'text/html; charset=utf-8'
    return resp


@app.post('/chat')
def chat():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'error': 'expected JSON body'}), 400
    message = (body.get('message') or '').strip()
    if not message:
        return jsonify({'error': 'missing message'}), 400
    if len(message) > 2000:
        return jsonify({'error': 'message too long (max 2000)'}), 400

    with _history_lock:
        _history.append({'role': 'user', 'content': message})

    answer = _ask(message)

    with _history_lock:
        _history.append({'role': 'assistant', 'content': answer})
        del _history[: max(0, len(_history) - MAX_HISTORY_TURNS * 2)]

    return jsonify({'response': answer})


@app.post('/reset')
def reset():
    """Clear conversation history (called from UI clear button)."""
    with _history_lock:
        _history.clear()
    return jsonify({'ok': True})


if __name__ == '__main__':
    print('[CHAT] AKSUMAEL Chat UI starting...')
    print('[CHAT] Open: http://192.168.0.156:7684/')
    app.run(host='0.0.0.0', port=7684, threaded=True, debug=False, use_reloader=False)
