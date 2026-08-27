# core/frame_server.py
"""
AKSUMAEL Jarvis HUD — MJPEG stream + live status overlay.

Routes:
  GET /         → Jarvis HUD (HTML)
  GET /stream   → MJPEG camera stream (~15 fps)
  GET /frame    → single JPEG snapshot
  GET /status   → JSON state (goal, objects, voice, thoughts, tick, uptime)

External callers:
  frame_server.push_thought(text)          — from cognitive / voice
  frame_server.update_state(**kwargs)      — from runtime tick loop
"""
import collections
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

import cv2
import numpy as np

# ── shared state (written by runtime/cognitive, read by HTTP handler) ─────────

_pipeline_ref = None
_hud_lock     = threading.Lock()
_thoughts     = collections.deque(maxlen=24)   # newest first
_hud_state    = {
    'goal':       'idle',
    'mode':       'training',
    'voice_mode': 'on',
    'voice_text': '',
    'tick':       0,
    'uptime':     0,
    'objects':    [],
}
_boot_ts = time.time()


def push_thought(text: str):
    """Add a thought to the HUD monologue stream. Thread-safe."""
    text = (text or '').strip()
    if not text:
        return
    with _hud_lock:
        _thoughts.appendleft({'t': time.time(), 'text': text[:300]})


def update_state(**kwargs):
    """Update HUD state dict. Call from runtime tick loop. Thread-safe."""
    with _hud_lock:
        _hud_state.update(kwargs)
        _hud_state['uptime'] = int(time.time() - _boot_ts)


# ── HTTP infrastructure ────────────────────────────────────────────────────────

class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads      = True
    allow_reuse_address = True


class _Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass   # silence per-request noise

    def do_GET(self):
        if self.path == '/stream':
            self._serve_mjpeg()
        elif self.path == '/frame':
            self._serve_single_jpeg()
        elif self.path == '/status':
            self._serve_status()
        else:
            self._serve_html()

    # ── helpers ───────────────────────────────────────────────────────────

    def _get_annotated_frame(self):
        if _pipeline_ref is None:
            return _blank_frame('No pipeline')
        frame, objs = _pipeline_ref.display.get_display_frame()
        if frame is None:
            return _blank_frame('No signal')
        out = frame.copy()
        for obj in (objs or []):
            box = obj.get('box')
            if not box or len(box) < 4:
                continue
            x1, y1, x2, y2 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
            base_label = f"{obj.get('label','?')} {obj.get('conf',0):.2f}"
            # Show entity_id if reid_bridge has assigned one
            eid = obj.get('entity_id')
            tid = obj.get('track_id')
            if eid is not None:
                label = f"{base_label} e#{eid}"
                col = (50, 255, 100)   # green = confirmed entity
            elif tid is not None:
                label = f"{base_label} t#{tid}"
                col = (0, 200, 255)    # cyan = tracked, no entity yet
            else:
                label = base_label
                col = (80, 80, 200) if obj.get('unknown') else (0, 220, 255)
            cv2.rectangle(out, (x1, y1), (x2, y2), col, 2)
            cv2.putText(out, label, (x1, max(y1 - 5, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
        return out

    def _encode_jpeg(self, frame):
        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 78])
        return buf.tobytes()

    # ── routes ────────────────────────────────────────────────────────────

    def _serve_mjpeg(self):
        self.send_response(200)
        self.send_header('Content-Type',
                         'multipart/x-mixed-replace; boundary=frame')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        try:
            while True:
                frame = self._get_annotated_frame()
                data  = self._encode_jpeg(frame)
                self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\n\r\n')
                self.wfile.write(data)
                self.wfile.write(b'\r\n')
                self.wfile.flush()
                time.sleep(1 / 15)
        except Exception:
            pass

    def _serve_single_jpeg(self):
        frame = self._get_annotated_frame()
        data  = self._encode_jpeg(frame)
        self.send_response(200)
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    def _serve_status(self):
        # Live objects from pipeline
        objs = []
        if _pipeline_ref is not None:
            try:
                _, raw = _pipeline_ref.display.get_display_frame()
                if raw:
                    objs = [{'label': o.get('label', '?'),
                              'conf':  round(o.get('conf', 0), 2)}
                             for o in raw[:12]]
            except Exception:
                pass

        # Voice mode from file (authoritative)
        voice_mode = 'on'
        try:
            vm = open('data/axon_mode.txt').read().strip()
            if vm in ('on', 'off', 'ptt'):
                voice_mode = vm
        except Exception:
            pass

        with _hud_lock:
            payload = dict(_hud_state)
            payload['objects']    = objs or payload.get('objects', [])
            payload['voice_mode'] = voice_mode
            payload['thoughts']   = list(_thoughts)[:10]

        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self):
        html = _JARVIS_HTML.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(html)))
        self.end_headers()
        self.wfile.write(html)


