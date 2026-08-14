#!/usr/bin/env python3
"""
AKSUMAEL conversational training server — port 7684.

Uses JarvisBrain directly: persistent history, AURORA logging, full tool access.
Accessible on LAN at http://192.168.0.156:7684
HUD overlay at http://192.168.0.156:7684/hud

Usage:
    cd /home/ros/vonduke-builds/AKSUMAEL
    venv/bin/python3 tools/chat_server.py
"""
import sys, os, datetime, json, subprocess, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify, Response
from jarvis.brain import get_brain

PORT = 7684
app = Flask(__name__)

# ── System status helper ────────────────────────────────────────────────────────

def get_system_status() -> dict:
    """Collect real-time system vitals for the HUD endpoint."""
    status: dict = {}

    # GPU — nvidia-smi
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3
        )
        parts = [p.strip() for p in r.stdout.strip().split(",")]
        status["gpu"] = {
            "used_mb":  int(parts[0]),
            "total_mb": int(parts[1]),
            "util_pct": int(parts[2]),
        }
    except Exception:
        status["gpu"] = None

    # Services
    svcs = ["aksumael", "mesh-llm", "aksumael-chat"]
    status["services"] = {}
    for svc in svcs:
        try:
            r = subprocess.run(
                ["systemctl", "--user", "is-active", f"{svc}.service"],
                capture_output=True, text=True, timeout=2
            )
            status["services"][svc] = r.stdout.strip() == "active"
        except Exception:
            status["services"][svc] = False

    # RAM — /proc/meminfo
    try:
        mi: dict = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                mi[k.strip()] = int(v.split()[0])
        total_mb = mi["MemTotal"] // 1024
        avail_mb = mi["MemAvailable"] // 1024
        status["ram"] = {"used_mb": total_mb - avail_mb, "total_mb": total_mb}
    except Exception:
        status["ram"] = None

    # CPU — two-sample delta from /proc/stat (quick)
    try:
        def _stat():
            with open("/proc/stat") as f:
                parts = f.readline().split()
            vals = [int(x) for x in parts[1:]]
            return vals[3], sum(vals)   # idle, total
        import time as _time
        i1, t1 = _stat(); _time.sleep(0.1); i2, t2 = _stat()
        dt = t2 - t1
        status["cpu_used_pct"] = round((1 - (i2 - i1) / dt) * 100, 1) if dt else 0.0
    except Exception:
        status["cpu_used_pct"] = None

    # AURORA
    aurora_db = os.path.expanduser("~/vonduke-builds/AKSUMAEL/data/aurora.db")
    try:
        conn = sqlite3.connect(aurora_db, timeout=2)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM episodes")
        status["aurora_episodes"] = c.fetchone()[0]
        try:
            c.execute("SELECT COUNT(*) FROM human_episodes")
            status["aurora_prefs"] = c.fetchone()[0]
        except Exception:
            status["aurora_prefs"] = 0
        conn.close()
    except Exception:
        status["aurora_episodes"] = None
        status["aurora_prefs"] = None

    # Goals
    goals_file = os.path.expanduser("~/vonduke-builds/AKSUMAEL/data/goals.json")
    try:
        with open(goals_file) as f:
            g = json.load(f)
        current = g.get("current", "")
        stack   = g.get("stack", [])
        status["goals"] = ([current] if current else []) + [s for s in stack[:4] if s != current]
    except Exception:
        status["goals"] = []

    status["ts"] = datetime.datetime.now().isoformat()
    return status


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
  .hud-link { margin-left: auto; font-size: 0.7em; color: #00e5ff88; text-decoration: none; letter-spacing: 1px; }
  .hud-link:hover { color: #00e5ff; }
</style>
</head>
<body>
<div id="header">
  <h1>⬡ AKSUMAEL</h1>
  <span id="status">● ONLINE</span>
  <span style="font-size:0.75em;color:#555;">Qwen3-8B · Local · Port 7684</span>
  <a href="/hud" target="_blank" class="hud-link">[ HUD ↗ ]</a>
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


# ── Jarvis HUD ─────────────────────────────────────────────────────────────────
_HUD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AKSUMAEL · JARVIS HUD</title>
<style>
:root {
  --cyan: #00e5ff;
  --cyan-dim: #00e5ff18;
  --cyan-mid: #00e5ff55;
  --green: #39ff14;
  --red: #ff3333;
  --orange: #ff8c00;
  --bg: #020509;
  --panel: #030810;
  --border: #00e5ff14;
  --text: #5a8fa8;
  --bright: #b0d8e8;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; overflow: hidden; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: 'Courier New', monospace;
  position: relative;
}

/* hexagonal grid bg */
body::before {
  content: '';
  position: fixed; inset: 0;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='56' height='100'%3E%3Cpath d='M28 66L0 50V18L28 2l28 16v32z' fill='none' stroke='%2300e5ff07' stroke-width='1'/%3E%3C/svg%3E");
  pointer-events: none; z-index: 0;
}

#app { position: relative; z-index: 1; height: 100vh; display: grid; grid-template-rows: 52px 1fr 28px; }

/* ── header ── */
#hdr {
  display: flex; align-items: center; gap: 14px;
  padding: 0 22px;
  border-bottom: 1px solid var(--border);
  background: linear-gradient(90deg, #010408, #020b12, #010408);
}
.logo { font-size: 1.15em; color: var(--cyan); letter-spacing: 5px; font-weight: bold; text-shadow: 0 0 12px var(--cyan); }
.sep { color: var(--border); font-size: 1.2em; }
.subtitle { font-size: 0.6em; letter-spacing: 3px; color: #2a4a5a; }
.conn-wrap { display: flex; align-items: center; gap: 6px; }
.dot { width: 7px; height: 7px; border-radius: 50%; }
.dot.on { background: var(--green); box-shadow: 0 0 8px var(--green); animation: blink 2.5s ease-in-out infinite; }
.dot.off { background: var(--red); box-shadow: 0 0 8px var(--red); }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.35} }
.conn-label { font-size: 0.65em; letter-spacing: 2px; }
.conn-label.on { color: var(--green); }
.conn-label.off { color: var(--red); }
#clock { margin-left: auto; font-size: 0.8em; color: var(--cyan); letter-spacing: 2px; opacity: 0.8; }

/* ── main grid ── */
#grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  grid-template-rows: 1fr 1fr;
  gap: 1px;
  background: var(--border);
}
.panel {
  background: var(--panel);
  padding: 16px 18px;
  position: relative;
  overflow: hidden;
}
.panel::after {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent 0%, var(--cyan-mid) 50%, transparent 100%);
  opacity: 0.5;
}

