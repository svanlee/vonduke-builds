# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Visual Labeling UI                 ║
# ║  Live YOLO overlay + skill viewer sidebar           ║
# ╚══════════════════════════════════════════════════════╝
#
# Layout (960×540 window):
#   Left  720px : live camera frame with YOLO boxes
#   Right 240px : skill viewer sidebar
#   Bottom HUD  : status bar
#
# Controls:
#   Click box + type + Enter → label it
#   TAB       → next unknown box
#   ESC       → cancel / deselect
#   p         → pause / resume
#   m         → cycle blend mode
#   g / b     → good / bad reward
#   s         → toggle sidebar
#   q         → quit

import cv2
import time
import config

# Colours (BGR)
C_KNOWN    = (0, 200, 0)
C_USER     = (200, 130, 0)
C_UNKNOWN  = (0, 0, 220)
C_SELECTED = (0, 220, 220)
C_HUD_BG   = (30, 30, 30)
C_HUD_TXT  = (200, 200, 200)
C_SIDE_BG  = (25, 25, 35)
C_SIDE_HDR = (80, 160, 255)
C_SKILL_G  = (0, 200, 80)
C_SKILL_B  = (0, 80, 200)
C_WHITE    = (255, 255, 255)

FONT       = cv2.FONT_HERSHEY_SIMPLEX
FONT_SM    = 0.38
FONT_MD    = 0.48
FONT_LG    = 0.58

WIN_W      = 960
WIN_H      = 540
SIDE_W     = 240
FRAME_W    = WIN_W - SIDE_W   # 720
HUD_H      = 36


