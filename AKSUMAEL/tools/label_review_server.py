#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Label Review Server                ║
# ║  Web UI to review/approve/reject Claude auto-labels   ║
# ╚══════════════════════════════════════════════════════╝
#
# Run: python3 tools/label_review_server.py
# Visit: http://localhost:5757

from flask import Flask, send_file, jsonify, request, render_template_string
from pathlib import Path
import json, shutil, os

app = Flask(__name__)
REVIEW_DIR = Path("data/label_review")
APPROVED_DIR = Path("data/label_review/approved")
REJECTED_DIR = Path("data/label_review/rejected")
APPROVED_DIR.mkdir(parents=True, exist_ok=True)
REJECTED_DIR.mkdir(parents=True, exist_ok=True)

HTML = """<!DOCTYPE html>
<html>
<head>
<title>AKSUMAEL Label Review</title>
<style>
body { background: #1a1a2e; color: #eee; font-family: monospace; padding: 20px; }
img { max-width: 800px; border: 2px solid #555; }
.controls { margin: 20px 0; }
button { padding: 10px 30px; margin: 5px; font-size: 16px; cursor: pointer; }
.approve { background: #2d6a4f; color: white; border: none; border-radius: 4px; }
.reject { background: #6a2d2d; color: white; border: none; border-radius: 4px; }
.labels { background: #16213e; padding: 10px; margin: 10px 0; white-space: pre; }
.count { color: #aaa; margin-bottom: 20px; }
</style>
</head>
<body>
<h2>🎮 AKSUMAEL Label Review</h2>
<div class="count" id="count">Loading...</div>
<div id="frame-container">
  <img id="frame-img" src="" />
  <div class="labels" id="labels-text"></div>
  <div class="controls">
    <button class="approve" onclick="vote('approve')">✅ Approve</button>
    <button class="reject" onclick="vote('reject')">❌ Reject</button>
  </div>
</div>
<script>
let current = null;
async function loadNext() {
  const r = await fetch('/next');
  const d = await r.json();
  if (!d.name) { document.getElementById('frame-container').innerHTML = '<p>All reviewed! 🎉</p>'; return; }
  current = d.name;
  document.getElementById('frame-img').src = '/image/' + d.name;
  document.getElementById('labels-text').textContent = JSON.stringify(d.labels, null, 2);
  document.getElementById('count').textContent = d.remaining + ' frames remaining';
}
async function vote(action) {
  if (!current) return;
  await fetch('/vote', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name: current, action})});
  loadNext();
}
loadNext();
</script>
</body>
</html>"""

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/next")
def next_frame():
    frames = [f for f in REVIEW_DIR.glob("*.jpg") if not (APPROVED_DIR / f.name).exists() and not (REJECTED_DIR / f.name).exists()]
    frames += [f for f in REVIEW_DIR.glob("*.png") if not (APPROVED_DIR / f.name).exists() and not (REJECTED_DIR / f.name).exists()]
    if not frames:
        return jsonify({"name": None, "remaining": 0})
    f = frames[0]
    label_file = REVIEW_DIR / f"{f.stem}.json"
    labels = json.loads(label_file.read_text()) if label_file.exists() else []
    return jsonify({"name": f.name, "labels": labels, "remaining": len(frames)})

@app.route("/image/<name>")
def serve_image(name):
    path = (REVIEW_DIR / Path(name).name).resolve()
    if path.parent == REVIEW_DIR.resolve() and path.exists():
        return send_file(path)
    return "not found", 404

@app.route("/vote", methods=["POST"])
def vote():
    data = request.get_json()
    name, action = Path(data["name"]).name, data["action"]
    src = REVIEW_DIR / name
    stem = Path(name).stem
    label = REVIEW_DIR / f"{stem}.json"
    dest = APPROVED_DIR if action == "approve" else REJECTED_DIR
    if src.exists(): shutil.move(str(src), str(dest / name))
    if label.exists(): shutil.move(str(label), str(dest / label.name))
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5757, debug=False)