/* scan line */
.scan {
  position: absolute; left: 0; right: 0; height: 60px;
  background: linear-gradient(transparent, #00e5ff08, transparent);
  pointer-events: none;
  animation: scanmove 5s linear infinite;
}
@keyframes scanmove { from{top:-60px} to{top:100%} }

.ptitle {
  font-size: 0.55em; letter-spacing: 3px; color: var(--cyan);
  opacity: 0.55; margin-bottom: 12px; text-transform: uppercase;
}

/* bars */
.brow { margin: 7px 0; }
.blabel { display: flex; justify-content: space-between; font-size: 0.68em; margin-bottom: 3px; }
.blabel span:last-child { color: var(--bright); }
.btrack { height: 5px; border-radius: 3px; background: #0a1525; overflow: hidden; }
.bfill { height: 100%; border-radius: 3px; transition: width 0.6s cubic-bezier(.4,0,.2,1); }
.bfill.gpu  { background: linear-gradient(90deg,#0090cc,var(--cyan)); box-shadow:0 0 6px #00e5ff44; }
.bfill.ram  { background: linear-gradient(90deg,#005588,#0099bb); }
.bfill.cpu  { background: linear-gradient(90deg,#003344,#006688); }
.big { font-size: 2em; color: var(--cyan); font-weight: bold; line-height: 1; letter-spacing: -1px; }
.unit { font-size: 0.5em; color: var(--text); margin-left: 3px; }

/* services */
.srow {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 0; border-bottom: 1px solid var(--border);
}
.srow:last-child { border-bottom: none; }
.sname { flex: 1; font-size: 0.78em; letter-spacing: 1px; color: var(--bright); }
.stag { font-size: 0.62em; padding: 2px 8px; border-radius: 10px; letter-spacing: 1px; }
.stag.on  { color: var(--green); background: #39ff1410; border: 1px solid #39ff1430; }
.stag.off { color: var(--red);   background: #ff333310; border: 1px solid #ff333330; }

/* goals */
.gitem {
  padding: 5px 0; border-bottom: 1px solid var(--border);
  font-size: 0.76em; color: var(--bright); white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}
.gitem:last-child { border-bottom: none; }
.gitem.active { color: var(--cyan); }
.gitem.active::before { content: '▶ '; }
.gitem:not(.active)::before { content: '  '; }
.gempty { font-size: 0.72em; color: #1a3040; font-style: italic; }

/* aurora */
.arow {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 0; border-bottom: 1px solid var(--border);
}
.arow:last-child { border-bottom: none; }
.akey { font-size: 0.68em; letter-spacing: 1px; }
.aval { font-size: 0.85em; color: var(--cyan); font-weight: bold; }

/* model */
.mtag {
  font-size: 0.72em; background: var(--cyan-dim);
  border: 1px solid var(--cyan-mid); color: var(--cyan);
  padding: 3px 10px; border-radius: 12px; display: inline-block;
  margin-bottom: 10px; letter-spacing: 1px;
}
.minfo { font-size: 0.7em; color: var(--text); line-height: 2; }
.minfo span { color: var(--bright); }

/* footer */
#footer {
  display: flex; align-items: center; padding: 0 22px;
  border-top: 1px solid var(--border);
  font-size: 0.58em; letter-spacing: 2px; color: #1a3040;
  background: #010408;
}
#footer .right { margin-left: auto; }

/* corner accents */
.panel::before {
  content: '';
  position: absolute; bottom: 0; right: 0;
  width: 20px; height: 20px;
  border-bottom: 1px solid var(--cyan-mid);
  border-right:  1px solid var(--cyan-mid);
  opacity: 0.3;
}
</style>
</head>
<body>
<div id="app">

  <!-- header -->
  <div id="hdr">
    <span class="logo">⬡ AKSUMAEL</span>
    <span class="sep">|</span>
    <span class="subtitle">JARVIS HUD · v1</span>
    <span class="sep">|</span>
    <span class="conn-wrap">
      <span class="dot on" id="cdot"></span>
      <span class="conn-label on" id="clbl">ONLINE</span>
    </span>
    <span id="clock"></span>
  </div>

  <!-- 3×2 grid -->
  <div id="grid">

    <!-- GPU -->
    <div class="panel">
      <div class="scan"></div>
      <div class="ptitle">GPU · VRAM</div>
      <div class="brow">
        <div class="blabel"><span>VRAM USED</span><span id="gpu-mb">— MB</span></div>
        <div class="btrack"><div class="bfill gpu" id="gpu-bar" style="width:0%"></div></div>
      </div>
      <div style="margin-top:14px">
        <span class="big" id="gpu-pct">—</span><span class="unit">% VRAM</span>
      </div>
      <div class="brow" style="margin-top:12px">
        <div class="blabel"><span>GPU UTIL</span><span id="gpu-util">—%</span></div>
        <div class="btrack"><div class="bfill cpu" id="gpu-util-bar" style="width:0%"></div></div>
      </div>
    </div>

    <!-- System -->
    <div class="panel">
      <div class="scan" style="animation-delay:-2s"></div>
      <div class="ptitle">SYSTEM · COMPUTE</div>
      <div class="brow">
        <div class="blabel"><span>RAM</span><span id="ram-mb">— GB</span></div>
        <div class="btrack"><div class="bfill ram" id="ram-bar" style="width:0%"></div></div>
      </div>
      <div class="brow" style="margin-top:10px">
        <div class="blabel"><span>CPU</span><span id="cpu-pct">—%</span></div>
        <div class="btrack"><div class="bfill cpu" id="cpu-bar" style="width:0%"></div></div>
      </div>
    </div>

    <!-- Services -->
    <div class="panel">
      <div class="ptitle">SERVICES</div>
      <div id="svc-list"></div>
    </div>

    <!-- Goals -->
    <div class="panel">
      <div class="scan" style="animation-delay:-3s"></div>
      <div class="ptitle">ACTIVE GOALS</div>
      <div id="goals-list"></div>
    </div>

    <!-- AURORA -->
    <div class="panel">
      <div class="ptitle">AURORA · MEMORY</div>
      <div class="arow"><span class="akey">EPISODES</span><span class="aval" id="a-ep">—</span></div>
      <div class="arow"><span class="akey">PREFERENCE PAIRS</span><span class="aval" id="a-pref">—</span></div>
      <div class="arow"><span class="akey">SYNC TIME</span><span class="aval" id="a-ts" style="font-size:0.72em;letter-spacing:1px">—</span></div>
    </div>

    <!-- Model -->
    <div class="panel">
      <div class="ptitle">MODEL · INFERENCE</div>
      <div class="mtag" id="m-name">Qwen3-8B · Local</div>
      <div class="minfo">
        Port <span>9337</span> · Temp <span>0.2</span><br>
        Fully local · <span>No external API</span><br>
        AURORA logging <span>ACTIVE</span>
      </div>
    </div>

  </div>

  <!-- footer -->
  <div id="footer">
    <span>AKSUMAEL INTELLIGENCE PLATFORM</span>
    <span class="right" id="last-sync">LAST SYNC —</span>
  </div>

</div>
<script>
// Clock
function tick() {
  const n = new Date();
  document.getElementById('clock').textContent =
    n.toLocaleTimeString('en-US',{hour12:false}) + ' · ' +
    n.toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric'});
}
tick(); setInterval(tick, 1000);

function setBar(id, pct) {
  const el = document.getElementById(id);
  if (el) el.style.width = Math.min(100, Math.max(0, pct)) + '%';
}
function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

async function refresh() {
  try {
    const r = await fetch('/status');
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();

    // Connection indicator
    document.getElementById('cdot').className = 'dot on';
    document.getElementById('clbl').className = 'conn-label on';
    document.getElementById('clbl').textContent = 'ONLINE';

    // GPU
    if (d.gpu) {
      const pct = Math.round(d.gpu.used_mb / d.gpu.total_mb * 100);
      setText('gpu-pct', pct);
      setText('gpu-mb', d.gpu.used_mb + ' / ' + d.gpu.total_mb + ' MB');
      setBar('gpu-bar', pct);
      setText('gpu-util', d.gpu.util_pct + '%');
      setBar('gpu-util-bar', d.gpu.util_pct);
    }

    // RAM
    if (d.ram) {
      const pct = Math.round(d.ram.used_mb / d.ram.total_mb * 100);
      setText('ram-mb', (d.ram.used_mb/1024).toFixed(1) + ' / ' + (d.ram.total_mb/1024).toFixed(1) + ' GB');
      setBar('ram-bar', pct);
    }

    // CPU
    if (d.cpu_used_pct !== null) {
      setText('cpu-pct', d.cpu_used_pct.toFixed(1) + '%');
      setBar('cpu-bar', d.cpu_used_pct);
    }

    // Services
    const svcs = d.services || {};
    document.getElementById('svc-list').innerHTML = Object.entries(svcs).map(([n, active]) =>
      `<div class="srow">
        <span class="sname">${n}</span>
        <span class="stag ${active?'on':'off'}">${active?'ACTIVE':'DOWN'}</span>
        <span class="dot ${active?'on':'off'}" style="width:6px;height:6px"></span>
      </div>`
    ).join('');

    // Goals
    const goals = d.goals || [];
    document.getElementById('goals-list').innerHTML = goals.length
      ? goals.map((g, i) => `<div class="gitem${i===0?' active':''}">${g}</div>`).join('')
      : '<div class="gempty">no active goals</div>';

    // AURORA
    if (d.aurora_episodes !== null) {
      setText('a-ep',   d.aurora_episodes);
      setText('a-pref', d.aurora_prefs || 0);
      setText('a-ts',   d.ts ? d.ts.slice(11,19) : '—');
    }

    // Footer
    setText('last-sync', 'LAST SYNC ' + new Date().toLocaleTimeString('en-US',{hour12:false}));

  } catch(e) {
    document.getElementById('cdot').className = 'dot off';
    document.getElementById('clbl').className = 'conn-label off';
    document.getElementById('clbl').textContent = 'OFFLINE';
  }
}

refresh(); setInterval(refresh, 2000);
</script>
</body>
</html>"""


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return Response(_HTML, mimetype="text/html")


@app.route("/hud", methods=["GET"])
def hud():
    return Response(_HUD_HTML, mimetype="text/html")


@app.route("/status", methods=["GET"])
def status():
    try:
        return jsonify(get_system_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
    print(f"[AKSUMAEL] Jarvis HUD     → http://192.168.0.156:{PORT}/hud")
    print(f"[AKSUMAEL] Status API     → http://192.168.0.156:{PORT}/status")
    print(f"[AKSUMAEL] Using JarvisBrain — history + AURORA logging enabled")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
