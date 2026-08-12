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
<title>AKSUMAEL // INTERFACE</title>
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
  background-image:radial-gradient(circle at 1px 1px,rgba(0,212,255,.05) 1px,transparent 0);
  background-size:28px 28px;
  pointer-events:none;z-index:0;
}

/* scan-line sweep */
.scan{
  position:fixed;top:0;left:0;right:0;height:2px;
  background:linear-gradient(90deg,transparent,rgba(0,212,255,.15),rgba(0,212,255,.4),rgba(0,212,255,.15),transparent);
  animation:scan 5s linear infinite;
  pointer-events:none;z-index:999;
}
@keyframes scan{0%{transform:translateY(-2px)}100%{transform:translateY(100vh)}}

/* scanline texture */
body::before{
  content:'';
  position:fixed;top:0;left:0;right:0;bottom:0;
  background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,.04) 2px,rgba(0,0,0,.04) 4px);
  pointer-events:none;z-index:998;
}

.hud{
  position:relative;z-index:1;
  display:grid;
  grid-template-columns:1fr 260px;
  grid-template-rows:36px 1fr 100px;
  height:100vh;
  gap:4px;padding:4px;
}

/* ── HEADER ── */
.header{
  grid-column:1/-1;
  display:flex;align-items:center;justify-content:space-between;
  border-bottom:1px solid rgba(0,212,255,.25);
  padding:0 10px;
}
.logo{
  display:flex;align-items:center;gap:8px;
  font-size:15px;font-weight:bold;letter-spacing:5px;
  color:#00d4ff;text-shadow:0 0 20px rgba(0,212,255,.7);
}
.logo-dot{
  width:8px;height:8px;border-radius:50%;
  background:#00ff88;box-shadow:0 0 10px #00ff88;
  animation:blink 2s infinite;
}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.2}}

.header-right{
  display:flex;gap:24px;
  font-size:9px;color:rgba(0,212,255,.5);letter-spacing:2px;
}

/* ── CAMERA PANEL ── */
.camera-wrap{
  position:relative;
  border:1px solid rgba(0,212,255,.2);
  background:#000;overflow:hidden;
}
.camera-wrap img{width:100%;height:100%;object-fit:contain;display:block}
.cam-label{
  position:absolute;top:6px;left:8px;
  font-size:8px;letter-spacing:3px;color:rgba(0,212,255,.45);
  z-index:10;
}
/* corner brackets */
.br{position:absolute;width:14px;height:14px;border-color:#00d4ff;border-style:solid;border-width:0;z-index:10;opacity:.7}
.br-tl{top:3px;left:3px;border-top-width:2px;border-left-width:2px}
.br-tr{top:3px;right:3px;border-top-width:2px;border-right-width:2px}
.br-bl{bottom:3px;left:3px;border-bottom-width:2px;border-left-width:2px}
.br-br{bottom:3px;right:3px;border-bottom-width:2px;border-right-width:2px}

/* ── SIDEBAR ── */
.sidebar{display:flex;flex-direction:column;gap:4px;overflow:hidden}

.panel{
  border:1px solid rgba(0,212,255,.18);
  background:rgba(0,18,28,.8);
  padding:8px;
  position:relative;
  overflow:hidden;
}
.panel::before{
  content:'';
  position:absolute;top:0;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(0,212,255,.4),transparent);
}
.panel-lbl{
  font-size:7px;letter-spacing:3px;color:rgba(0,212,255,.4);
  text-transform:uppercase;
  margin-bottom:6px;
  padding-bottom:3px;
  border-bottom:1px solid rgba(0,212,255,.08);
}

