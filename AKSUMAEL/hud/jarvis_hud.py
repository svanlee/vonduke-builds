"""
hud.jarvis_hud — Jarvis HUD rendering

Extracted from core/capture.py. Render the full Jarvis HUD onto a window.
Call render_hud(pipeline, window_name, frame, objs) from the main thread.

'pipeline' is the VideoCapturePipeline instance — its class-level attributes
(_JARVIS_START, _JARVIS_FRAME, _WINDOW_CREATED, _CONVO_LOG, etc.) are
accessed and mutated here.
"""
import math as _math
import time
import random as _rand
import subprocess as _sp2
import cv2
import numpy as _np

def render_hud(pipeline, window_name: str, frame, objs):
    """Render a Jarvis-style HUD — camera feed as the main display,
    cognitive state / detections in a right sidebar, thinking pulse in header.

    Layout:
      ┌─────────── header: AKSUMAEL | TICK | MODE | UP | [THINK PULSE] ──┐
      │  CAMERA FEED (big, with YOLO boxes)    │  COGNITIVE STATE         │
      │                                        │  > thought lines ...     │
      │                                        ├──────────────────────────│
      │                                        │  DETECTIONS              │
      │                                        │  ore  91% ████           │
      ├────────────────────────────────────────┴──────────────────────────│
      │  VOICE ◉  [transcript]          OBJECTIVE: mine_diamonds           │
      └───────────────────────────────────────────────────────────────────┘
      ├──────────────────────────────────────┬──────────────────┤
      │  COGNITIVE STATE / INNER MONOLOGUE   │  ┌─ PiP cam ─┐  │
      │  (large scrolling thought stream)    │  │  camera   │  │
      │                                      │  └───────────┘  │
      │  > thought line 1                    │  DETECTIONS      │
      │  > thought line 2                    │  ore  91% ████  │
      │  > ...                               │  player 87% ██  │
      │                                      │                  │
      │  VOICE ◉  transcript text here       │  OBJECTIVE       │
      └──────────────────────────────────────┴──────────────────┘
    """
    # Import module-level helpers from capture — safe because this function is
    # only called after core.capture is fully loaded (lazy import in _jarvis_imshow).
    from core.capture import _CV2_GUI_OK, _monologue_render_lines

    if pipeline.__class__._JARVIS_START is None:
        pipeline.__class__._JARVIS_START = time.time()
    pipeline.__class__._JARVIS_FRAME += 1
    fnum = pipeline.__class__._JARVIS_FRAME

    # ── Colours (BGR) ──────────────────────────────────────────────
    BG     = (10,   6,   2)       # near-black navy
    CYAN   = (255, 212,  0)       # #00d4ff — BGR for cyan
    CYAN2  = (180, 150,  0)       # mid cyan
    DCYAN  = (55,   40,  0)       # dim cyan
    VCYAN  = (25,   18,  0)       # very dim
    PANEL  = (18,  12,  3)        # sidebar bg
    GREEN  = (60,  230, 40)       # health green
    DGREEN = (15,   60, 10)       # dim green (bar bg)
    ORANGE = (10,  140, 255)      # hunger / voice
    DORANGE= (5,   40,  80)       # dim orange
    WHITE  = (190, 195, 185)      # text
    RED    = (40,   30, 220)      # warn
    FONT   = cv2.FONT_HERSHEY_SIMPLEX

    # ── Glow helpers ───────────────────────────────────────────────
    def _gline(img, p1, p2, col, dim, thick=1):
        """Line with glow halo (dim wide stroke + bright thin stroke)."""
        cv2.line(img, p1, p2, dim, thick + 4, cv2.LINE_AA)
        cv2.line(img, p1, p2, col, thick,     cv2.LINE_AA)

    def _gcircle(img, ctr, r, col, dim, thick=1):
        cv2.circle(img, ctr, r + 3, dim, thick + 4, cv2.LINE_AA)
        cv2.circle(img, ctr, r,     col, thick,     cv2.LINE_AA)

    def _garc(img, ctr, r, start_deg, end_deg, col, dim, thick=2):
        cv2.ellipse(img, ctr, (r, r), 0, start_deg, end_deg, dim, thick + 4, cv2.LINE_AA)
        cv2.ellipse(img, ctr, (r, r), 0, start_deg, end_deg, col, thick,     cv2.LINE_AA)

    def _gtext(img, txt, pos, scale, col, dim, thick=1):
        cv2.putText(img, txt, pos, FONT, scale, dim, thick + 2, cv2.LINE_AA)
        cv2.putText(img, txt, pos, FONT, scale, col, thick,     cv2.LINE_AA)

    def _corner_bracket(img, x, y, dx, dy, size, col, dim):
        """Draw L-shaped corner bracket with glow. dx/dy = ±1 direction."""
        ex, ey = x + dx * size, y + dy * size
        _gline(img, (x, y), (ex, y), col, dim, 2)
        _gline(img, (x, y), (x, ey), col, dim, 2)
        # small tick at tip
        cv2.line(img, (ex - dx * 4, y - 1), (ex - dx * 4, y + 1), col, 1)

    def _arc_gauge(img, cx, cy, r, pct, col_full, col_dim, col_bg,
                   start_deg=135, sweep=270):
        """270° arc gauge: start_deg → start_deg+sweep filled by pct."""
        # Background arc
        cv2.ellipse(img, (cx, cy), (r, r), 0, start_deg,
                    start_deg + sweep, col_bg, 4, cv2.LINE_AA)
        # Filled portion
        fill_end = start_deg + sweep * max(0.0, min(1.0, pct))
        if fill_end > start_deg + 1:
            _garc(img, (cx, cy), r, start_deg, fill_end, col_full, col_dim, 3)
        # Tick marks around ring
        for i in range(11):
            a_deg = start_deg + sweep * i / 10
            a_rad = _math.radians(a_deg)
            r1 = r + 5; r2 = r + (9 if i % 5 == 0 else 7)
            px1 = int(cx + r1 * _math.cos(a_rad))
            py1 = int(cy + r1 * _math.sin(a_rad))
            px2 = int(cx + r2 * _math.cos(a_rad))
            py2 = int(cy + r2 * _math.sin(a_rad))
            tcol = col_full if i % 5 == 0 else col_dim
            cv2.line(img, (px1, py1), (px2, py2), tcol, 1, cv2.LINE_AA)

    def _radar_sweep(img, cx, cy, r, angle_deg, col, dim):
        """Radar sweep wedge + leading line."""
        a1 = _math.radians(angle_deg)
        a2 = _math.radians(angle_deg - 40)
        # Dim wedge fill
        pts = []
        pts.append([cx, cy])
        for a in range(int(angle_deg) - 40, int(angle_deg) + 1, 2):
            ar = _math.radians(a)
            pts.append([int(cx + r * _math.cos(ar)),
                         int(cy + r * _math.sin(ar))])
        if len(pts) > 2:
            import numpy as _np2
            pts_arr = _np2.array(pts, dtype=_np2.int32)
            overlay = img.copy()
            cv2.fillPoly(overlay, [pts_arr], dim)
            cv2.addWeighted(overlay, 0.35, img, 0.65, 0, img)
        # Leading sweep line
        lx = int(cx + r * _math.cos(a1))
        ly = int(cy + r * _math.sin(a1))
        _gline(img, (cx, cy), (lx, ly), col, (col[0]//3, col[1]//3, col[2]//3), 1)
        # Outer ring
        cv2.ellipse(img, (cx, cy), (r, r), 0, 0, 360, dim, 1, cv2.LINE_AA)
        # Cross hairs
        cv2.line(img, (cx - r, cy), (cx + r, cy), (col[0]//8, col[1]//8, col[2]//8), 1)
        cv2.line(img, (cx, cy - r), (cx, cy + r), (col[0]//8, col[1]//8, col[2]//8), 1)

    # ── Canvas dimensions ──────────────────────────────────────────
    WIN_W  = 1280
    WIN_H  = 720
    HDR_H  = 48
    FOOT_H = 36
    SIDE_W = 300
    CAM_W  = WIN_W - SIDE_W
    CAM_H  = WIN_H - HDR_H - FOOT_H

    canvas = _np.zeros((WIN_H, WIN_W, 3), dtype=_np.uint8)
    canvas[:] = BG

    # ── HUD state ─────────────────────────────────────────────────
    goal       = 'idle'
    mode       = 'live'
    tick       = 0
    voice_text = ''
    thinking   = False
    hp_pct     = 1.0
    food_pct   = 1.0
    try:
        from core import frame_server as _fs
        with _fs._hud_lock:
            goal       = _fs._hud_state.get('goal', 'idle') or 'idle'
            mode       = _fs._hud_state.get('mode', 'live') or 'live'
            tick       = _fs._hud_state.get('tick', 0)
            voice_text = _fs._hud_state.get('voice_text', '')
            thinking   = bool(_fs._hud_state.get('thinking', False))
            hp_pct     = float(_fs._hud_state.get('health_pct', 100)) / 100.0
            food_pct   = float(_fs._hud_state.get('hunger_pct', 100)) / 100.0
    except Exception:
        pass

    uptime_s   = int(time.time() - pipeline.__class__._JARVIS_START)
    uptime_str = f'{uptime_s // 3600:02d}:{(uptime_s % 3600) // 60:02d}:{uptime_s % 60:02d}'

    # ── Sysstat cache refresh (used by both mini panels and sidebar) ──
    _now_ss = time.time()
    if _now_ss >= pipeline.__class__._SYSSTAT_NEXT:
        pipeline.__class__._SYSSTAT_NEXT = _now_ss + 2.0
        try:
            import psutil as _ps
            pipeline.__class__._SYSSTAT['cpu'] = _ps.cpu_percent(interval=None) / 100.0
            pipeline.__class__._SYSSTAT['ram'] = _ps.virtual_memory().percent / 100.0
        except Exception:
            pass
        try:
            import subprocess as _sp
            _smi = _sp.run(
                ['nvidia-smi','--query-gpu=utilization.gpu,memory.used,memory.total',
                 '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=1)
            _vals = _smi.stdout.strip().split(',')
            pipeline.__class__._SYSSTAT['gpu']     = float(_vals[0].strip()) / 100.0
            pipeline.__class__._SYSSTAT['gpu_mem'] = float(_vals[1].strip()) / float(_vals[2].strip())
        except Exception:
            pass
    cpu_pct  = pipeline.__class__._SYSSTAT['cpu']
    gpu_util = pipeline.__class__._SYSSTAT['gpu']
    gpu_mem  = pipeline.__class__._SYSSTAT['gpu_mem']
    ram_pct  = pipeline.__class__._SYSSTAT['ram']
    thoughts   = _monologue_render_lines()

    # ── Webcam porthole refresh (laptop built-in, 8 fps) ─────────
    _wc_now = time.time()
    if _wc_now >= pipeline.__class__._WEBCAM_NEXT:
        pipeline.__class__._WEBCAM_NEXT = _wc_now + 0.125  # ~8 fps
        try:
            if pipeline.__class__._WEBCAM is None:
                _wc = cv2.VideoCapture(0)   # /dev/video0 — built-in webcam
                if not _wc.isOpened():
                    _wc = cv2.VideoCapture(1)  # fallback to video1
                pipeline.__class__._WEBCAM = _wc if _wc.isOpened() else False
            if pipeline.__class__._WEBCAM:
                _ok, _wf = pipeline.__class__._WEBCAM.read()
                if _ok:
                    pipeline.__class__._WEBCAM_FRAME = _wf
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════
    # HEADER
    # ══════════════════════════════════════════════════════════════
    # Header — no fill, elements float on black
    # Thin arc sweep instead of a full-width solid bar
    cv2.ellipse(canvas, (WIN_W // 2, 0), (WIN_W // 2, HDR_H + 6),
                0, 0, 180, (DCYAN[0]//2, DCYAN[1]//2, DCYAN[2]//2), 1, cv2.LINE_AA)

    # Logo with glow
    _gtext(canvas, 'A K S U M A E L', (12, 32), 0.72, CYAN, DCYAN, 2)

    # Info
    info = f'TICK {tick:07d}   {mode.upper():<8s}   UP {uptime_str}'
    cv2.putText(canvas, info, (280, 32), FONT, 0.38, CYAN2, 1, cv2.LINE_AA)

    # ── Thinking ring cluster (right of header) ────────────────────
    rc_x = WIN_W - 55
    rc_y = HDR_H // 2 + 2

    if thinking:
        ang = (fnum * 5) % 360
        ang2 = (fnum * 8 + 120) % 360
        # Outer spinning arc
        _garc(canvas, (rc_x, rc_y), 20, ang, ang + 200, CYAN, DCYAN, 2)
        # Inner counter-spinning arc
        _garc(canvas, (rc_x, rc_y), 13, -ang2, -ang2 + 130, CYAN2,
              (DCYAN[0]//2, DCYAN[1]//2, DCYAN[2]//2), 1)
        # Core dot
        _gcircle(canvas, (rc_x, rc_y), 4, CYAN, DCYAN, -1)
        _gtext(canvas, 'PROCESSING', (WIN_W - 180, 32), 0.32, CYAN, DCYAN)
    else:
        # Idle: dim pulsing ring
        phase = abs(_math.sin(fnum * 0.04))
        bc = int(30 + 25 * phase)
        ic = (bc, bc // 2, 0)
        cv2.circle(canvas, (rc_x, rc_y), 20, ic, 1, cv2.LINE_AA)
        cv2.circle(canvas, (rc_x, rc_y), 13, ic, 1, cv2.LINE_AA)
        cv2.circle(canvas, (rc_x, rc_y), 4,
                   (bc // 2, bc // 2, 0), -1, cv2.LINE_AA)
        cv2.putText(canvas, 'STANDBY', (WIN_W - 140, 32),
                    FONT, 0.30, DCYAN, 1, cv2.LINE_AA)

    # Circuit trace deco: short dashes after logo
    for xi in range(220, 260, 8):
        cv2.rectangle(canvas, (xi, 18), (xi + 4, 20), VCYAN, -1)

    # ══════════════════════════════════════════════════════════════
    # NEURAL MIND — 3D sphere + arc reactor (depth-sorted)
    # ══════════════════════════════════════════════════════════════
    nn_x0, nn_y0 = 0, HDR_H
    nn_w, nn_h   = CAM_W, CAM_H
    ncx = nn_x0 + nn_w // 2
    ncy = nn_y0 + nn_h // 2

    # Dark background
    cv2.rectangle(canvas, (nn_x0, nn_y0), (nn_x0+nn_w, nn_y0+nn_h), (2, 6, 10), -1)

    # Dot-grid background
    for gy2 in range(nn_y0+14, nn_y0+nn_h, 28):
        for gx2 in range(nn_x0+14, nn_x0+nn_w, 28):
            cv2.circle(canvas, (gx2, gy2), 1, (0, 20, 30), -1)

    # ── Build 3D fibonacci-sphere node graph once (180 nodes) ────────
    _NN_TARGET = 180
    if pipeline.__class__._NN_NODES is None or len(pipeline.__class__._NN_NODES) != _NN_TARGET:
        pipeline.__class__._NN_ADJ = None  # force adjacency rebuild
        _phi3d = _math.pi * (3. - _math.sqrt(5.))
        import random as _rng_init
        _rng = _rng_init.Random(0xACE)   # fixed seed → stable layout, asymmetric
        nn_nodes3 = []
        for i in range(_NN_TARGET):
            _y3d = 1. - (i / (_NN_TARGET-1.)) * 2.
            _r3d = _math.sqrt(max(0., 1. - _y3d*_y3d))
            _th3d = _phi3d * i
            # Small position jitter breaks the perfect Fibonacci lattice
            _jx = _rng.uniform(-0.045, 0.045)
            _jy = _rng.uniform(-0.035, 0.035)
            _jz = _rng.uniform(-0.045, 0.045)
            nn_nodes3.append({
                'x3': _r3d * _math.cos(_th3d) + _jx,
                'y3': _y3d + _jy,
                'z3': _r3d * _math.sin(_th3d) + _jz,
                # Random phase — not linear, so no two nodes breathe in sync
                'phase': _rng.uniform(0., _math.pi * 2.),
                'spd':   _rng.uniform(0.15, 0.95),
                'rb':    2 + (i % 3),
                # Per-node unique oscillation frequencies and amplitudes
                'df': (_rng.uniform(0.7, 2.1), _rng.uniform(0.3, 1.4)),
                'da': (_rng.uniform(0.03, 0.10), _rng.uniform(0.02, 0.07)),
            })
        pipeline.__class__._NN_NODES = nn_nodes3
        nn_edges3 = []
        for i in range(len(nn_nodes3)):
            for j in range(i+1, len(nn_nodes3)):
                _a3, _b3 = nn_nodes3[i], nn_nodes3[j]
                _dx3=_a3['x3']-_b3['x3']; _dy3=_a3['y3']-_b3['y3']; _dz3=_a3['z3']-_b3['z3']
                _d3 = _math.sqrt(_dx3*_dx3+_dy3*_dy3+_dz3*_dz3)
                if _d3 < 0.48:
                    nn_edges3.append({'a':i,'b':j,'d':_d3,'tier':'short'})
                elif _d3 < 0.85:
                    nn_edges3.append({'a':i,'b':j,'d':_d3,'tier':'long'})
        pipeline.__class__._NN_EDGES = nn_edges3

    nn_nodes = pipeline.__class__._NN_NODES
    nn_edges = pipeline.__class__._NN_EDGES

    # Gold palette — BGR: all warm amber/orange/gold regardless of goal
    # (goal tints the heat highlight color only)
    _GC_TINT = {
        # Jarvis states
        'standby':   ( 0, 185, 255),  # amber gold
        'listen':    (20, 220, 200),  # green-gold (active listening)
        'think':     ( 0, 155, 255),  # deep orange (processing)
        'assist':    ( 0, 210, 255),  # bright gold (responding)
        'alert':     ( 0,  80, 255),  # red-orange (urgent)
        'idle':      ( 0, 185, 255),  # amber gold
        # Legacy Minecraft labels kept for backward compatibility during transition
        'explore':        ( 0, 210, 255),
        'mine_diamonds':  ( 0, 155, 255),
        'find_food':      (20, 220, 200),
        'return_to_base': ( 0, 210, 255),
        'craft':          ( 0, 185, 255),
        'combat':         ( 0, 110, 255),
    }
    gc  = _GC_TINT.get(goal, (0, 185, 255))  # default amber gold (BGR)
    gcd = (gc[0]//6, gc[1]//6, gc[2]//6)
    # Secondary fire color for hottest nodes
    gc_hot = (80, 240, 255)  # near-white gold

    # ── 3D → 2D perspective projection ───────────────────────────
    _rot_y = (fnum * 0.006) % (_math.pi*2)
    _rot_xa = _math.sin(fnum * 0.003) * 0.25
    _cy3, _sy3 = _math.cos(_rot_y), _math.sin(_rot_y)
    _cx3, _sx3 = _math.cos(_rot_xa), _math.sin(_rot_xa)
    _sscale = min(nn_w, nn_h) * 0.36
    _fov    = 3.5

    def _proj3(x3, y3, z3):
        xr = x3*_cy3 + z3*_sy3
        yr = y3
        zr = -x3*_sy3 + z3*_cy3
        yr2 = yr*_cx3 - zr*_sx3
        zr2 = yr*_sx3 + zr*_cx3
        s = _sscale * _fov / (_fov + zr2)
        return int(ncx + xr*s), int(ncy + yr2*s), zr2

    # ── Holosphere state (gold, armillary, dense) ─────────────────
    heat = pipeline.__class__._NN_HEAT

    # Organic deformation: each node breathes outward/inward along its own
    # normal vector. Heat pulls nodes inward — hot nodes cluster near the
    # core (inner shell r≈0.45), cold nodes sit on the outer shell (r≈1.0).
    # Three natural layers emerge: core (active), mid (warming), outer (idle).
    _t_slow = fnum * 0.008
    def _deformed(n, idx):
        _phase_n = n['phase']           # fully random per node, not idx-linear
        _fa, _fb = n['df']              # unique frequencies per node
        _aa, _ab = n['da']              # unique amplitudes per node
        _bulge = _aa * _math.sin(_t_slow * _fa + _phase_n) \
               + _ab * _math.cos(_t_slow * _fb + _phase_n * 1.3)
        _h = heat.get(idx, 0.0)
        _layer_r = 1.0 - _h * 0.55
        _r = _layer_r + _bulge
        px, py, pz = _proj3(n['x3'] * _r, n['y3'] * _r, n['z3'] * _r)
        # Brain-ellipse: wider than tall, slowly drifting asymmetry
        _drift_x = _math.sin(fnum * 0.0009) * 9
        _drift_y = _math.cos(fnum * 0.0007) * 6
        px2 = int(ncx + (px - ncx) * 1.42 + _drift_x)
        py2 = int(ncy + (py - ncy) * 0.68 + _drift_y)
        return px2, py2, pz

    pnodes = [_deformed(n, i) for i, n in enumerate(nn_nodes)]

    if pipeline.__class__._NN_ADJ is None:
        adj = {i: [] for i in range(len(nn_nodes))}
        for _e in nn_edges:
            adj[_e['a']].append(_e['b'])
            adj[_e['b']].append(_e['a'])
        pipeline.__class__._NN_ADJ = adj
    adj = pipeline.__class__._NN_ADJ

    # When speaking: fire nodes much more aggressively (living, reactive look)
    try:
        import core.voice as _vm2
        _spk = getattr(_vm2, 'JARVIS_SPEAKING', False)
    except Exception:
        _spk = False
    try:
        import core.llm_router as _lr_b
        _thinking = getattr(_lr_b, 'LLM_THINKING', False)
    except Exception:
        _thinking = False
    _brain_active = _spk or _thinking
    # Shift node color to cyan when Jarvis is thinking or speaking
    if _brain_active:
        gc     = CYAN        # (255, 212, 0) BGR
        gc_hot = (255, 255, 120)  # bright cyan-white
        gcd    = (gc[0]//6, gc[1]//6, gc[2]//6)
    _fire_rate = 3 if _brain_active else 15
    if fnum % _fire_rate == 0:
        heat[_rand.randint(0, len(nn_nodes)-1)] = 1.0
        if _brain_active:  # fire multiple nodes when thinking/speaking
            heat[_rand.randint(0, len(nn_nodes)-1)] = 0.8
            heat[_rand.randint(0, len(nn_nodes)-1)] = 0.6

    _new_heat = {}
    for i in range(len(nn_nodes)):
        h = heat.get(i, 0.0)
        if h > 0.72 and _rand.random() < 0.10:
            for nb in adj.get(i, []):
                if heat.get(nb, 0.0) < 0.2:
                    _new_heat[nb] = min(1.0, heat.get(nb, 0.0) + 0.65)
        _new_heat[i] = max(0.0, min(1.0, _new_heat.get(i, h) - 0.015))
    pipeline.__class__._NN_HEAT = _new_heat
    heat = _new_heat

    breath = 0.88 + 0.12 * _math.sin(fnum * 0.020)

    def _hcol_gold(h):
        if h < 0.5:
            f = h * 2.0
            return (int(gc[0]*(0.08+0.92*f)), int(gc[1]*(0.08+0.92*f)), int(gc[2]*(0.08+0.92*f)))
        else:
            f = (h-0.5)*2.0
            return (min(255,int(gc[0]+(gc_hot[0]-gc[0])*f)),
                    min(255,int(gc[1]+(gc_hot[1]-gc[1])*f)),
                    min(255,int(gc[2]+(gc_hot[2]-gc[2])*f)))

    _glow = _np.zeros((nn_h, nn_w, 3), dtype=_np.float32)
    _lcx, _lcy = ncx - nn_x0, ncy - nn_y0

    # Ambient halo (very subtle — just a faint sphere edge, no fill)
    cv2.circle(_glow, (_lcx,_lcy), int(255*breath),
               (gc[0]/255.*0.03, gc[1]/255.*0.03, gc[2]/255.*0.03), 3)

    # Separate short/long edge tiers
    _short_edges = [e for e in nn_edges if e.get('tier','short')=='short']
    _long_edges  = [e for e in nn_edges if e.get('tier','long')=='long']

    # Faint starfield background — tiny dots scattered inside the sphere
    _rng_seed = 42
    for _si in range(80):
        _sx = int(nn_x0 + nn_w * (0.5 + 0.45 * _math.sin(_si * 17.3 + _rng_seed)))
        _sy = int(nn_y0 + nn_h * (0.5 + 0.45 * _math.cos(_si * 9.7 + _rng_seed)))
        _star_r = 1 if _si % 3 != 0 else 2
        _star_br = 25 + (_si % 5) * 8  # 25-57 brightness
        cv2.circle(canvas, (_sx, _sy), _star_r,
                   (int(gc[0]*_star_br//255), int(gc[1]*_star_br//255)+_star_br//3, int(gc[2]*_star_br//255)+_star_br//2),
                   -1, cv2.LINE_AA)

    # Long-range connections — ultra-faint constellation threads
    for e2 in _long_edges:
        ax2,ay2,az2=pnodes[e2['a']]; bx2,by2,bz2=pnodes[e2['b']]
        if not(nn_x0<=ax2<nn_x0+nn_w and nn_y0<=ay2<nn_y0+nn_h): continue
        if not(nn_x0<=bx2<nn_x0+nn_w and nn_y0<=by2<nn_y0+nn_h): continue
        eh=max(heat.get(e2['a'],0.),heat.get(e2['b'],0.))
        if eh < 0.25: continue  # only show when hot
        dim=int(max(3, eh*15))
        cv2.line(canvas,(ax2,ay2),(bx2,by2),(0,dim//3,dim),1,cv2.LINE_AA)

    # Short connections — dim constellation lines, universe style
    for e2 in sorted(_short_edges, key=lambda e:(pnodes[e['a']][2]+pnodes[e['b']][2])/2):
        ax2,ay2,az2=pnodes[e2['a']]; bx2,by2,bz2=pnodes[e2['b']]
        if not(nn_x0<=ax2<nn_x0+nn_w and nn_y0<=ay2<nn_y0+nn_h): continue
        if not(nn_x0<=bx2<nn_x0+nn_w and nn_y0<=by2<nn_y0+nn_h): continue
        df=max(0.,0.5+(az2+bz2)*0.25)
        eh=max(heat.get(e2['a'],0.),heat.get(e2['b'],0.))
        raw=_hcol_gold(max(eh,df*0.25))
        scale = 0.07 + eh*0.18   # very dim — constellation lines
        ec=(int(raw[0]*scale),int(raw[1]*scale),int(raw[2]*scale))
        cv2.line(canvas,(ax2,ay2),(bx2,by2),ec,1,cv2.LINE_AA)
        lx1,ly1=ax2-nn_x0,ay2-nn_y0
        lx2v,ly2v=bx2-nn_x0,by2-nn_y0
        egf=df*0.02+eh*0.06   # minimal glow on edges
        if egf>0.04:
            cv2.line(_glow,(lx1,ly1),(lx2v,ly2v),
                     (gc[0]/255.*egf,gc[1]/255.*egf,gc[2]/255.*egf),1,cv2.LINE_AA)

    # Armillary rings disabled — they looked like hard bars on the brain shape
    _arms = []
    for (inc_b, spin_r, rbr, rth) in _arms:
        inc = inc_b + fnum * spin_r
        _ci, _si = _math.cos(inc), _math.sin(inc)
        prev_px, prev_py, prev_pz = None, None, None
        for _tdeg in range(0, 362, 2):  # 2° steps = smooth
            _t = _math.radians(_tdeg)
            _rx = _math.cos(_t)
            _ry = _math.sin(_t)*_ci
            _rz = _math.sin(_t)*_si
            _px,_py,_pz = _proj3(_rx,_ry,_rz)
            if nn_x0<=_px<nn_x0+nn_w and nn_y0<=_py<nn_y0+nn_h:
                if prev_px is not None and _pz > -0.2:
                    vf = max(0., 0.4+_pz*0.6)
                    rc_b=min(255,int(gc[0]*rbr*vf))
                    rc_g=min(255,int(gc[1]*rbr*vf))
                    rc_r=min(255,int(gc[2]*rbr*vf))
                    cv2.line(canvas,(prev_px,prev_py),(_px,_py),
                             (rc_b,rc_g,rc_r),rth,cv2.LINE_AA)
                    lrx1,lry1=prev_px-nn_x0,prev_py-nn_y0
                    lrx2,lry2=_px-nn_x0,_py-nn_y0
                    gfr=rbr*0.65*max(0.,vf)  # strong glow on rings
                    cv2.line(_glow,(lrx1,lry1),(lrx2,lry2),
                             (gc[0]/255.*gfr,gc[1]/255.*gfr,gc[2]/255.*gfr),3,cv2.LINE_AA)
                prev_px,prev_py,prev_pz=_px,_py,_pz
            else:
                prev_px=None

    # Synaptic pulses
    if fnum%2==0 and _short_edges and len(pipeline.__class__._NN_PULSES)<50:
        hot_e=[e for e in _short_edges if heat.get(e['a'],0.)>0.4 or heat.get(e['b'],0.)>0.4]
        src=hot_e if hot_e else _short_edges
        _pe=src[_rand.randint(0,len(src)-1)]
        pipeline.__class__._NN_PULSES.append(
            {'a':_pe['a'],'b':_pe['b'],'t':0.,'spd':0.06+_rand.random()*0.08,'dir':1})
    _alive2=[]
    for p2 in pipeline.__class__._NN_PULSES:
        p2['t']+=p2['spd']
        if p2['t']<=1.0:
            pax2,pay2,paz2=pnodes[p2['a']]; pbx2,pby2,pbz2=pnodes[p2['b']]
            ppx=int(pax2+(pbx2-pax2)*p2['t']); ppy=int(pay2+(pby2-pay2)*p2['t'])
            pdp=paz2+(pbz2-paz2)*p2['t']; pr=max(2,int(5*(0.5+pdp*0.5)))
            if nn_x0<=ppx<nn_x0+nn_w and nn_y0<=ppy<nn_y0+nn_h:
                pc=_hcol_gold(0.9+pdp*0.1)
                cv2.circle(canvas,(ppx,ppy),pr+5,gcd,-1,cv2.LINE_AA)
                cv2.circle(canvas,(ppx,ppy),pr,pc,-1,cv2.LINE_AA)
                lx3,ly3=ppx-nn_x0,ppy-nn_y0
                cv2.circle(_glow,(lx3,ly3),pr+12,
                           (gc[0]/255.*1.2,gc[1]/255.*1.2,gc[2]/255.*1.2),-1)
                if p2['t']>0.88:
                    heat[p2['b']]=min(1.0,heat.get(p2['b'],0.)+0.5)
            _alive2.append(p2)
    pipeline.__class__._NN_PULSES=_alive2

    # Nodes — stars: varied sizes, brighter hot/front nodes
    for ni in sorted(range(len(nn_nodes)), key=lambda i: pnodes[i][2]):
        n2=nn_nodes[ni]; nx2,ny2,nz2=pnodes[ni]
        if not(nn_x0<=nx2<nn_x0+nn_w and nn_y0<=ny2<nn_y0+nn_h): continue
        df2=max(0.,0.5+nz2*0.5)
        nh=heat.get(ni,0.)
        bp=_math.sin(fnum*0.035*n2['spd']+n2['phase'])*0.5+0.5
        eff_h=max(nh,df2*0.25+bp*0.08)
        # Star size: most 1px, front nodes 2-3px, hot nodes up to 5px
        _is_bright_star = (ni % 7 == 0) and df2 > 0.6   # ~1 in 7 front nodes = big star
        if _is_bright_star:
            nr2 = max(2, int((2 + nh*4) * (0.7 + df2*0.3)))  # 2-5px
        else:
            nr2 = max(1, int((1 + nh*2.5) * (0.35 + df2*0.55)))  # 1-3px
        nc=_hcol_gold(eff_h)
        # Diffuse outer glow for bright stars
        if _is_bright_star:
            cv2.circle(canvas,(nx2,ny2),nr2+5,(nc[0]//6,nc[1]//6,nc[2]//6),-1,cv2.LINE_AA)
            cv2.circle(canvas,(nx2,ny2),nr2+2,(nc[0]//3,nc[1]//3,nc[2]//3),-1,cv2.LINE_AA)
        elif nh>0.5:
            cv2.circle(canvas,(nx2,ny2),nr2+3,(nc[0]//6,nc[1]//6,nc[2]//6),-1,cv2.LINE_AA)
        cv2.circle(canvas,(nx2,ny2),nr2,nc,-1,cv2.LINE_AA)
        # Glow for hot/bright/front nodes
        if nh>0.2 or df2>0.55 or _is_bright_star:
            lx4,ly4=nx2-nn_x0,ny2-nn_y0
            gf3=(df2*0.18+nh*0.60)*breath * (1.8 if _is_bright_star else 1.0)
            cv2.circle(_glow,(lx4,ly4),nr2+10,
                       (gc[0]/255.*gf3,gc[1]/255.*gf3,gc[2]/255.*gf3),-1)

    # Sun core — translucent outer ring (fixed), solid inner orb pulses to voice
    if _brain_active:
        _glow_r = 72  # fixed translucent boundary — solid never goes outside this
        # Translucent outer ring: brighter when speaking vs only thinking
        _ring_alpha = 0.30 if _spk else 0.14
        _ring_alpha2 = 0.55 if _spk else 0.28
        cv2.circle(_glow, (_lcx, _lcy), _glow_r,
                   (gc[0]/255.*_ring_alpha, gc[1]/255.*_ring_alpha, gc[2]/255.*_ring_alpha), -1)
        cv2.circle(_glow, (_lcx, _lcy), _glow_r - 16,
                   (gc[0]/255.*_ring_alpha2, gc[1]/255.*_ring_alpha2, gc[2]/255.*_ring_alpha2), -1)
        # Inner orb: voice-amplitude-synced when speaking, slow breathe when only thinking
        if _spk:
            try:
                import time as _tm_orb
                _env_v = getattr(_vm2, 'JARVIS_VOICE_ENVELOPE', [])
                _vst_v = getattr(_vm2, 'JARVIS_VOICE_START_TIME', 0.0)
                _vidx = int((_tm_orb.monotonic() - _vst_v) * 30)
                _voice_amp = _env_v[_vidx] if _env_v and 0 <= _vidx < len(_env_v) else 0.5
            except Exception:
                _voice_amp = 0.5
            # Pulse 35%–80% of _glow_r in sync with speech amplitude
            core_r = int(_glow_r * (0.35 + 0.45 * _voice_amp))
        else:
            # Thinking only: small slow breathe, no drama
            _think_t = abs(_math.sin(fnum * 0.08))
            core_r = int(_glow_r * (0.28 + 0.14 * _think_t))
        _inner_r = max(4, core_r // 2)
        for gr in [_inner_r, _inner_r - 4, _inner_r - 8, 4, 2]:
            if gr < 1: continue
            ga4 = min(255, int(210 + (_inner_r - gr) * 8))
            cv2.circle(canvas, (ncx, ncy), gr,
                       (min(255, gc_hot[0]*ga4//220),
                        min(255, gc_hot[1]*ga4//220),
                        min(255, gc_hot[2]*ga4//220)), -1, cv2.LINE_AA)
    else:
        core_r = int(nn_h * 0.045 * breath)  # scale to brain area size
        cv2.circle(_glow, (_lcx, _lcy), core_r + 20,
                   (gc[0]/255.*0.30, gc[1]/255.*0.30, gc[2]/255.*0.30), -1)
        for gr in [core_r, core_r-7, core_r-14, core_r-19, core_r-23, 3]:
            if gr < 1: continue
            ga4 = min(255, int(155 + (core_r - gr) * 5))
            cv2.circle(canvas, (ncx, ncy), gr,
                       (min(255, gc_hot[0]*ga4//220),
                        min(255, gc_hot[1]*ga4//220),
                        min(255, gc_hot[2]*ga4//220)), -1, cv2.LINE_AA)

    # Voice-reactive bloom: use the real voice amplitude envelope when speaking
    _bloom_base = 90.   # idle: gentle background glow only
    if _spk:
        try:
            import time as _tm_bloom
            _env_b = getattr(_vm2, 'JARVIS_VOICE_ENVELOPE', [])
            _vst_b = getattr(_vm2, 'JARVIS_VOICE_START_TIME', 0.0)
            _bidx = int((_tm_bloom.monotonic() - _vst_b) * 30)
            _bamp = _env_b[_bidx] if _env_b and 0 <= _bidx < len(_env_b) else 0.5
        except Exception:
            _bamp = 0.5
        _bloom_base = 180. + 170. * _bamp  # 180–350 tracking voice
    elif _thinking:
        _bloom_base = 140.  # dim steady glow during thinking

    # Bloom — lower multiplier keeps background visible
    _glow_blur=cv2.GaussianBlur(_glow,(0,0),14)
    _region=canvas[nn_y0:nn_y0+nn_h,nn_x0:nn_x0+nn_w].astype(_np.float32)
    _region=_np.clip(_region+_glow_blur*_bloom_base,0,255)
    canvas[nn_y0:nn_y0+nn_h,nn_x0:nn_x0+nn_w]=_region.astype(_np.uint8)

    # ── Corner brackets ───────────────────────────────────────────
    BLEN=30
    _corner_bracket(canvas,nn_x0,       nn_y0,        1, 1,BLEN,CYAN,DCYAN)
    _corner_bracket(canvas,nn_x0+nn_w,  nn_y0,       -1, 1,BLEN,CYAN,DCYAN)
    _corner_bracket(canvas,nn_x0,        nn_y0+nn_h,   1,-1,BLEN,CYAN,DCYAN)
    _corner_bracket(canvas,nn_x0+nn_w,   nn_y0+nn_h,  -1,-1,BLEN,CYAN,DCYAN)

    # Chamfer lines removed per user request

    # ── HUD structural frame: tick rulers + dividers ───────────────
    # Tick ruler along bottom of header
    for _ti in range(0, WIN_W, 10):
        _th = 5 if _ti % 100 == 0 else (3 if _ti % 50 == 0 else 2)
        _tc = DCYAN if _ti % 50 == 0 else VCYAN
        cv2.line(canvas, (_ti, HDR_H - _th), (_ti, HDR_H), _tc, 1)
    # Tick ruler along top of footer
    for _ti in range(0, WIN_W, 10):
        _th = 5 if _ti % 100 == 0 else (3 if _ti % 50 == 0 else 2)
        _tc = DCYAN if _ti % 50 == 0 else VCYAN
        cv2.line(canvas, (_ti, WIN_H - FOOT_H), (_ti, WIN_H - FOOT_H + _th), _tc, 1)
    # Left border
    cv2.line(canvas, (0, HDR_H), (0, WIN_H - FOOT_H),
             (DCYAN[0]//3, DCYAN[1]//3, DCYAN[2]//3), 1)
    # Main area bottom edge line
    cv2.line(canvas, (0, WIN_H - FOOT_H), (CAM_W, WIN_H - FOOT_H),
             (DCYAN[0]//2, DCYAN[1]//2, DCYAN[2]//2), 1)
    # Vertical divider: main area | sidebar
    cv2.line(canvas, (CAM_W, HDR_H), (CAM_W, WIN_H - FOOT_H), DCYAN, 1, cv2.LINE_AA)
    # Small tick marks crossing the divider
    for _ty in range(HDR_H + 24, WIN_H - FOOT_H, 32):
        _dk = DCYAN if (_ty // 32) % 3 == 0 else VCYAN
        cv2.line(canvas, (CAM_W - 5, _ty), (CAM_W + 5, _ty), _dk, 1)

    # Labels
    cv2.putText(canvas,'JARVIS',(nn_x0+8,nn_y0+15),FONT,0.33,CYAN2,1,cv2.LINE_AA)
    cv2.putText(canvas,f'{goal.upper().replace("_"," ")}',
                (nn_x0+8,nn_y0+30),FONT,0.30,gc,1,cv2.LINE_AA)

    # ══════════════════════════════════════════════════════════════
    # EXPANDABLE MINI PANELS
    # Four panels sit in the corners of the sphere area. Click any
    # mini panel to expand it to a variable-scale overlay.  Scroll
    # wheel adjusts the expansion fraction (0.3-0.96). Click
    # anywhere outside a panel's expanded view to collapse it.
    # Only one panel can be expanded at a time.
    # ══════════════════════════════════════════════════════════════

    # ── Animate panel scales ──────────────────────────────────────
    LERP = 0.18
    for _p in pipeline.__class__._PANELS.values():
        _p['scale'] += (_p['target'] - _p['scale']) * LERP
        if _p['scale'] < 0.003:
            _p['scale'] = 0.0

    # ── Set mini_rect positions (top-left, bottom-left, bottom-mid, top-right)
    _PM = pipeline.__class__._PANELS
    _PM['sysstat']['mini_rect'] = (nn_x0 + 8, nn_y0 + 8,               170, 100)
    _PM['goals'  ]['mini_rect'] = (nn_x0 + 8, nn_y0 + nn_h - 85 - 8,  200, 80 )
    _PM['thought']['mini_rect'] = (nn_x0 + nn_w//2 - 120, nn_y0 + nn_h - 75 - 8, 240, 70)
    _PM['camera' ]['mini_rect'] = (nn_x0 + nn_w - 224 - 8, nn_y0 + 8, 224, 126)

    # ── Register mouse/scroll callback once ──────────────────────
    if not pipeline.__class__._MOUSE_CB_SET:
        def _on_mouse(event, mx, my, flags, param):
            panels = pipeline.__class__._PANELS
            # Scroll wheel — adjust exp_scale of the open panel
            if event == cv2.EVENT_MOUSEWHEEL:
                for _pp in panels.values():
                    if _pp['target'] > 0.05:
                        _delta = 0.05 if flags > 0 else -0.05
                        _pp['exp_scale'] = max(0.30, min(0.96, _pp['exp_scale'] + _delta))
                        _pp['target'] = _pp['exp_scale']
                return
            if event != cv2.EVENT_LBUTTONDOWN:
                return
            # Click anywhere in the COMM LINK strip (bottom ~130px of main area)
            _comm_y_top = 720 - 36 - 130   # = 554
            _comm_y_bot = 720 - 36          # = 684
            _comm_x_bot = 1280 - 300        # = 980 (left of sidebar)
            if 0 <= mx <= _comm_x_bot and _comm_y_top <= my <= _comm_y_bot:
                pipeline.__class__._TEXT_ACTIVE = True
                pipeline.__class__._TEXT_INPUT  = ''
                return
            # If any panel is expanded, a click collapses it
            _any_open = any(_pp['target'] > 0.05 for _pp in panels.values())
            if _any_open:
                for _pp in panels.values():
                    _pp['target'] = 0.0
                return
            # Click on a mini rect → expand it, collapse others
            for _pid, _pp in panels.items():
                rx, ry, rw, rh = _pp['mini_rect']
                if rx <= mx < rx + rw and ry <= my < ry + rh:
                    for _qp in panels.values():
                        _qp['target'] = 0.0
                    _pp['target'] = _pp['exp_scale']
                    return
        try:
            cv2.setMouseCallback(window_name, _on_mouse)
            pipeline.__class__._MOUSE_CB_SET = True
        except Exception:
            pass

    # ── Draw mini panels — fully organic, no rectangles ──────────
    # All elements use arcs, ellipses, and circles only.

    # ── Camera mini — circular porthole ──────────────────────────
    _cam_rx, _cam_ry, _cam_rw, _cam_rh = _PM['camera']['mini_rect']
    _cam_cx = _cam_rx + _cam_rw // 2
    _cam_cy = _cam_ry + _cam_rh // 2
    _cam_r  = min(_cam_rw, _cam_rh) // 2 - 6
    # Outer decoration rings
    cv2.circle(canvas, (_cam_cx, _cam_cy), _cam_r + 9,
               (DCYAN[0]//3, DCYAN[1]//3, DCYAN[2]//3), 1, cv2.LINE_AA)
    cv2.circle(canvas, (_cam_cx, _cam_cy), _cam_r + 5, DCYAN, 1, cv2.LINE_AA)
    # Tick marks
    for _ti in range(0, 360, 20):
        _ta = _math.radians(_ti)
        _is_maj = _ti % 60 == 0
        _tir = _cam_r + 6; _tor = _cam_r + (11 if _is_maj else 8)
        cv2.line(canvas,
                 (int(_cam_cx + _tir*_math.cos(_ta)), int(_cam_cy + _tir*_math.sin(_ta))),
                 (int(_cam_cx + _tor*_math.cos(_ta)), int(_cam_cy + _tor*_math.sin(_ta))),
                 CYAN if _is_maj else DCYAN, 1, cv2.LINE_AA)
    # Circular-masked webcam frame (laptop camera, not screen capture)
    _wc_frame = pipeline.__class__._WEBCAM_FRAME
    if _wc_frame is not None and _wc_frame.size > 0 and _cam_rw > 0 and _cam_rh > 0:
        _mf = cv2.resize(_wc_frame, (max(2,_cam_rw), max(2,_cam_rh)))
        _cmask = _np.zeros((_cam_rh, _cam_rw), dtype=_np.uint8)
        cv2.circle(_cmask, (_cam_rw // 2, _cam_rh // 2), max(1, _cam_r - 1), 255, -1)
        _croi = canvas[_cam_ry:_cam_ry+_cam_rh, _cam_rx:_cam_rx+_cam_rw]
        _np.copyto(_croi, _mf, where=(_cmask[..., _np.newaxis] > 0))
    else:
        cv2.circle(canvas, (_cam_cx, _cam_cy), max(1, _cam_r - 1), (4, 3, 2), -1)
    cv2.circle(canvas, (_cam_cx, _cam_cy), _cam_r, CYAN, 1, cv2.LINE_AA)
    cv2.putText(canvas, 'WEBCAM', (_cam_cx - 19, _cam_ry + _cam_rh + 10),
                FONT, 0.25, DCYAN, 1, cv2.LINE_AA)

    # ── Screen view porthole — what the AI sees ───────────────────
    _scr_rw = 180;  _scr_rh = 100
    _scr_rx = nn_x0 + nn_w - _scr_rw - 8
    _scr_ry = _cam_ry + _cam_rh + 22
    _scr_cx = _scr_rx + _scr_rw // 2
    _scr_cy = _scr_ry + _scr_rh // 2
    _scr_r  = min(_scr_rw, _scr_rh) // 2 - 4
    # Rings
    cv2.circle(canvas, (_scr_cx, _scr_cy), _scr_r + 5,
               (DCYAN[0]//3, DCYAN[1]//3, DCYAN[2]//3), 1, cv2.LINE_AA)
    cv2.circle(canvas, (_scr_cx, _scr_cy), _scr_r + 2, DCYAN, 1, cv2.LINE_AA)
    for _sti in range(0, 360, 30):
        _sta = _math.radians(_sti)
        cv2.line(canvas,
                 (int(_scr_cx + (_scr_r+3)*_math.cos(_sta)),
                  int(_scr_cy + (_scr_r+3)*_math.sin(_sta))),
                 (int(_scr_cx + (_scr_r+8)*_math.cos(_sta)),
                  int(_scr_cy + (_scr_r+8)*_math.sin(_sta))),
                 CYAN if _sti % 90 == 0 else DCYAN, 1, cv2.LINE_AA)
    # Screen frame inside circle
    if frame is not None and frame.size > 0 and _scr_rw > 0 and _scr_rh > 0:
        _sf = cv2.resize(frame, (max(2,_scr_rw), max(2,_scr_rh)))
        _smask = _np.zeros((_scr_rh, _scr_rw), dtype=_np.uint8)
        cv2.circle(_smask, (_scr_rw//2, _scr_rh//2), max(1, _scr_r-1), 255, -1)
        _sroi = canvas[_scr_ry:_scr_ry+_scr_rh, _scr_rx:_scr_rx+_scr_rw]
        _np.copyto(_sroi, _sf, where=(_smask[..., _np.newaxis] > 0))
    else:
        cv2.circle(canvas, (_scr_cx, _scr_cy), max(1, _scr_r-1), (4, 3, 2), -1)
    cv2.circle(canvas, (_scr_cx, _scr_cy), _scr_r, CYAN, 1, cv2.LINE_AA)
    cv2.putText(canvas, 'SCREEN', (_scr_cx - 17, _scr_ry + _scr_rh + 10),
                FONT, 0.25, DCYAN, 1, cv2.LINE_AA)

    # Sysstat mini removed — large gauges in sidebar replace it
    _PM['sysstat']['mini_rect'] = (nn_x0 + 16, nn_y0 + 12, 4, 4)  # zero-area, click disabled

    # ── Goals mini — arc node cluster ─────────────────────────────
    _gl_rx, _gl_ry, _gl_rw, _gl_rh = _PM['goals']['mini_rect']
    _gl_cx = _gl_rx + _gl_rw // 2;  _gl_cy = _gl_ry + 28
    # Arc bracket curving above goal text
    cv2.ellipse(canvas, (_gl_cx, _gl_cy), (_gl_rw // 2 - 6, 22),
                0, 195, 345, DCYAN, 1, cv2.LINE_AA)
    # Central node dot
    cv2.circle(canvas, (_gl_cx, _gl_cy), 5, CYAN, -1, cv2.LINE_AA)
    cv2.circle(canvas, (_gl_cx, _gl_cy), 8, DCYAN, 1, cv2.LINE_AA)
    _goal_disp = goal[:26].replace('_', ' ').upper()
    (_gtw2, _gth2), _ = cv2.getTextSize(_goal_disp, FONT, 0.27, 1)
    cv2.putText(canvas, _goal_disp, (_gl_cx - _gtw2 // 2, _gl_cy + 20),
                FONT, 0.27, CYAN, 1, cv2.LINE_AA)
    # Queued goals as satellite dots
    try:
        import json as _js
        with open('data/state.json') as _sf:
            _stk = _js.load(_sf).get('goal_stack', []) or []
        for _gi3, _gg3 in enumerate(_stk[:3]):
            _dot_x = _gl_rx + 14 + _gi3 * 56
            _dot_y = _gl_ry + _gl_rh - 10
            cv2.line(canvas, (_gl_cx, _gl_cy + 10), (_dot_x, _dot_y),
                     (DCYAN[0]//2, DCYAN[1]//2, DCYAN[2]//2), 1, cv2.LINE_AA)
            cv2.circle(canvas, (_dot_x, _dot_y), 3, DCYAN, -1, cv2.LINE_AA)
            _gtxt3 = str(_gg3)[:10].replace('_', ' ')
            cv2.putText(canvas, _gtxt3, (_dot_x - 14, _dot_y + 11),
                        FONT, 0.20, DCYAN, 1, cv2.LINE_AA)
    except Exception:
        pass

    # ── Thought mini — arc text strip ─────────────────────────────
    _th_rx, _th_ry, _th_rw, _th_rh = _PM['thought']['mini_rect']
    _th_cx = _th_rx + _th_rw // 2;  _th_cy = _th_ry + 8
    # Arc above the text
    cv2.ellipse(canvas, (_th_cx, _th_cy + 18), (_th_rw // 2 - 6, 16),
                0, 202, 338, DCYAN, 1, cv2.LINE_AA)
    cv2.circle(canvas, (_th_cx, _th_cy + 4), 3, CYAN, -1, cv2.LINE_AA)
    _th_last = (thoughts or ['...'])[-1][:38]
    (_ttw, _tth), _ = cv2.getTextSize(_th_last, FONT, 0.27, 1)
    cv2.putText(canvas, _th_last, (_th_cx - _ttw // 2, _th_cy + 38),
                FONT, 0.27, WHITE, 1, cv2.LINE_AA)
    if len(thoughts or []) > 1:
        _th_prev = (thoughts)[-2][:38]
        (_tp2w, _), _ = cv2.getTextSize(_th_prev, FONT, 0.22, 1)
        cv2.putText(canvas, _th_prev, (_th_cx - _tp2w // 2, _th_cy + 52),
                    FONT, 0.22, DCYAN, 1, cv2.LINE_AA)

    # ── Draw expanded panel overlay ───────────────────────────────
    for _eid, _ep in pipeline.__class__._PANELS.items():
        _es = _ep['scale']
        if _es < 0.02:
            continue
        _ew = int(WIN_W * _ep['exp_scale'] * 0.92)
        _eh = int(WIN_H * _ep['exp_scale'] * 0.92)
        _ex = (WIN_W - _ew) // 2
        _ey = (WIN_H - _eh) // 2
        # Dim everything behind the panel
        _overlay = canvas.copy()
        cv2.rectangle(_overlay, (0, 0), (WIN_W, WIN_H), (0, 0, 0), -1)
        cv2.addWeighted(_overlay, 0.55 * _es, canvas, 1.0, 0, canvas)
        # Panel background
        _pw = int(_ew * _es); _ph = int(_eh * _es)
        _px2 = (WIN_W - _pw) // 2; _py2 = (WIN_H - _ph) // 2
        cv2.rectangle(canvas, (_px2, _py2), (_px2+_pw, _py2+_ph), (14, 9, 2), -1)
        # Elliptical frame — no corner brackets
        _epx = _px2 + _pw // 2;  _epy = _py2 + _ph // 2
        cv2.ellipse(canvas, (_epx, _epy), (_pw // 2, _ph // 2),
                    0, 0, 360, DCYAN, 1, cv2.LINE_AA)
        cv2.ellipse(canvas, (_epx, _epy), (_pw // 2 + 3, _ph // 2 + 3),
                    0, 0, 360, (DCYAN[0]//2, DCYAN[1]//2, DCYAN[2]//2), 1, cv2.LINE_AA)
        cv2.ellipse(canvas, (_epx, _epy), (_pw // 2 - 2, _ph // 2 - 2),
                    0, 0, 360, CYAN, 1, cv2.LINE_AA)
        # Arc tick marks at cardinal points
        for _adeg in range(0, 360, 30):
            _ar = _math.radians(_adeg)
            _is_card = _adeg % 90 == 0
            _r1x = int(_epx + (_pw//2 + 4) * _math.cos(_ar))
            _r1y = int(_epy + (_ph//2 + 4) * _math.sin(_ar))
            _r2x = int(_epx + (_pw//2 + (10 if _is_card else 6)) * _math.cos(_ar))
            _r2y = int(_epy + (_ph//2 + (10 if _is_card else 6)) * _math.sin(_ar))
            cv2.line(canvas, (_r1x, _r1y), (_r2x, _r2y),
                     CYAN if _is_card else DCYAN, 1, cv2.LINE_AA)
        # Title bar
        _etitle = {'camera':'VISION', 'sysstat':'SYSTEM STATUS',
                   'goals':'GOAL STACK', 'thought':'INNER MONOLOGUE'}.get(_eid, _eid.upper())
        _gtext(canvas, _etitle, (_px2+12, _py2+20), 0.55, CYAN, DCYAN, 1)
        cv2.putText(canvas, '[click anywhere to close]  [scroll to resize]',
                    (_px2+12, _py2+34), FONT, 0.26, DCYAN, 1, cv2.LINE_AA)
        cv2.ellipse(canvas, (_px2+_pw//2, _py2+38), (_pw//2-8, 6),
                    0, 0, 180, DCYAN, 1, cv2.LINE_AA)
        _cy_e = _py2 + 52
        _cx_e = _px2 + 16
        _cw_e = _pw - 32

        if _eid == 'camera':
            if frame is not None and frame.size > 0:
                _vw = max(2, _cw_e); _vh = max(2, int(_cw_e * 9 / 16))
                if _vh > _ph - 56: _vh = max(2, _ph - 56); _vw = max(2, int(_vh * 16 / 9))
                _ef = cv2.resize(frame, (_vw, _vh))
                _vx = _px2 + (_pw - _vw) // 2
                canvas[_cy_e:_cy_e+_vh, _vx:_vx+_vw] = _ef
                # Elliptical frame around video instead of rectangle
                _vfcx = _vx + _vw // 2;  _vfcy = _cy_e + _vh // 2
                cv2.ellipse(canvas, (_vfcx, _vfcy), (_vw//2, _vh//2),
                            0, 0, 360, DCYAN, 1, cv2.LINE_AA)

        elif _eid == 'sysstat':
            _gr = min(38, (_ph - 56) // 3)
            _row1_cy = _cy_e + _gr + 10
            _row2_cy = _cy_e + 3*_gr + 30
            _g1x = _px2 + _pw//4;  _g2x = _px2 + 3*_pw//4
            for _gcx, _gpct, _glbl, _gcol in [
                (_g1x, cpu_pct,  'CPU',  GREEN if cpu_pct < 0.65 else (ORANGE if cpu_pct < 0.85 else RED)),
                (_g2x, gpu_util, 'GPU',  CYAN  if gpu_util < 0.65 else (ORANGE if gpu_util < 0.85 else RED)),
            ]:
                _gc = (_gcol[0]//4, _gcol[1]//4, _gcol[2]//4)
                _arc_gauge(canvas, _gcx, _row1_cy, _gr, _gpct, _gcol, _gc, VCYAN)
                _gs = f'{int(_gpct*100)}%'
                (_gtw,_gth),_ = cv2.getTextSize(_gs, FONT, 0.45, 1)
                cv2.putText(canvas, _gs, (_gcx-_gtw//2, _row1_cy+_gth//2), FONT, 0.45, _gcol, 1, cv2.LINE_AA)
                cv2.putText(canvas, _glbl, (_gcx-12, _row1_cy+_gth//2+16), FONT, 0.32, DCYAN, 1, cv2.LINE_AA)
            _g3x = _px2 + _pw//4;  _g4x = _px2 + 3*_pw//4
            for _gcx, _gpct, _glbl, _gcol in [
                (_g3x, ram_pct, 'RAM',  GREEN if ram_pct < 0.75 else (ORANGE if ram_pct < 0.90 else RED)),
                (_g4x, gpu_mem, 'VRAM', CYAN  if gpu_mem  < 0.75 else (ORANGE if gpu_mem  < 0.90 else RED)),
            ]:
                _gc = (_gcol[0]//4, _gcol[1]//4, _gcol[2]//4)
                _arc_gauge(canvas, _gcx, _row2_cy, _gr, _gpct, _gcol, _gc, VCYAN)
                _gs = f'{int(_gpct*100)}%'
                (_gtw,_gth),_ = cv2.getTextSize(_gs, FONT, 0.45, 1)
                cv2.putText(canvas, _gs, (_gcx-_gtw//2, _row2_cy+_gth//2), FONT, 0.45, _gcol, 1, cv2.LINE_AA)
                cv2.putText(canvas, _glbl, (_gcx-14, _row2_cy+_gth//2+16), FONT, 0.32, DCYAN, 1, cv2.LINE_AA)

        elif _eid == 'goals':
            cv2.putText(canvas, f'ACTIVE: {goal}', (_cx_e, _cy_e+14), FONT, 0.55, CYAN, 1, cv2.LINE_AA)
            _cy_e += 30
            try:
                import json as _js2
                with open('data/state.json') as _sf2:
                    _st2 = _js2.load(_sf2)
                _stk2 = _st2.get('goal_stack', []) or []
                for _gi2, _gs2 in enumerate(_stk2[:12]):
                    cv2.putText(canvas, f'  {_gi2+1}. {str(_gs2)[:60]}',
                                (_cx_e, _cy_e + 20 + _gi2*22), FONT, 0.40, WHITE, 1, cv2.LINE_AA)
            except Exception:
                pass

        elif _eid == 'thought':
            _lh_e = max(14, (_ph - 56) // max(1, min(20, len(thoughts or ['']))))
            _max_e = max(1, (_ph - 56) // _lh_e)
            _chars_e = _cw_e * 2 // 7
            for _ti, _tl in enumerate((thoughts or [])[-_max_e:]):
                _is_last = _ti == min(_max_e, len(thoughts)) - 1
                _tc = CYAN if _is_last else (WHITE if _ti >= _max_e - 4 else DCYAN)
                _td = (_tl[:_chars_e] + '..') if len(_tl) > _chars_e else _tl
                cv2.putText(canvas, ('> ' if _is_last else '  ') + _td,
                            (_cx_e, _cy_e + _ti*_lh_e), FONT, 0.32, _tc, 1, cv2.LINE_AA)
        break  # only one expanded at a time

    # ══════════════════════════════════════════════════════════════
    # RIGHT SIDEBAR
    # ══════════════════════════════════════════════════════════════
    # ══════════════════════════════════════════════════════════════
    # RIGHT SIDE — Rainmeter-style ring widgets, floating on black
    # ══════════════════════════════════════════════════════════════
    sx = CAM_W
    px = sx + 14

    # ── Ring widget helper ────────────────────────────────────────
    def _ring_widget(cx, cy, r, lines, label='', col=CYAN, dim=DCYAN, tick_step=20):
        """Circular ring widget: concentric rings + tick marks + centered text."""
        # Three concentric rings
        cv2.circle(canvas, (cx, cy), r + 6, (dim[0]//3, dim[1]//3, dim[2]//3), 1, cv2.LINE_AA)
        cv2.circle(canvas, (cx, cy), r + 2, dim, 1, cv2.LINE_AA)
        cv2.circle(canvas, (cx, cy), r - 4, (col[0]//3, col[1]//3, col[2]//3), 1, cv2.LINE_AA)
        cv2.circle(canvas, (cx, cy), r,     col, 1, cv2.LINE_AA)
        # Tick marks around outer ring
        for _td in range(0, 360, tick_step):
            _ta = _math.radians(_td)
            _is_maj = _td % 90 == 0
            _tr1 = r + 8;  _tr2 = r + (15 if _is_maj else 10)
            cv2.line(canvas,
                     (int(cx + _tr1*_math.cos(_ta)), int(cy + _tr1*_math.sin(_ta))),
                     (int(cx + _tr2*_math.cos(_ta)), int(cy + _tr2*_math.sin(_ta))),
                     col if _is_maj else dim, 1, cv2.LINE_AA)
        # Centered text inside
        _total_h = len(lines) * 20
        _start_y = cy - _total_h // 2 + 10
        for _li, (_txt, _sz, _tc) in enumerate(lines):
            (_tw, _th), _ = cv2.getTextSize(_txt, FONT, _sz, 2 if _sz >= 0.55 else 1)
            cv2.putText(canvas, _txt,
                        (cx - _tw // 2, _start_y + _li * 22),
                        FONT, _sz, _tc, 2 if _sz >= 0.55 else 1, cv2.LINE_AA)
        # Label below ring
        if label:
            (_lw, _), _ = cv2.getTextSize(label, FONT, 0.26, 1)
            cv2.putText(canvas, label, (cx - _lw // 2, cy + r + 18),
                        FONT, 0.26, dim, 1, cv2.LINE_AA)

    # ── Clock ring — in brain area, below camera feeds ────────────
    import datetime as _dt
    _now_dt = _dt.datetime.now()
    _clk_cx = CAM_W - 118   # right side of brain area, aligned with cameras
    _clk_cy = WIN_H - FOOT_H - 130
    _ring_widget(_clk_cx, _clk_cy, 52, [
        (_now_dt.strftime('%H:%M'), 0.62, CYAN),
        (_now_dt.strftime('%S'), 0.34, DCYAN),
    ], _now_dt.strftime('%a  %d %b').upper(), CYAN, DCYAN, 15)

    # ── COMM LINK — conversation log fills full sidebar ───────────
    _cog_y = HDR_H + 20
    _tc_active = pipeline.__class__._TEXT_ACTIVE
    _tc_input  = pipeline.__class__._TEXT_INPUT
    _sb_cl_w   = SIDE_W - 20
    _log_font  = 0.38                          # readable font size
    _log_lh_sb = 19                            # line height px
    _sb_cl_chars = max(16, _sb_cl_w // 9)     # chars per row at 0.38

    _gtext(canvas, 'COMM LINK', (px, _cog_y - 2), 0.28, CYAN2, DCYAN)

    def _wrap_sb(text, max_c):
        if len(text) <= max_c:
            return [text]
        words = text.split(' ')
        out_sb, cur_sb = [], ''
        for w_sb in words:
            if len(cur_sb) + len(w_sb) + (1 if cur_sb else 0) <= max_c:
                cur_sb = cur_sb + (' ' if cur_sb else '') + w_sb
            else:
                if cur_sb:
                    out_sb.append(cur_sb)
                cur_sb = w_sb
        if cur_sb:
            out_sb.append(cur_sb)
        return out_sb or [text[:max_c]]

    _log_max_y_sb = WIN_H - FOOT_H - 30  # gauges moved to brain area, use full sidebar height
    # Show only the most recent entries that fit, reading from newest
    _log_all = pipeline.__class__._CONVO_LOG[:]
    _log_lines = []  # list of (text, color)
    for (_spk_sb, _txt_sb) in reversed(_log_all):
        _lc_sb = CYAN if _spk_sb == 'JARVIS' else WHITE
        _prefix_sb = f'[{_spk_sb}] '
        _iw_sb = max(8, _sb_cl_chars - len(_prefix_sb))
        _wrapped_sb = _wrap_sb(_txt_sb, _iw_sb)
        _entry_lines = []
        for _wi_sb, _wl_sb in enumerate(_wrapped_sb):
            _ls_sb = (_prefix_sb if _wi_sb == 0 else ' ' * len(_prefix_sb)) + _wl_sb
            _entry_lines.append((_ls_sb, _lc_sb))
        _log_lines = _entry_lines + _log_lines
        # Check if adding this entry would overflow — if so, trim from top
        _avail = _log_max_y_sb - (_cog_y + _log_lh_sb)
        if len(_log_lines) * _log_lh_sb > _avail:
            _max_lines = max(1, _avail // _log_lh_sb)
            _log_lines = _log_lines[-_max_lines:]
            break

    _log_y_sb = _cog_y + _log_lh_sb
    for (_ls_sb, _lc_sb) in _log_lines:
        if _log_y_sb > _log_max_y_sb:
            break
        cv2.putText(canvas, _ls_sb,
                    (px, _log_y_sb), FONT, _log_font, _lc_sb, 1, cv2.LINE_AA)
        _log_y_sb += _log_lh_sb

    # Input line at bottom of sidebar comm section
    _cursor_blink_sb = '|' if int(fnum / 12) % 2 == 0 and _tc_active else ''
    _ic_sb = CYAN if _tc_active else DCYAN
    _input_y_sb = min(_log_max_y_sb + _log_lh_sb, _log_y_sb + 6)
    cv2.putText(canvas, f'> {_tc_input}{_cursor_blink_sb}',
                (px, _input_y_sb), FONT, 0.36, _ic_sb, 1, cv2.LINE_AA)
    if not _tc_active:
        cv2.putText(canvas, '[T to type]',
                    (px, _input_y_sb + _log_lh_sb), FONT, 0.26, DCYAN, 1, cv2.LINE_AA)

    # ── CPU / GPU gauges — moved into brain area, compact bar strip ──
    cpu_col  = RED if cpu_pct  > 0.85 else (ORANGE if cpu_pct  > 0.65 else GREEN)
    gpu_col  = RED if gpu_util > 0.85 else (ORANGE if gpu_util > 0.65 else CYAN)
    ram_col  = RED if ram_pct  > 0.90 else (ORANGE if ram_pct  > 0.75 else GREEN)
    vram_col = RED if gpu_mem  > 0.90 else (ORANGE if gpu_mem  > 0.75 else CYAN)
    _br_gauge_items = [
        ('CPU',  cpu_pct,  cpu_col),
        ('GPU',  gpu_util, gpu_col),
        ('RAM',  ram_pct,  ram_col),
        ('VRAM', gpu_mem,  vram_col),
    ]
    _br_bar_w = 110  # bar width per gauge
    _br_bar_h = 7
    _br_gx0 = 12
    _br_gy  = WIN_H - FOOT_H - 28  # just above footer
    for _gi, (_glbl, _gpct, _gcol) in enumerate(_br_gauge_items):
        _bgx = _br_gx0 + _gi * (_br_bar_w + 16)
        # label
        cv2.putText(canvas, f'{_glbl} {int(_gpct*100)}%',
                    (_bgx, _br_gy - 3), FONT, 0.30, _gcol, 1, cv2.LINE_AA)
        # bar track
        cv2.rectangle(canvas, (_bgx, _br_gy + 2), (_bgx + _br_bar_w, _br_gy + 2 + _br_bar_h),
                      (DCYAN[0]//4, DCYAN[1]//4, DCYAN[2]//4), -1)
        # bar fill
        _bfill = max(1, int(_br_bar_w * _gpct))
        cv2.rectangle(canvas, (_bgx, _br_gy + 2), (_bgx + _bfill, _br_gy + 2 + _br_bar_h),
                      _gcol, -1)

    # ── Detection dots — brain area bottom-left ───────────────────
    if objs:
        _det_y0 = WIN_H - FOOT_H - 60
        for _di, _obj in enumerate((objs or [])[:4]):
            _lbl = str(_obj.get('label', _obj.get('class_name', '?')))[:12]
            _conf = float(_obj.get('confidence', _obj.get('conf', 0.0)))
            _det_col = GREEN if _conf > 0.7 else (ORANGE if _conf > 0.4 else DCYAN)
            _dy = _det_y0 - _di * 14
            cv2.circle(canvas, (12, _dy), 3, _det_col, -1, cv2.LINE_AA)
            cv2.putText(canvas, f'{_lbl}  {_conf:.0%}',
                        (20, _dy + 4), FONT, 0.26, _det_col, 1, cv2.LINE_AA)

    # COMM LINK now rendered in right sidebar — no brain-area overlay
    # Keep _tc_active / _tc_input accessible for keyboard handler below
    _tc_active = pipeline.__class__._TEXT_ACTIVE
    _tc_input  = pipeline.__class__._TEXT_INPUT
    _cursor_blink = '|' if int(fnum / 12) % 2 == 0 and _tc_active else ''

    # ══════════════════════════════════════════════════════════════
    # FOOTER
    # ══════════════════════════════════════════════════════════════
    # Footer — no fill, floating on black
    fy = WIN_H - FOOT_H
    # Arc sweep instead of a solid bar
    cv2.ellipse(canvas, (WIN_W // 2, WIN_H),
                (WIN_W // 2, FOOT_H + 6), 0, 180, 360,
                (DCYAN[0]//2, DCYAN[1]//2, DCYAN[2]//2), 1, cv2.LINE_AA)
    fmy = fy + 22

    # Voice dot + text
    if voice_text:
        pulse_r2 = 5 + int(3 * abs(_math.sin(fnum * 0.1)))
        _gcircle(canvas, (18, fmy - 5), pulse_r2, ORANGE, DORANGE, -1)
        vt = (voice_text[:80] + '…') if len(voice_text) > 80 else voice_text
        cv2.putText(canvas, vt, (32, fmy), FONT, 0.38, ORANGE, 1, cv2.LINE_AA)
    else:
        cv2.circle(canvas, (18, fmy - 5), 5, DCYAN, 1, cv2.LINE_AA)
        cv2.putText(canvas, 'VOICE READY  F9=PTT',
                    (32, fmy), FONT, 0.35, DCYAN, 1, cv2.LINE_AA)

    # Objective right-aligned
    obj_label = f'>> {goal[:55]}'
    (tw3, _), _ = cv2.getTextSize(obj_label, FONT, 0.38, 1)
    _gtext(canvas, obj_label, (WIN_W - tw3 - 10, fmy), 0.38, CYAN, DCYAN)

    # Dot constellation centre-footer (no rectangles)
    for _fi, xi in enumerate(range(WIN_W // 2 - 60, WIN_W // 2 + 61, 12)):
        _fr = 2 if _fi % 3 == 0 else 1
        cv2.circle(canvas, (xi, fy + 10), _fr, VCYAN, -1, cv2.LINE_AA)

    if _CV2_GUI_OK:
        # Make window resizable on first render; strip OS decoration
        if not getattr(pipeline.__class__, '_WINDOW_CREATED', False):
            cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(window_name, WIN_W, WIN_H)
            pipeline.__class__._WINDOW_CREATED = True
            # Strip WM title bar/border only — NOT fullscreen
            # Press F in the window to toggle fullscreen manually
            time.sleep(0.5)
            try:
                _wid = _sp2.check_output(
                    ['xdotool', 'search', '--name', window_name],
                    timeout=2).decode().split()[0]
                _sp2.Popen(['xprop', '-id', _wid,
                            '-f', '_MOTIF_WM_HINTS', '32c',
                            '-set', '_MOTIF_WM_HINTS', '2, 0, 0, 0, 0'])
            except Exception:
                pass
        cv2.imshow(window_name, canvas)

# ── Display pump (main-thread only) ──────────────────────────────────

