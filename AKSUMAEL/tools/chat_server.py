#!/usr/bin/env python3
"""
AKSUMAEL conversational training server — port 7684.

Uses JarvisBrain directly: persistent history, AURORA logging, full tool access.
Accessible on LAN at http://192.168.0.156:7684

Usage:
    cd /home/ros/vonduke-builds/AKSUMAEL
    venv/bin/python3 tools/chat_server.py
"""
import sys, os, datetime, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify, Response
from jarvis.brain import get_brain

PORT = 7684
app = Flask(__name__)

# ── Static chat UI ─────────────────────────────────────────────────────────────
_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AKSUMAEL — Conversational Interface</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0a0e14; color: #c9d1d9; font-family: 'Courier New', monospace; height: 100vh; display: flex; flex-direction: column; }
  #header { background: #0d1117; border-bottom: 1px solid #00e5ff33; padding: 12px 20px; display: flex; align-items: center; gap: 12px; }
  #header h1 { font-size: 1.1em; color: #00e5ff; letter-spacing: 3px; font-weight: bold; }
  #status { font-size: 0.7em; color: #30d158; background: #30d15820; padding: 3px 10px; border-radius: 12px; border: 1px solid #30d15840; }
  #chat { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 14px; }
  .msg { max-width: 80%; padding: 12px 16px; border-radius: 8px; line-height: 1.5; font-size: 0.9em; white-space: pre-wrap; word-break: break-word; }
  .user { align-self: flex-end; background: #1c3d5a; border: 1px solid #00e5ff44; color: #c9d1d9; }
  .aksumael { align-self: flex-start; background: #0d1f2d; border: 1px solid #00e5ff22; color: #a8d8ea; }
  .aksumael .name { color: #00e5ff; font-size: 0.75em; margin-bottom: 6px; letter-spacing: 1px; }
  .system { align-self: center; color: #555; font-size: 0.75em; font-style: italic; }
  #input-row { display: flex; gap: 10px; padding: 14px 20px; background: #0d1117; border-top: 1px solid #00e5ff22; }
  #msg-input { flex: 1; background: #161b22; border: 1px solid #30363d; border-radius: 6px; color: #c9d1d9; padding: 10px 14px; font-size: 0.9em; font-family: inherit; resize: none; height: 44px; }
  #msg-input:focus { outline: none; border-color: #00e5ff66; }
  #send-btn { background: #00e5ff22; border: 1px solid #00e5ff55; color: #00e5ff; padding: 0 20px; border-radius: 6px; cursor: pointer; font-size: 0.85em; letter-spacing: 1px; transition: background 0.2s; }
  #send-btn:hover { background: #00e5ff44; }
  #send-btn:disabled { opacity: 0.4; cursor: default; }
  #clear-btn { background: transparent; border: 1px solid #555; color: #888; padding: 0 12px; border-radius: 6px; cursor: pointer; font-size: 0.8em; }
  #clear-btn:hover { border-color: #888; color: #aaa; }
  .thinking { color: #555; font-style: italic; }
</style>
</head>
<body>
<div id="header">
  <h1>⬡ AKSUMAEL</h1>
  <span id="status">● ONLINE</span>
  <span style="margin-left:auto;font-size:0.75em;color:#555;">Qwen3-8B · Local · Port 7684</span>
</div>
<div id="chat">
  <div class="msg system">Session started — AKSUMAEL is ready. All messages are logged to AURORA memory.</div>
</div>
<div id="input-row">
  <textarea id="msg-input" placeholder="Talk to AKSUMAEL..." rows="1"></textarea>
  <button id="clear-btn" onclick="clearHistory()">CLEAR</button>
  <button id="send-btn" onclick="send()">SEND</button>
</div>
<script>
const chat = document.getElementById('chat');
const input = document.getElementById('msg-input');
const btn = document.getElementById('send-btn');

input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});

function addMsg(role, text) {
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  if (role === 'aksumael') {
    div.innerHTML = '<div class="name">AKSUMAEL</div>' + escHtml(text);
  } else {
    div.textContent = text;
  }
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}

function escHtml(t) {
  return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>');
}

async function send() {
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  btn.disabled = true;
  addMsg('user', text);
  const thinking = addMsg('aksumael', '');
  thinking.querySelector('.name').insertAdjacentHTML('afterend', '<span class="thinking">thinking…</span>');

  try {
    const res = await fetch('/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text})
    });
    const d = await res.json();
    thinking.innerHTML = '<div class="name">AKSUMAEL</div>' + escHtml(d.answer || d.error || '(no response)');
  } catch (e) {
    thinking.innerHTML = '<div class="name">AKSUMAEL</div><span style="color:#ff6b6b">Connection error</span>';
  }
  btn.disabled = false;
  chat.scrollTop = chat.scrollHeight;
}

async function clearHistory() {
  await fetch('/clear', {method: 'POST'});
  chat.innerHTML = '<div class="msg system">History cleared.</div>';
}
</script>
</body>
</html>"""


@app.route("/", methods=["GET"])
def index():
    return Response(_HTML, mimetype="text/html")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "model": "Qwen3-8B-local", "port": PORT})


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json or {}
    message = (data.get("message") or data.get("question") or "").strip()
    if not message:
        return jsonify({"error": "missing 'message' field"}), 400
    try:
        brain = get_brain()
        answer = brain.respond(message)
        return jsonify({"answer": answer, "ts": datetime.datetime.now().isoformat()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/ask", methods=["POST"])
def ask():
    """Backward-compat alias for /chat."""
    return chat()


@app.route("/clear", methods=["POST"])
def clear():
    try:
        get_brain().clear_history()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print(f"[AKSUMAEL] Chat interface → http://192.168.0.156:{PORT}/")
    print(f"[AKSUMAEL] Using JarvisBrain — history + AURORA logging enabled")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