class LabelingUI:
    WINDOW = 'AKSUMAEL'

    def __init__(self, yolo, router=None, reward=None, skills=None):
        self.yolo    = yolo
        self.router  = router
        self.reward  = reward
        self.skills  = skills   # SkillSystem reference for sidebar

        self.frame        = None
        self.objects      = []
        self.selected_idx = None
        self.typing       = False
        self.type_buffer  = ''

        # Runtime state flags read by the main loop
        self.paused         = False
        self.quit           = False
        self.pending_reward = 0
        self.show_sidebar   = True
        self._overlay_text  = ''   # inner-monologue caption (see set_overlay_text)

        # For correct mouse→frame coordinate mapping
        self._frame_h = 1
        self._frame_w = 1
        self._disp_h  = WIN_H - HUD_H
        self._disp_w  = FRAME_W

        self._enabled = self._init_window()

    def _init_window(self) -> bool:
        # Headless rigs (no monitor — see config.ENABLE_DISPLAY_UI) have no
        # GTK/Qt/Cocoa backend for cv2 to open a window against; skip the
        # attempt entirely instead of trying-and-catching a guaranteed
        # failure on every single restart.
        if not getattr(config, 'ENABLE_DISPLAY_UI', False):
            return False
        try:
            cv2.namedWindow(self.WINDOW, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(self.WINDOW, WIN_W, WIN_H)
            cv2.setMouseCallback(self.WINDOW, self._on_mouse)
            print('[UI] labeling window ready  '
                  '(960×540 | sidebar=on | TAB=next unknown)')
            return True
        except Exception as e:
            print(f'[UI] window init failed (headless?): {e}')
            return False

    @property
    def enabled(self):
        return self._enabled

    # ── Mouse ──────────────────────────────────────────────────
    def _on_mouse(self, event, x, y, flags, param):
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        # Only respond to clicks in the frame area (left FRAME_W px)
        if x > FRAME_W:
            return
        # Map display coords → frame coords
        fh, fw = self._frame_h, self._frame_w
        sx = fw / self._disp_w
        sy = fh / self._disp_h
        fx = x * sx
        fy = y * sy
        for i, obj in enumerate(self.objects):
            x1, y1, x2, y2 = obj['box']
            if x1 <= fx <= x2 and y1 <= fy <= y2:
                self.selected_idx = i
                self.typing       = True
                self.type_buffer  = ''
                lbl = obj['label']
                print(f'[UI] box selected: "{lbl}" (conf {obj["conf"]:.2f}) '
                      f'— type new label + Enter')
                return
        # Click on empty space → deselect
        self.selected_idx = None
        self.typing       = False

    # ── Update ─────────────────────────────────────────────────
    def update(self, frame, objects: list):
        self.frame   = frame
        self.objects = objects if objects else []

    def set_overlay_text(self, text: str):
        """Set the inner-monologue caption drawn in the black letterbox
        strip below the video (see _draw_monologue) — called from the main
        decision loop via VideoCapturePipeline.set_overlay_text()."""
        self._overlay_text = text or ''

    # ── Render ─────────────────────────────────────────────────
    def render(self) -> bool:
        """Draw everything. Returns False when the user quits."""
        if not self._enabled:
            return not self.quit

        canvas = self._make_canvas()
        self._draw_frame(canvas)
        if self.show_sidebar:
            self._draw_sidebar(canvas)
        self._draw_hud(canvas)
        if self.typing and self.selected_idx is not None:
            self._draw_type_overlay(canvas)

        cv2.imshow(self.WINDOW, canvas)
        self._handle_keys()
        return not self.quit

    def _make_canvas(self):
        import numpy as np
        return np.zeros((WIN_H, WIN_W, 3), dtype='uint8')

    def _draw_frame(self, canvas):
        import cv2, numpy as np
        area_h = WIN_H - HUD_H

        if self.frame is None:
            cv2.putText(canvas, 'No frame — check camera',
                        (20, area_h // 2), FONT, FONT_LG, C_UNKNOWN, 1)
            return

        fh, fw = self.frame.shape[:2]
        self._frame_h = fh
        self._frame_w = fw

        # Scale frame to fill FRAME_W × area_h, preserving aspect ratio
        scale = min(FRAME_W / fw, area_h / fh)
        dw    = int(fw * scale)
        dh    = int(fh * scale)
        self._disp_w = dw
        self._disp_h = dh

        resized = cv2.resize(self.frame, (dw, dh))
        canvas[:dh, :dw] = resized

        # Draw YOLO boxes scaled to display size
        for i, obj in enumerate(self.objects):
            x1, y1, x2, y2 = obj['box']
            dx1 = int(x1 * scale)
            dy1 = int(y1 * scale)
            dx2 = int(x2 * scale)
            dy2 = int(y2 * scale)

            if i == self.selected_idx:
                col = C_SELECTED
            elif obj.get('user_label'):
                col = C_USER
            elif obj.get('unknown'):
                col = C_UNKNOWN
            else:
                col = C_KNOWN

            cv2.rectangle(canvas, (dx1, dy1), (dx2, dy2), col, 2)

            tag = obj['label']
            if obj.get('unknown') and not obj.get('user_label'):
                tag = f'? {tag}'
            tag += f' {obj["conf"]:.2f}'

            # Text background
            (tw, th), _ = cv2.getTextSize(tag, FONT, FONT_SM, 1)
            ty = max(th + 2, dy1 - 2)
            cv2.rectangle(canvas, (dx1, ty - th - 2), (dx1 + tw + 2, ty + 2),
                          (0, 0, 0), -1)
            cv2.putText(canvas, tag, (dx1 + 1, ty), FONT, FONT_SM, col, 1,
                        cv2.LINE_AA)

        self._draw_monologue(canvas, dh, area_h)

    def _draw_monologue(self, canvas, video_bottom: int, area_h: int):
        """Draw the inner-monologue caption in the black letterbox strip
        between the scaled video frame and the HUD bar — white text on the
        canvas's existing black background, not burned onto the video
        frame itself (that overlaid the caption on the game feed and let
        it get scaled/squashed along with the video).

        self._overlay_text arrives pre-wrapped and newline-joined from
        core/capture.py's monologue buffer/typewriter animation (see
        push_monologue_line() and VideoCapturePipeline.poll_display()) —
        one physical line per '\\n', newest line last and possibly still
        mid-typing, so we just split and draw rather than re-wrapping."""
        if not self._overlay_text:
            return
        strip_h = area_h - video_bottom
        if strip_h < 14:
            return   # frame fills the whole area — no black strip to use
        scale       = FONT_LG
        thickness   = 1
        line_height = 26
        max_lines   = max(1, strip_h // line_height)
        lines = self._overlay_text.split('\n')
        lines = lines[-max_lines:]
        y = video_bottom + line_height
        for line in lines:
            cv2.putText(canvas, line, (8, y), FONT, scale, C_WHITE, thickness,
                        cv2.LINE_AA)
            y += line_height

    def _draw_sidebar(self, canvas):
        import cv2
        x0 = FRAME_W
        h  = WIN_H - HUD_H

        # Background
        canvas[:h, x0:] = C_SIDE_BG

        # Header
        cv2.putText(canvas, 'SKILLS', (x0 + 8, 22),
                    FONT, FONT_MD, C_SIDE_HDR, 1, cv2.LINE_AA)

        if self.skills is None or not self.skills.skills:
            cv2.putText(canvas, 'none yet', (x0 + 8, 50),
                        FONT, FONT_SM, C_HUD_TXT, 1)
            self._draw_label_legend(canvas, x0, 90)
            return

        skill_list = self.skills.list_skills()[:12]  # show top 12
        y = 45
        for sk in skill_list:
            # Reward bar (0–1 mapped to 0–120px)
            bar_w = int(max(0.0, min(1.0, sk.avg_reward)) * 120)
            col   = C_SKILL_G if sk.avg_reward >= 0 else C_SKILL_B
            cv2.rectangle(canvas, (x0 + 8, y - 10),
                          (x0 + 8 + bar_w, y - 2), col, -1)
            # Name (truncated)
            name = sk.name[:18]
            cv2.putText(canvas, name, (x0 + 8, y + 8),
                        FONT, FONT_SM, C_WHITE, 1, cv2.LINE_AA)
            # Stats
            stats = f'r={sk.avg_reward:.2f} x{sk.uses}'
            cv2.putText(canvas, stats, (x0 + 8, y + 20),
                        FONT, FONT_SM, C_HUD_TXT, 1, cv2.LINE_AA)
            y += 38
            if y > h - 80:
                break

        # Divider
        cv2.line(canvas, (x0 + 8, y + 4), (WIN_W - 8, y + 4),
                 (60, 60, 80), 1)
        self._draw_label_legend(canvas, x0, y + 18)

    def _draw_label_legend(self, canvas, x0: int, y: int):
        """Colour legend for box types."""
        import cv2
        legend = [
            (C_KNOWN,   'known'),
            (C_USER,    'labeled'),
            (C_UNKNOWN, 'unknown'),
        ]
        for col, txt in legend:
            cv2.rectangle(canvas, (x0 + 8, y - 9), (x0 + 18, y + 1), col, -1)
            cv2.putText(canvas, txt, (x0 + 22, y),
                        FONT, FONT_SM, C_HUD_TXT, 1, cv2.LINE_AA)
            y += 16

    def _draw_hud(self, canvas):
        import cv2
        y0 = WIN_H - HUD_H
        # Background bar
        canvas[y0:, :] = C_HUD_BG

        mode    = self.router.blend_mode if self.router else '?'
        status  = 'PAUSED' if self.paused else 'RUN'
        unkn    = sum(1 for o in self.objects
                      if o.get('unknown') and not o.get('user_label'))
        sk_cnt  = len(self.skills.skills) if self.skills else 0

        left  = f' AKSUMAEL  {status}  mode:{mode}  skills:{sk_cnt}  unknowns:{unkn}'
        right = 'click=label  TAB=next  p=pause  m=mode  g/b=reward  s=sidebar  q=quit'

        cv2.putText(canvas, left,  (6, y0 + 22), FONT, FONT_MD, C_HUD_TXT, 1)
        cv2.putText(canvas, right, (6, y0 + HUD_H - 4), FONT, FONT_SM,
                    (130, 130, 130), 1)

    def _draw_type_overlay(self, canvas):
        import cv2
        obj    = self.objects[self.selected_idx]
        prompt = f' Labeling "{obj["label"]}" → {self.type_buffer}_'
        # Overlay at top of frame area
        cv2.rectangle(canvas, (0, 0), (FRAME_W, 32), (10, 10, 50), -1)
        cv2.putText(canvas, prompt, (6, 22),
                    FONT, FONT_MD, (0, 255, 255), 1, cv2.LINE_AA)

    # ── Keys ───────────────────────────────────────────────────
    def _handle_keys(self):
        key = cv2.waitKey(1) & 0xFF
        if key == 255:
            return
        if self.typing:
            self._handle_typing(key)
        else:
            self._handle_command(key)

    def _handle_command(self, key: int):
        if key == ord('q'):
            self.quit = True
        elif key == ord('p'):
            self.paused = not self.paused
            print(f'[UI] {"paused" if self.paused else "resumed"}')
        elif key == ord('m') and self.router:
            self.router.cycle_blend_mode()
        elif key == ord('g'):
            self.pending_reward = +1
        elif key == ord('b'):
            self.pending_reward = -1
        elif key == ord('s'):
            self.show_sidebar = not self.show_sidebar
        elif key == 9:   # TAB
            self._select_next_unknown()
        elif key == 27:  # ESC
            self.selected_idx = None
            self.typing = False

    def _handle_typing(self, key: int):
        if key in (13, 10):    # Enter
            self._commit_label()
        elif key == 27:        # ESC
            self.typing       = False
            self.type_buffer  = ''
            self.selected_idx = None
        elif key == 8:         # Backspace
            self.type_buffer  = self.type_buffer[:-1]
        elif 32 <= key <= 126:
            self.type_buffer += chr(key)

    def _commit_label(self):
        if self.selected_idx is None or not self.type_buffer.strip():
            self.typing = False
            return
        label = self.type_buffer.strip().lower()
        obj   = self.objects[self.selected_idx]
        self.yolo.teach_label(obj['box'], label)
        obj['label']      = label
        obj['user_label'] = True
        obj['unknown']    = False
        print(f'[UI] labeled as "{label}"')
        self.typing       = False
        self.type_buffer  = ''
        self.selected_idx = None

    def _select_next_unknown(self):
        unkn = [i for i, o in enumerate(self.objects)
                if o.get('unknown') and not o.get('user_label')]
        if not unkn:
            print('[UI] no unknown boxes')
            return
        if self.selected_idx in unkn:
            cur = unkn.index(self.selected_idx)
            self.selected_idx = unkn[(cur + 1) % len(unkn)]
        else:
            self.selected_idx = unkn[0]
        self.typing      = True
        self.type_buffer = ''
        obj = self.objects[self.selected_idx]
        print(f'[UI] unknown selected: {obj["box"]} — type label + Enter')

    # ── Public helpers ─────────────────────────────────────────
    def consume_reward(self) -> int:
        r = self.pending_reward
        self.pending_reward = 0
        return r

    def close(self):
        if self._enabled:
            cv2.destroyAllWindows()


# ── Quick test ─────────────────────────────────────────────────
if __name__ == '__main__':
    import numpy as np

    class FakeYolo:
        def teach_label(self, box, label):
            print(f'  taught: {label} → {box}')

    class FakeRouter:
        blend_mode = 'aksumael_only'
        def cycle_blend_mode(self):
            modes = ['aksumael_only','human_only','assist','blend']
            self.blend_mode = modes[(modes.index(self.blend_mode)+1)%4]
            return self.blend_mode

    class FakeSkills:
        class _sk:
            name='tree_a1b2c3'; avg_reward=0.72; uses=14
            trigger_objects=['tree']
            steps=[]
            actions=[]
        class _sk2:
            name='stone_d4e5f6'; avg_reward=0.31; uses=3
            trigger_objects=['stone']
            steps=[]
            actions=[]
        skills = {'a': _sk(), 'b': _sk2()}
        def list_skills(self): return list(self.skills.values())

    print('Labeling UI self-test (needs a display)')
    ui = LabelingUI(FakeYolo(), FakeRouter(), skills=FakeSkills())
    if not ui.enabled:
        print('No display — UI runs on the Victus laptop screen.')
    else:
        frame = np.full((480, 720, 3), 40, dtype='uint8')
        objects = [
            {'label':'person','conf':0.92,'box':[50,50,200,380],
             'user_label':False,'unknown':False},
            {'label':'thing','conf':0.28,'box':[350,80,550,300],
             'user_label':False,'unknown':True},
            {'label':'tree','conf':0.71,'box':[580,40,700,400],
             'user_label':True,'unknown':False},
        ]
        print('Window open. Click the red box, type a label, Enter. q to quit.')
        while True:
            ui.update(frame, objects)
            if not ui.render():
                break
        ui.close()
