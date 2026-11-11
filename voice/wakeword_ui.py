"""
Live wake word detection UI.
Runs a local web server + streams scores to the browser via SSE.

Usage:
    python3 wakeword_ui.py [model.onnx] [threshold]
"""
import sys
import subprocess
import threading
import time
import numpy as np
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from openwakeword.model import Model

MODEL_PATH = sys.argv[1] if len(sys.argv) > 1 else "sunday.onnx"
THRESHOLD  = float(sys.argv[2]) if len(sys.argv) > 2 else 0.15
PORT       = int(sys.argv[3]) if len(sys.argv) > 3 else 7777

# Shared state
latest = {"score": 0.0, "detected": False}
clients = []
clients_lock = threading.Lock()

HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Wake Word Tester</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #0a0a0a;
    color: #fff;
    font-family: -apple-system, BlinkMacSystemFont, sans-serif;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100vh;
    gap: 40px;
    user-select: none;
  }

  #circle {
    width: 220px;
    height: 220px;
    border-radius: 50%;
    background: #111;
    border: 3px solid #222;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 15px;
    color: #555;
    letter-spacing: 2px;
    text-transform: uppercase;
    transition: background 0.1s, border-color 0.1s, box-shadow 0.1s;
    position: relative;
  }

  #circle.active {
    background: #1a1a2e;
    border-color: #4f8ef7;
    box-shadow: 0 0 60px #4f8ef7aa, 0 0 120px #4f8ef744;
    color: #a0c4ff;
  }

  #circle.detected {
    background: #0d2a0d;
    border-color: #4cff72;
    box-shadow: 0 0 80px #4cff72cc, 0 0 160px #4cff7255;
    color: #b0ffbb;
  }

  #bar-wrap {
    width: 320px;
    background: #1a1a1a;
    border-radius: 8px;
    overflow: hidden;
    height: 8px;
  }

  #bar {
    height: 100%;
    width: 0%;
    background: #4f8ef7;
    border-radius: 8px;
    transition: width 0.08s, background 0.08s;
  }

  #bar.detected { background: #4cff72; }

  #score-label {
    font-size: 13px;
    color: #444;
    letter-spacing: 1px;
  }

  #score-label span { color: #888; }

  #threshold-label {
    font-size: 11px;
    color: #333;
  }
</style>
</head>
<body>
<div id="circle">listening</div>
<div id="bar-wrap"><div id="bar"></div></div>
<div id="score-label">score: <span id="score-val">0.0000</span></div>
<div id="threshold-label">threshold: THRESH</div>

<script>
const circle = document.getElementById('circle');
const bar    = document.getElementById('bar');
const scoreVal = document.getElementById('score-val');
const thresh = THRESH_VALUE;
let detectedTimer = null;

document.querySelector('#threshold-label').textContent = 'threshold: ' + thresh.toFixed(2);

const es = new EventSource('/stream');
es.onmessage = (e) => {
  const d = JSON.parse(e.data);
  const score = d.score;
  const pct = Math.min(score * 100 / thresh, 100);

  scoreVal.textContent = score.toFixed(4);
  bar.style.width = pct + '%';

  if (d.detected) {
    circle.className = 'detected';
    circle.textContent = 'sunday';
    bar.className = 'detected';
    clearTimeout(detectedTimer);
    detectedTimer = setTimeout(() => {
      circle.className = score > 0.02 ? 'active' : '';
      circle.textContent = 'listening';
      bar.className = '';
    }, 800);
  } else if (score > 0.02) {
    if (circle.className !== 'detected') {
      circle.className = 'active';
      circle.textContent = 'listening';
    }
  } else {
    if (circle.className !== 'detected') {
      circle.className = '';
      circle.textContent = 'listening';
    }
  }
};
</script>
</body>
</html>
""".replace("THRESH_VALUE", str(THRESHOLD)).replace("THRESH", str(THRESHOLD))


def broadcast(data: str):
    dead = []
    with clients_lock:
        for q in clients:
            try:
                q.put_nowait(data)
            except Exception:
                dead.append(q)
        for q in dead:
            clients.remove(q)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # silence request logs

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(HTML.encode())

        elif self.path == "/stream":
            import queue
            q = queue.Queue(maxsize=30)
            with clients_lock:
                clients.append(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                while True:
                    try:
                        msg = q.get(timeout=5)
                        self.wfile.write(f"data: {msg}\n\n".encode())
                        self.wfile.flush()
                    except Exception:
                        # send keepalive
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
            except Exception:
                with clients_lock:
                    if q in clients:
                        clients.remove(q)
        else:
            self.send_response(404)
            self.end_headers()


def detection_loop():
    import json
    try:
        model = Model(wakeword_models=[MODEL_PATH])
    except TypeError:
        model = Model(wakeword_model_paths=[MODEL_PATH])
    if sys.platform == "darwin":
        cmd = ["sox", "-t", "coreaudio", "AIRHUG 21", "-r", "16000", "-c", "1",
               "-e", "signed-integer", "-b", "16", "-t", "raw", "-"]
    else:
        cmd = ["arecord", "-D", "plughw:A21", "-r", "16000", "-c", "1",
               "-f", "S16_LE", "-t", "raw", "-q"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    frame = 0
    try:
        while True:
            raw = proc.stdout.read(1280 * 2)
            if not raw:
                break
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            audio = np.clip(audio * 3.0, -32768, 32767).astype(np.int16)
            preds = model.predict(audio)
            frame += 1
            if frame <= 10:
                continue
            score = float(max(preds.values()))
            detected = score >= THRESHOLD
            broadcast(json.dumps({"score": round(score, 4), "detected": detected}))
    finally:
        proc.kill()
        proc.wait()


if __name__ == "__main__":
    print(f"[Wake UI] Model:     {MODEL_PATH}")
    print(f"[Wake UI] Threshold: {THRESHOLD}")
    print(f"[Wake UI] Open:      http://localhost:{PORT}")

    t = threading.Thread(target=detection_loop, daemon=True)
    t.start()

    server = ThreadingHTTPServer(("", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Wake UI] Stopped.")
