# core/frame_server.py
"""
Lightweight MJPEG HTTP server for viewing AKSUMAEL's vision in a browser.

Usage in runtime.py (after pipeline.start()):
    from core.frame_server import FrameServer
    frame_server = FrameServer(pipeline)
    frame_server.start()

View at: http://localhost:8765/
"""
import io
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import cv2
import numpy as np

_pipeline_ref = None


class _Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass  # suppress per-request access logs

    def do_GET(self):
        if self.path == '/stream':
            self._serve_mjpeg()
        elif self.path == '/frame':
            self._serve_single_jpeg()
        else:
            self._serve_html()

    # ── helpers ──────────────────────────────────────────────────────────

    def _get_annotated_frame(self):
        """Return an annotated BGR frame, or a blank 'No signal' image."""
        if _pipeline_ref is None:
            return _blank_frame('No pipeline')

        frame, objs = _pipeline_ref.display.get_display_frame()

        if frame is None:
            return _blank_frame('No signal')

        out = frame.copy()
        for obj in (objs or []):
            x1 = int(obj.get('x1', 0))
            y1 = int(obj.get('y1', 0))
            x2 = int(obj.get('x2', 0))
            y2 = int(obj.get('y2', 0))
            label = f"{obj.get('label', '?')} {obj.get('conf', 0):.2f}"
            cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(out, label, (x1, max(y1 - 5, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
        return out

    def _encode_jpeg(self, frame):
        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return buf.tobytes()

    # ── routes ───────────────────────────────────────────────────────────

    def _serve_mjpeg(self):
        """Multipart MJPEG stream — works in Chrome/Firefox natively."""
        self.send_response(200)
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        try:
            while True:
                frame = self._get_annotated_frame()
                data  = self._encode_jpeg(frame)
                self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\n\r\n')
                self.wfile.write(data)
                self.wfile.write(b'\r\n')
                self.wfile.flush()
                time.sleep(1 / 15)   # ~15 fps — low enough to be cheap
        except Exception:
            pass   # client disconnected

    def _serve_single_jpeg(self):
        """Single JPEG snapshot — useful for quick checks."""
        frame = self._get_annotated_frame()
        data  = self._encode_jpeg(frame)
        self.send_response(200)
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    def _serve_html(self):
        html = b'''<!DOCTYPE html>
<html><head>
<title>AKSUMAEL Live View</title>
<meta charset="utf-8"/>
<style>
  body{background:#111;color:#0f0;font-family:monospace;
       text-align:center;margin:0;padding:12px}
  h1{font-size:1.1em;letter-spacing:.1em;margin:0 0 10px}
  img{max-width:100%;border:1px solid #1a1a1a;display:block;margin:0 auto}
  small{color:#444;font-size:.75em}
</style>
</head><body>
<h1>&#x25B6; AKSUMAEL LIVE VIEW</h1>
<img src="/stream"/>
<br/><small>MJPEG stream &bull; ~15 fps &bull; /frame for snapshot</small>
</body></html>'''
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(html)))
        self.end_headers()
        self.wfile.write(html)


# ── public API ────────────────────────────────────────────────────────────────

class FrameServer:
    """
    Start an MJPEG HTTP server that streams annotated frames from the
    VideoCapturePipeline's DisplayThread buffer.

    Args:
        pipeline: A ``core.capture.VideoCapturePipeline`` instance.
        port: TCP port to bind (default 8765).
    """
    DEFAULT_PORT = 8765

    def __init__(self, pipeline, port: int = DEFAULT_PORT):
        global _pipeline_ref
        _pipeline_ref = pipeline
        self._port   = port
        self._server = HTTPServer(('0.0.0.0', port), _Handler)
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
    cv2.putText(img, msg, (180, 180),
                cv2.FONT_HERSHEY_SIMPLEX, 1.4, (80, 80, 80), 2, cv2.LINE_AA)
    return img
