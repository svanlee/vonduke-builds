"""
AKSUMAEL Stats Dashboard
Run: python3 tools/dashboard.py
Visit: http://localhost:5758
"""

from flask import Flask, render_template_string
from pathlib import Path
import json, glob

app = Flask(__name__)
MEMORY_FILE = Path("data/world_memory.json")

HTML = """<!DOCTYPE html>
<html>
<head>
<title>AKSUMAEL Dashboard</title>
<meta http-equiv="refresh" content="10">
<style>
* { box-sizing: border-box; }
body { background: #0d1117; color: #c9d1d9; font-family: 'Courier New', monospace; padding: 24px; margin: 0; }
h1 { color: #58a6ff; margin: 0 0 8px 0; }
.subtitle { color: #8b949e; margin-bottom: 24px; font-size: 13px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
.card h3 { color: #58a6ff; margin: 0 0 12px 0; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; }
.stat { display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid #21262d; font-size: 13px; }
.stat:last-child { border-bottom: none; }
.val { color: #7ee787; font-weight: bold; }
.bar-wrap { margin: 4px 0; }
.bar-label { font-size: 11px; color: #8b949e; margin-bottom: 2px; }
.bar-bg { background: #21262d; border-radius: 4px; height: 14px; }
.bar-fill { background: #238636; height: 14px; border-radius: 4px; transition: width 0.3s; }
.events { max-height: 200px; overflow-y: auto; font-size: 11px; color: #8b949e; }
.event { padding: 2px 0; border-bottom: 1px solid #21262d; }
</style>
</head>
<body>
<h1>🎮 AKSUMAEL</h1>
<div class="subtitle">Auto-refreshes every 10s</div>
<div class="grid" id="grid">{{ content }}</div>
</body>
</html>"""

def load_memory():
    if not MEMORY_FILE.exists():
        return {}
    try:
        return json.loads(MEMORY_FILE.read_text())
    except Exception:
        return {}

def render_cards(mem):
    if not mem:
        return "<div class='card'><h3>No data yet</h3><p>AKSUMAEL hasn't run long enough to generate stats.</p></div>"

    seen = mem.get("seen_objects", {})
    events = mem.get("recent_events", [])

    # Sort seen objects by count
    top_objects = sorted(seen.items(), key=lambda x: -x[1])[:15]
    max_count = top_objects[0][1] if top_objects else 1

    bars = ""
    for name, count in top_objects:
        pct = int(count / max_count * 100)
        bars += f"""
        <div class="bar-wrap">
          <div class="bar-label">{name} ({count})</div>
          <div class="bar-bg"><div class="bar-fill" style="width:{pct}%"></div></div>
        </div>"""

    event_html = "".join(f"<div class='event'>{e}</div>" for e in reversed(events[-30:]))

    goal = mem.get("current_goal", "explore")
    depth = mem.get("depth_estimate", 64)
    y_level = mem.get("y_level", depth)
    biome = mem.get("biome", "unknown")
    hunger = mem.get("hunger_level", 20)
    pickaxe = mem.get("pickaxe_uses", 0)

    return f"""
    <div class="card">
      <h3>Session</h3>
      <div class="stat"><span>Total ticks</span><span class="val">{mem.get('total_ticks', 0)}</span></div>
      <div class="stat"><span>Deaths</span><span class="val">{mem.get('deaths', 0)}</span></div>
      <div class="stat"><span>Surveys</span><span class="val">{mem.get('surveys', 0)}</span></div>
      <div class="stat"><span>Session #</span><span class="val">{mem.get('session', 1)}</span></div>
    </div>
    <div class="card">
      <h3>State</h3>
      <div class="stat"><span>Goal</span><span class="val">{goal}</span></div>
      <div class="stat"><span>Y-level</span><span class="val">{y_level}</span></div>
      <div class="stat"><span>Biome</span><span class="val">{biome}</span></div>
      <div class="stat"><span>Hunger</span><span class="val">{hunger}/20</span></div>
      <div class="stat"><span>Pickaxe uses</span><span class="val">{pickaxe}</span></div>
    </div>
    <div class="card">
      <h3>Objects Seen</h3>
      {bars}
    </div>
    <div class="card">
      <h3>Recent Events</h3>
      <div class="events">{event_html or '<div class="event">None yet</div>'}</div>
    </div>"""

@app.route("/")
def index():
    mem = load_memory()
    content = render_cards(mem)
    return render_template_string(HTML, content=content)

@app.route("/api/memory")
def api_memory():
    from flask import jsonify
    return jsonify(load_memory())

if __name__ == "__main__":
    print("[DASHBOARD] http://localhost:5758")
    app.run(host="0.0.0.0", port=5758, debug=False)