/* Objective */
.goal-val{
  color:#00ff88;font-size:13px;font-weight:bold;letter-spacing:1px;
  text-transform:uppercase;
  text-shadow:0 0 12px rgba(0,255,136,.5);
  margin:4px 0 6px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.info-row{display:flex;justify-content:space-between;margin:2px 0;font-size:9px}
.ik{color:rgba(0,212,255,.4)}
.iv{color:#00d4ff;font-weight:bold;font-variant-numeric:tabular-nums}

/* Voice */
.voice-row{display:flex;align-items:center;gap:10px}
.vring{
  width:26px;height:26px;border-radius:50%;
  border:2px solid rgba(0,212,255,.2);
  display:flex;align-items:center;justify-content:center;
  flex-shrink:0;
}
.vring.on{border-color:#00d4ff;box-shadow:0 0 14px rgba(0,212,255,.6);animation:vpulse 1.8s infinite}
.vring.ptt{border-color:rgba(255,170,0,.5)}
@keyframes vpulse{0%,100%{box-shadow:0 0 8px rgba(0,212,255,.4)}50%{box-shadow:0 0 22px rgba(0,212,255,.9)}}
.vdot{width:9px;height:9px;border-radius:50%;background:rgba(0,212,255,.3)}
.vring.on .vdot{background:#00d4ff;box-shadow:0 0 8px #00d4ff}
.vtxt{font-size:10px;letter-spacing:2px;color:rgba(0,212,255,.5)}
.vtxt.on{color:#00d4ff}
.vtxt.ptt{color:#ffaa00}
.vtx-sub{font-size:9px;color:rgba(0,212,255,.3);margin-top:5px;min-height:13px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* Detections */
.det-item{display:flex;align-items:center;gap:5px;margin:3px 0}
.det-name{font-size:9px;color:rgba(0,212,255,.7);width:80px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex-shrink:0}
.det-bg{flex:1;height:3px;background:rgba(0,212,255,.08);border-radius:2px}
.det-bar{height:100%;border-radius:2px;background:linear-gradient(90deg,#0055aa,#00d4ff);transition:width .4s}
.det-pct{font-size:9px;color:#00d4ff;width:28px;text-align:right;flex-shrink:0}
.no-det{color:rgba(0,212,255,.18);font-size:10px;text-align:center;padding:10px 0}

/* ── THOUGHT STREAM ── */
.thought-wrap{
  grid-column:1;
  border:1px solid rgba(0,212,255,.15);
  background:rgba(0,8,12,.85);
  padding:6px 10px;
  overflow:hidden;
  display:flex;flex-direction:column;
}
.thought-lbl{font-size:7px;letter-spacing:3px;color:rgba(0,212,255,.35);margin-bottom:4px;flex-shrink:0}
.thought-list{
  flex:1;overflow:hidden;
  display:flex;flex-direction:column-reverse;
  gap:2px;
}
.ti{
  font-size:10px;line-height:1.45;
  color:rgba(0,212,255,.35);
  padding:1px 0 1px 7px;
  border-left:1px solid rgba(0,212,255,.08);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  animation:fadein .4s ease;
}
.ti:first-child{color:rgba(0,212,255,.85);border-left-color:rgba(0,212,255,.5)}
.ti:nth-child(2){color:rgba(0,212,255,.55)}
@keyframes fadein{from{opacity:0;transform:translateY(-3px)}to{opacity:1;transform:none}}
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
    <div class="header-right">
      <span>TICK&nbsp;<span id="hTick">0</span></span>
      <span>UP&nbsp;<span id="hUp">0s</span></span>
      <span id="hMode">TRAINING</span>
    </div>
  </header>

  <!-- CAMERA -->
  <div class="camera-wrap">
    <div class="br br-tl"></div><div class="br br-tr"></div>
    <div class="br br-bl"></div><div class="br br-br"></div>
    <span class="cam-label">VISION&nbsp;FEED</span>
    <img src="/stream" alt="feed"/>
  </div>

  <!-- SIDEBAR -->
  <aside class="sidebar">

    <!-- Objective -->
    <div class="panel">
      <div class="panel-lbl">OBJECTIVE</div>
      <div class="goal-val" id="sGoal">IDLE</div>
      <div class="info-row"><span class="ik">MODE</span><span class="iv" id="sMode">—</span></div>
      <div class="info-row"><span class="ik">TICK</span><span class="iv" id="sTick">0</span></div>
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

  <!-- THOUGHT STREAM -->
  <div class="thought-wrap">
    <div class="thought-lbl">MONOLOGUE</div>
    <div class="thought-list" id="tList">
      <div class="ti">System online. Awaiting input.</div>
    </div>
  </div>

</div>
<script>
let _lastThoughtSig = '';

function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

function fmtUp(s){
  const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),ss=s%60;
  return h>0?`${h}h${String(m).padStart(2,'0')}m`:`${m}m${String(ss).padStart(2,'0')}s`;
}

async function poll(){
  try{
    const d=await fetch('/status').then(r=>r.json());

    // header
    document.getElementById('hTick').textContent=d.tick||0;
    document.getElementById('hUp').textContent=fmtUp(d.uptime||0);
    const mode=(d.mode||'training').toUpperCase();
    document.getElementById('hMode').textContent=mode;

    // objective
    const goal=(d.goal||'idle').replace(/_/g,' ').toUpperCase();
    document.getElementById('sGoal').textContent=goal;
    document.getElementById('sMode').textContent=mode;
    document.getElementById('sTick').textContent=d.tick||0;

    // voice
    const vm=d.voice_mode||'on';
    const ring=document.getElementById('vRing');
    const lbl=document.getElementById('vLbl');
    ring.className='vring '+(vm==='on'?'on':vm==='ptt'?'ptt':'');
    lbl.className='vtxt '+(vm==='on'?'on':vm==='ptt'?'ptt':'');
    lbl.textContent=vm==='on'?'LISTENING':vm==='ptt'?'PTT (F9)':'MUTED';
    const tx=document.getElementById('vTx');
    tx.textContent=d.voice_text?'> '+d.voice_text:'';

    // detections
    const objs=d.objects||[];
    const dEl=document.getElementById('dList');
    if(!objs.length){
      dEl.innerHTML='<div class="no-det">NO OBJECTS</div>';
    } else {
      dEl.innerHTML=objs.map(o=>`
        <div class="det-item">
          <span class="det-name">${esc(o.label)}</span>
          <div class="det-bg"><div class="det-bar" style="width:${Math.round(o.conf*100)}%"></div></div>
          <span class="det-pct">${Math.round(o.conf*100)}%</span>
        </div>`).join('');
    }

    // thoughts
    const thoughts=d.thoughts||[];
    const sig=thoughts.map(t=>t.text).join('|');
    if(sig!==_lastThoughtSig){
      _lastThoughtSig=sig;
      const tEl=document.getElementById('tList');
      tEl.innerHTML=thoughts.length
        ? thoughts.map(t=>`<div class="ti">${esc(t.text)}</div>`).join('')
        : '<div class="ti" style="color:rgba(0,212,255,.15)">Awaiting thoughts...</div>';
    }

  } catch(e){}
}

poll();
setInterval(poll,900);
</script>
</body>
</html>"""