# ── public API ────────────────────────────────────────────────────────────────

class FrameServer:
    DEFAULT_PORT = 8765

    def __init__(self, pipeline, port: int = DEFAULT_PORT):
        global _pipeline_ref
        _pipeline_ref = pipeline
        self._port   = port
        self._server = _ThreadingHTTPServer(('0.0.0.0', port), _Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name='FrameServer',
            daemon=True,
        )

    def start(self):
        self._thread.start()
        print(f'[DISPLAY] Frame server → http://localhost:{self._port}/')

    def stop(self):
        self._server.shutdown()


# ── helpers ───────────────────────────────────────────────────────────────────

def _blank_frame(msg: str = 'No signal') -> np.ndarray:
    img = np.zeros((360, 640, 3), dtype=np.uint8)
    cv2.putText(img, msg, (200, 180),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 60, 80), 2, cv2.LINE_AA)
    return img


# ── Jarvis HUD ────────────────────────────────────────────────────────────────

_JARVIS_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>AKSUMAEL // NEURAL MIND</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}

body{
  background:#020c10;
  color:#00d4ff;
  font-family:'Courier New',monospace;
  font-size:12px;
  height:100vh;
  overflow:hidden;
  user-select:none;
}

/* dot-grid bg */
body::after{
  content:'';
  position:fixed;top:0;left:0;right:0;bottom:0;
  background-image:radial-gradient(circle at 1px 1px,rgba(0,212,255,.04) 1px,transparent 0);
  background-size:28px 28px;
  pointer-events:none;z-index:0;
}

/* scan-line sweep */
.scan{
  position:fixed;top:0;left:0;right:0;height:2px;
  background:linear-gradient(90deg,transparent,rgba(0,212,255,.12),rgba(0,212,255,.35),rgba(0,212,255,.12),transparent);
  animation:scan 6s linear infinite;
  pointer-events:none;z-index:999;
}
@keyframes scan{0%{transform:translateY(-2px)}100%{transform:translateY(100vh)}}

/* scanline texture */
body::before{
  content:'';
  position:fixed;top:0;left:0;right:0;bottom:0;
  background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,.03) 2px,rgba(0,0,0,.03) 4px);
  pointer-events:none;z-index:998;
}

/* ── LAYOUT: left status | center mind | right vision ── */
.hud{
  position:relative;z-index:1;
  display:grid;
  grid-template-columns:210px 1fr 220px;
  grid-template-rows:36px 1fr;
  height:100vh;
  gap:4px;padding:4px;
}

/* ── HEADER ── */
.header{
  grid-column:1/-1;
  display:flex;align-items:center;justify-content:space-between;
  border-bottom:1px solid rgba(0,212,255,.2);
  padding:0 12px;
}
.logo{
  display:flex;align-items:center;gap:10px;
  font-size:14px;font-weight:bold;letter-spacing:6px;
  color:#00d4ff;text-shadow:0 0 18px rgba(0,212,255,.6);
}
.logo-dot{
  width:8px;height:8px;border-radius:50%;
  background:#00ff88;box-shadow:0 0 10px #00ff88;
  animation:blink 2s infinite;
}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.15}}
.header-center{
  font-size:9px;letter-spacing:4px;color:rgba(0,212,255,.3);
  text-transform:uppercase;
}
.header-right{
  display:flex;gap:20px;
  font-size:9px;color:rgba(0,212,255,.45);letter-spacing:2px;
}

/* ── SHARED PANEL ── */
.panel{
  border:1px solid rgba(0,212,255,.15);
  background:rgba(0,12,20,.82);
  padding:10px;
  position:relative;
  overflow:hidden;
}
.panel::before{
  content:'';
  position:absolute;top:0;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(0,212,255,.35),transparent);
}
.panel-lbl{
  font-size:7px;letter-spacing:3px;color:rgba(0,212,255,.35);
  text-transform:uppercase;
  margin-bottom:8px;
  padding-bottom:4px;
  border-bottom:1px solid rgba(0,212,255,.07);
}

/* ── LEFT COLUMN ── */
.left-col{display:flex;flex-direction:column;gap:4px;overflow:hidden}

/* Objective */
.goal-val{
  color:#00ff88;font-size:16px;font-weight:bold;letter-spacing:1px;
  text-transform:uppercase;
  text-shadow:0 0 14px rgba(0,255,136,.55);
  margin:2px 0 8px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.info-row{display:flex;justify-content:space-between;margin:3px 0;font-size:9px}
.ik{color:rgba(0,212,255,.35)}
.iv{color:#00d4ff;font-weight:bold;font-variant-numeric:tabular-nums}

/* Voice */
.voice-row{display:flex;align-items:center;gap:10px;margin-bottom:6px}
.vring{
  width:28px;height:28px;border-radius:50%;
  border:2px solid rgba(0,212,255,.15);
  display:flex;align-items:center;justify-content:center;flex-shrink:0;
}
.vring.on{border-color:#00d4ff;box-shadow:0 0 14px rgba(0,212,255,.5);animation:vpulse 2s infinite}
.vring.ptt{border-color:rgba(255,170,0,.6);box-shadow:0 0 10px rgba(255,170,0,.3)}
@keyframes vpulse{0%,100%{box-shadow:0 0 6px rgba(0,212,255,.3)}50%{box-shadow:0 0 20px rgba(0,212,255,.8)}}
.vdot{width:10px;height:10px;border-radius:50%;background:rgba(0,212,255,.2)}
.vring.on .vdot{background:#00d4ff;box-shadow:0 0 8px #00d4ff}
.vring.ptt .vdot{background:#ffaa00;box-shadow:0 0 8px #ffaa00}
.vtxt{font-size:10px;letter-spacing:2px;color:rgba(0,212,255,.4)}
.vtxt.on{color:#00d4ff}
.vtxt.ptt{color:#ffaa00}
.vtx-sub{
  font-size:9px;color:rgba(0,212,255,.3);
  min-height:14px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  border-left:2px solid rgba(0,212,255,.15);
  padding-left:6px;
}

/* Detections */
.det-item{display:flex;align-items:center;gap:5px;margin:4px 0}
.det-name{font-size:9px;color:rgba(0,212,255,.65);width:72px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex-shrink:0}
.det-bg{flex:1;height:3px;background:rgba(0,212,255,.07);border-radius:2px}
.det-bar{height:100%;border-radius:2px;background:linear-gradient(90deg,#003a6e,#00d4ff);transition:width .5s}
.det-pct{font-size:9px;color:#00d4ff;width:26px;text-align:right;flex-shrink:0}
.no-det{color:rgba(0,212,255,.15);font-size:10px;text-align:center;padding:12px 0}

/* ── CENTER — MIND VIEW ── */
.mind-wrap{
  border:1px solid rgba(0,212,255,.2);
  background:rgba(0,6,12,.9);
  padding:14px 18px;
  overflow:hidden;
  display:flex;flex-direction:column;
  position:relative;
}
.mind-wrap::before{
  content:'';
  position:absolute;top:0;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(0,212,255,.5),transparent);
}

/* fade mask at top so old thoughts fade out */
.mind-wrap::after{
  content:'';
  position:absolute;top:36px;left:0;right:0;height:60px;
  background:linear-gradient(to bottom,rgba(0,6,12,.9),transparent);
  pointer-events:none;z-index:2;
}

.mind-header{
  display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-shrink:0;z-index:3;
}
.mind-label{font-size:8px;letter-spacing:4px;color:rgba(0,212,255,.35);text-transform:uppercase}
.mind-pulse{
  width:6px;height:6px;border-radius:50%;
  background:#00d4ff;box-shadow:0 0 8px #00d4ff;
  animation:mpulse 1.5s ease-in-out infinite;
  flex-shrink:0;
}
@keyframes mpulse{0%,100%{opacity:.3;transform:scale(.8)}50%{opacity:1;transform:scale(1.2)}}

.thought-list{
  flex:1;
  overflow:hidden;
  display:flex;
  flex-direction:column;
  justify-content:flex-end;
  gap:10px;
  padding-top:20px;
}

/* Each thought bubble */
.ti{
  display:flex;gap:10px;align-items:flex-start;
  animation:thought-in .5s cubic-bezier(.16,1,.3,1);
  flex-shrink:0;
}
@keyframes thought-in{
  from{opacity:0;transform:translateY(12px)}
  to{opacity:1;transform:none}
}
.ti-marker{
  width:2px;flex-shrink:0;margin-top:4px;
  border-radius:1px;
  align-self:stretch;
  background:rgba(0,212,255,.12);
}
.ti:first-child .ti-marker{background:#00d4ff;box-shadow:0 0 6px rgba(0,212,255,.5)}
.ti:nth-child(2) .ti-marker{background:rgba(0,212,255,.4)}
.ti-body{flex:1;min-width:0}
.ti-time{
  font-size:7px;letter-spacing:2px;color:rgba(0,212,255,.25);
  margin-bottom:3px;
}
.ti-text{
  font-size:12px;line-height:1.6;
  color:rgba(0,212,255,.3);
  word-break:break-word;white-space:pre-wrap;
}
.ti:first-child .ti-text{
  color:rgba(0,212,255,.88);
  font-size:13px;
  text-shadow:0 0 30px rgba(0,212,255,.2);
}
.ti:nth-child(2) .ti-text{color:rgba(0,212,255,.55)}
.ti:nth-child(3) .ti-text{color:rgba(0,212,255,.38)}

/* ── RIGHT COLUMN — VISION ── */
.right-col{display:flex;flex-direction:column;gap:4px;overflow:hidden}

.camera-wrap{
  position:relative;
  border:1px solid rgba(0,212,255,.18);
  background:#000;overflow:hidden;
  flex-shrink:0;
  height:130px;
}
.camera-wrap img{width:100%;height:100%;object-fit:cover;display:block;opacity:.9}
.cam-label{
  position:absolute;top:5px;left:7px;
  font-size:7px;letter-spacing:3px;color:rgba(0,212,255,.4);
  z-index:10;
}
/* corner brackets */
.br{position:absolute;width:10px;height:10px;border-color:#00d4ff;border-style:solid;border-width:0;z-index:10;opacity:.6}
.br-tl{top:3px;left:3px;border-top-width:1px;border-left-width:1px}
.br-tr{top:3px;right:3px;border-top-width:1px;border-right-width:1px}
.br-bl{bottom:3px;left:3px;border-bottom-width:1px;border-left-width:1px}
.br-br{bottom:3px;right:3px;border-bottom-width:1px;border-right-width:1px}

/* reward bar */
.reward-bar-wrap{display:flex;align-items:center;gap:6px;margin:4px 0}
.reward-track{flex:1;height:4px;background:rgba(0,212,255,.07);border-radius:2px}
.reward-fill{height:100%;border-radius:2px;background:linear-gradient(90deg,#004d00,#00ff88);transition:width .6s}

/* ── NEURAL CANVAS ── */
#neuralCanvas{
  display:block;
  flex:1;
  width:100%;
  min-height:0;
}

/* Thought overlay under canvas */
#thoughtOverlay{
  flex-shrink:0;
  padding:8px 0 2px;
  border-top:1px solid rgba(0,212,255,.08);
  display:flex;flex-direction:column;gap:4px;
}
.ov-thought{
  font-size:11px;line-height:1.5;
  color:rgba(0,212,255,.85);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  padding-left:8px;
  border-left:2px solid rgba(0,212,255,.4);
}
.ov-thought+.ov-thought{
  color:rgba(0,212,255,.45);
  border-left-color:rgba(0,212,255,.12);
  font-size:10px;
}
</style>
</head>
<body>
<div class="scan"></div>
<div class="hud">

  <!-- HEADER -->
  <header class="header">
    <div class="logo">
      <div class="logo-dot"></div>
      AKSUMAEL
    </div>
    <div class="header-center">COGNITIVE INTERFACE</div>
    <div class="header-right">
      <span>TICK&nbsp;<span id="hTick">0</span></span>
      <span>UP&nbsp;<span id="hUp">0s</span></span>
      <span id="hMode">INIT</span>
    </div>
  </header>

  <!-- LEFT COLUMN -->
  <aside class="left-col">

    <!-- Objective -->
    <div class="panel">
      <div class="panel-lbl">OBJECTIVE</div>
      <div class="goal-val" id="sGoal">IDLE</div>
      <div class="info-row"><span class="ik">MODE</span><span class="iv" id="sMode">—</span></div>
      <div class="info-row"><span class="ik">TICK</span><span class="iv" id="sTick">0</span></div>
      <div class="panel-lbl" style="margin-top:8px">REWARD</div>
      <div class="reward-bar-wrap">
        <div class="reward-track"><div class="reward-fill" id="rFill" style="width:0%"></div></div>
        <span class="iv" id="rVal" style="font-size:9px;width:32px;text-align:right">—</span>
      </div>
    </div>

    <!-- Voice -->
    <div class="panel">
      <div class="panel-lbl">VOICE</div>
      <div class="voice-row">
        <div class="vring" id="vRing"><div class="vdot"></div></div>
        <span class="vtxt" id="vLbl">STANDBY</span>
      </div>
      <div class="vtx-sub" id="vTx"></div>
    </div>

    <!-- Detections -->
    <div class="panel" style="flex:1;overflow:hidden">
      <div class="panel-lbl">DETECTIONS</div>
      <div id="dList"><div class="no-det">NO OBJECTS</div></div>
    </div>

  </aside>

  <!-- CENTER — NEURAL MIND -->
  <main class="mind-wrap">
    <div class="mind-header">
      <div class="mind-pulse"></div>
      <span class="mind-label">Neural State</span>
    </div>
    <canvas id="neuralCanvas"></canvas>
    <div id="thoughtOverlay"></div>
  </main>

  <!-- RIGHT COLUMN — VISION + STATUS -->
  <aside class="right-col">

    <!-- Camera thumbnail -->
    <div class="camera-wrap">
      <div class="br br-tl"></div><div class="br br-tr"></div>
      <div class="br br-bl"></div><div class="br br-br"></div>
      <span class="cam-label">VISION</span>
      <img src="/stream" alt="feed"/>
    </div>

    <!-- System -->
    <div class="panel">
      <div class="panel-lbl">SYSTEM</div>
      <div class="info-row"><span class="ik">UPTIME</span><span class="iv" id="rUp">—</span></div>
      <div class="info-row"><span class="ik">TICK</span><span class="iv" id="rTick">0</span></div>
    </div>

  </aside>

</div>
<script>
// ── Utility ─────────────────────────────────────────────────────────────────
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function fmtUp(s){const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),ss=s%60;return h>0?`${h}h${String(m).padStart(2,'0')}m`:`${m}m${String(ss).padStart(2,'0')}s`;}
function fmtTime(ts){return new Date(ts*1000).toLocaleTimeString('en-US',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'});}

// ── Neural Network ───────────────────────────────────────────────────────────
const canvas = document.getElementById('neuralCanvas');
const ctx = canvas.getContext('2d');

// State driven by poll
let netState = {activity: 0.5, color: [0,212,255], goal: 'idle'};

// Build node graph
const NODE_COUNT = 52;
const CONNECT_DIST = 0.22;  // fraction of canvas width
let nodes = [], edges = [], pulses = [];

function buildGraph(W, H){
  nodes = [];
  // Scatter nodes in organic clusters
  const clusters = [{x:.2,y:.35},{x:.5,y:.2},{x:.8,y:.35},{x:.35,y:.65},{x:.65,y:.65},{x:.5,y:.5}];
  for(let i=0;i<NODE_COUNT;i++){
    const c = clusters[i % clusters.length];
    nodes.push({
      x: (c.x + (Math.random()-.5)*.32) * W,
      y: (c.y + (Math.random()-.5)*.32) * H,
      r: 2.5 + Math.random()*3,
      glow: Math.random(),        // base glow 0-1
      phase: Math.random()*Math.PI*2,
      speed: 0.4 + Math.random()*0.8,
      active: 0,                  // 0-1 activation level
    });
  }
  // Build edges
  edges = [];
  for(let i=0;i<nodes.length;i++){
    for(let j=i+1;j<nodes.length;j++){
      const dx=(nodes[i].x-nodes[j].x)/W, dy=(nodes[i].y-nodes[j].y)/H;
      const d=Math.sqrt(dx*dx+dy*dy);
      if(d < CONNECT_DIST) edges.push({a:i,b:j,d});
    }
  }
}

function spawnPulse(){
  if(edges.length===0) return;
  const e = edges[Math.floor(Math.random()*edges.length)];
  const rev = Math.random()<.5;
  pulses.push({
    edge: e,
    t: 0,          // 0→1 along edge
    speed: 0.008 + Math.random()*0.014,
    dir: rev ? -1 : 1,
    alpha: 0.6 + Math.random()*0.4,
  });
}

function drawFrame(ts){
  const W=canvas.width, H=canvas.height;
  ctx.clearRect(0,0,W,H);

  const [cr,cg,cb] = netState.color;
  const act = netState.activity;      // 0-1 overall activity

  // Spawn pulses proportional to activity
  if(Math.random() < 0.05 + act*0.25) spawnPulse();

  // Move pulses
  pulses = pulses.filter(p=>{
    p.t += p.speed * p.dir;
    const done = p.t>1.0 || p.t<0.0;
    if(!done){
      // activate destination node
      const ni = p.dir>0 ? p.edge.b : p.edge.a;
      nodes[ni].active = Math.min(1, nodes[ni].active + 0.3);
    }
    return !done;
  });

  // Decay node activation
  nodes.forEach(n => { n.active = Math.max(0, n.active - 0.018); });

  // Draw edges
  edges.forEach(e=>{
    const a=nodes[e.a], b=nodes[e.b];
    const baseAlpha = 0.04 + (1-e.d/CONNECT_DIST)*0.07;
    ctx.beginPath();
    ctx.moveTo(a.x,a.y);
    ctx.lineTo(b.x,b.y);
    ctx.strokeStyle=`rgba(${cr},${cg},${cb},${baseAlpha})`;
    ctx.lineWidth=0.6;
    ctx.stroke();
  });

  // Draw pulses
  pulses.forEach(p=>{
    const a=nodes[p.edge.a], b=nodes[p.edge.b];
    const t = p.dir>0 ? p.t : 1-p.t;
    const px=a.x+(b.x-a.x)*t, py=a.y+(b.y-a.y)*t;
    // Trail
    for(let i=0;i<6;i++){
      const tt=Math.max(0,t-i*0.035);
      const tx2=a.x+(b.x-a.x)*tt, ty2=a.y+(b.y-a.y)*tt;
      ctx.beginPath();
      ctx.arc(tx2,ty2,1.5-i*0.2,0,Math.PI*2);
      ctx.fillStyle=`rgba(${cr},${cg},${cb},${(p.alpha*(1-i/6)*0.6).toFixed(3)})`;
      ctx.fill();
    }
    // Head
    ctx.beginPath();
    ctx.arc(px,py,2,0,Math.PI*2);
    ctx.fillStyle=`rgba(${cr},${cg},${cb},${p.alpha})`;
    ctx.fill();
    // Glow
    const g=ctx.createRadialGradient(px,py,0,px,py,8);
    g.addColorStop(0,`rgba(${cr},${cg},${cb},0.3)`);
    g.addColorStop(1,'transparent');
    ctx.beginPath();ctx.arc(px,py,8,0,Math.PI*2);
    ctx.fillStyle=g;ctx.fill();
  });

  // Draw nodes
  const t=ts*0.001;
  nodes.forEach(n=>{
    const pulse = Math.sin(t*n.speed+n.phase)*0.5+0.5;
    const brightness = n.active*0.7 + pulse*(0.15+act*0.15);
    const r = n.r + n.active*3 + pulse*1;
    // Outer glow
    if(brightness>0.05){
      const g=ctx.createRadialGradient(n.x,n.y,0,n.x,n.y,r*5);
      g.addColorStop(0,`rgba(${cr},${cg},${cb},${(brightness*0.35).toFixed(3)})`);
      g.addColorStop(1,'transparent');
      ctx.beginPath();ctx.arc(n.x,n.y,r*5,0,Math.PI*2);
      ctx.fillStyle=g;ctx.fill();
    }
    // Core
    ctx.beginPath();ctx.arc(n.x,n.y,r,0,Math.PI*2);
    ctx.fillStyle=`rgba(${cr},${cg},${cb},${Math.min(1,0.15+brightness*0.85).toFixed(3)})`;
    ctx.fill();
  });

  requestAnimationFrame(drawFrame);
}

// Resize handler
function resize(){
  const mw = document.querySelector('.mind-wrap');
  const hdr = document.querySelector('.mind-header');
  const ov = document.getElementById('thoughtOverlay');
  const W = mw.clientWidth - 28;
  const H = mw.clientHeight - hdr.offsetHeight - ov.offsetHeight - 28;
  canvas.width = W;
  canvas.height = Math.max(H, 100);
  buildGraph(canvas.width, canvas.height);
}
window.addEventListener('resize', resize);
setTimeout(resize, 100);
requestAnimationFrame(drawFrame);

// ── Goal → color mapping ─────────────────────────────────────────────────────
const GOAL_COLORS = {
  explore:       [0,212,255],
  mine_diamonds: [100,180,255],
  find_food:     [0,255,136],
  return_to_base:[255,200,0],
  craft:         [200,100,255],
  combat:        [255,80,80],
  idle:          [0,212,255],
};

// ── Poll ─────────────────────────────────────────────────────────────────────
let _lastThoughtSig = '';

async function poll(){
  try{
    const d=await fetch('/status').then(r=>r.json());

    // header
    document.getElementById('hTick').textContent=d.tick||0;
    document.getElementById('hUp').textContent=fmtUp(d.uptime||0);
    const mode=(d.mode||'init').toUpperCase();
    document.getElementById('hMode').textContent=mode;

    // left — objective
    const goal=(d.goal||'idle');
    document.getElementById('sGoal').textContent=goal.replace(/_/g,' ').toUpperCase();
    document.getElementById('sMode').textContent=mode;
    document.getElementById('sTick').textContent=d.tick||0;

    // reward
    const rw=d.last_reward!=null?d.last_reward:(d.reward!=null?d.reward:null);
    if(rw!=null){
      const pct=Math.max(0,Math.min(100,rw*100));
      document.getElementById('rFill').style.width=pct+'%';
      document.getElementById('rVal').textContent=rw.toFixed(3);
      netState.activity = Math.max(0.2, Math.min(1, 0.4 + rw));
    }

    // update neural network color from goal
    const col = GOAL_COLORS[goal] || GOAL_COLORS.explore;
    netState.color = col;
    netState.goal = goal;

    // voice
    const vm=d.voice_mode||'on';
    const ring=document.getElementById('vRing');
    const lbl=document.getElementById('vLbl');
    ring.className='vring '+(vm==='on'?'on':vm==='ptt'?'ptt':'');
    lbl.className='vtxt '+(vm==='on'?'on':vm==='ptt'?'ptt':'');
    lbl.textContent=vm==='on'?'LISTENING':vm==='ptt'?'PTT  F9':'MUTED';
    const tx=document.getElementById('vTx');
    tx.textContent=d.voice_text?'» '+d.voice_text:'';

    // detections
    const objs=d.objects||[];
    const dEl=document.getElementById('dList');
    dEl.innerHTML=!objs.length?'<div class="no-det">NO OBJECTS</div>':objs.map(o=>`
      <div class="det-item">
        <span class="det-name">${esc(o.label)}</span>
        <div class="det-bg"><div class="det-bar" style="width:${Math.round(o.conf*100)}%"></div></div>
        <span class="det-pct">${Math.round(o.conf*100)}%</span>
      </div>`).join('');

    // right system
    document.getElementById('rUp').textContent=fmtUp(d.uptime||0);
    document.getElementById('rTick').textContent=d.tick||0;

    // thought overlay (latest 3 thoughts below the canvas)
    const thoughts=d.thoughts||[];
    const sig=thoughts.map(t=>t.text).join('|');
    if(sig!==_lastThoughtSig){
      _lastThoughtSig=sig;
      const ov=document.getElementById('thoughtOverlay');
      ov.innerHTML=thoughts.slice(0,3).map((t,i)=>`
        <div class="ov-thought" style="opacity:${1-i*0.28}">${esc(t.text)}</div>`).join('');
      setTimeout(resize,50);
    }

  } catch(e){}
}

poll();
setInterval(poll,900);
</script>
</body>
</html>"""
