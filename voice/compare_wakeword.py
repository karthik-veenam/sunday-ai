"""
Side-by-side wake word comparison — single mic capture feeds both models.

Usage:
    python3 compare_wakeword.py <old_model.onnx> <new_model.onnx> [threshold] [port]
"""
import sys
import subprocess
import threading
import json
import numpy as np
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from openwakeword.model import Model

OLD_MODEL = sys.argv[1] if len(sys.argv) > 1 else "sunday.onnx"
NEW_MODEL = sys.argv[2] if len(sys.argv) > 2 else "sun_day.onnx"
THRESHOLD = float(sys.argv[3]) if len(sys.argv) > 3 else 0.15
PORT      = int(sys.argv[4]) if len(sys.argv) > 4 else 7779

clients     = {"old": [], "new": []}
clients_lock = threading.Lock()

HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Wake Word Comparison</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #050505;
    font-family: -apple-system, BlinkMacSystemFont, sans-serif;
    height: 100vh;
    display: flex;
    flex-direction: column;
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-around;
    padding: 16px 0 0;
    flex-shrink: 0;
  }
  .label {
    color: #444;
    font-size: 12px;
    letter-spacing: 2px;
    text-transform: uppercase;
    text-align: center;
  }
  .label strong { display: block; color: #888; font-size: 14px; margin-bottom: 4px; }
  .panels { display: flex; flex: 1; }
  .panel {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 32px;
    border-right: 1px solid #111;
  }
  .panel:last-child { border-right: none; }
  .circle {
    width: 200px; height: 200px; border-radius: 50%;
    background: #111; border: 3px solid #222;
    display: flex; align-items: center; justify-content: center;
    font-size: 13px; color: #555; letter-spacing: 2px; text-transform: uppercase;
    transition: background 0.1s, border-color 0.1s, box-shadow 0.1s;
  }
  .circle.active {
    background: #1a1a2e; border-color: #4f8ef7;
    box-shadow: 0 0 60px #4f8ef7aa, 0 0 120px #4f8ef744; color: #a0c4ff;
  }
  .circle.detected {
    background: #0d2a0d; border-color: #4cff72;
    box-shadow: 0 0 80px #4cff72cc, 0 0 160px #4cff7255; color: #b0ffbb;
  }
  .bar-wrap { width: 280px; background: #1a1a1a; border-radius: 8px; height: 8px; overflow: hidden; }
  .bar { height: 100%; width: 0%; background: #4f8ef7; border-radius: 8px; transition: width 0.08s, background 0.08s; }
  .bar.detected { background: #4cff72; }
  .score { font-size: 13px; color: #444; letter-spacing: 1px; }
  .score span { color: #888; }
</style>
</head>
<body>
<header>
  <div class="label"><strong>OLD_NAME</strong>old model</div>
  <div class="label"><strong>NEW_NAME</strong>new model</div>
</header>
<div class="panels">
  <div class="panel">
    <div class="circle" id="c-old">listening</div>
    <div class="bar-wrap"><div class="bar" id="b-old"></div></div>
    <div class="score">score: <span id="s-old">0.0000</span></div>
  </div>
  <div class="panel">
    <div class="circle" id="c-new">listening</div>
    <div class="bar-wrap"><div class="bar" id="b-new"></div></div>
    <div class="score">score: <span id="s-new">0.0000</span></div>
  </div>
</div>
<script>
const thresh = THRESH_VALUE;
function connect(key, circleId, barId, scoreId) {
  const circle = document.getElementById(circleId);
  const bar    = document.getElementById(barId);
  const score  = document.getElementById(scoreId);
  let timer = null;
  const es = new EventSource('/stream/' + key);
  es.onmessage = (e) => {
    const d = JSON.parse(e.data);
    const s = d.score;
    score.textContent = s.toFixed(4);
    bar.style.width = Math.min(s / thresh * 100, 100) + '%';
    if (d.detected) {
      circle.className = 'circle detected';
      circle.textContent = 'sunday';
      bar.className = 'bar detected';
      clearTimeout(timer);
      timer = setTimeout(() => {
        circle.className = s > 0.02 ? 'circle active' : 'circle';
        circle.textContent = 'listening';
        bar.className = 'bar';
      }, 800);
    } else if (s > 0.02) {
      if (circle.className !== 'circle detected') { circle.className = 'circle active'; }
    } else {
      if (circle.className !== 'circle detected') { circle.className = 'circle'; }
    }
  };
}
connect('old', 'c-old', 'b-old', 's-old');
connect('new', 'c-new', 'b-new', 's-new');
</script>
</body>
</html>
""".replace("OLD_NAME", OLD_MODEL.split("/")[-1]) \
   .replace("NEW_NAME", NEW_MODEL.split("/")[-1]) \
   .replace("THRESH_VALUE", str(THRESHOLD))


def broadcast(key: str, data: str):
    import queue as _q
    dead = []
    with clients_lock:
        for q in clients[key]:
            try:
                q.put_nowait(data)
            except Exception:
                dead.append(q)
        for q in dead:
            clients[key].remove(q)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(HTML.encode())
        elif self.path in ("/stream/old", "/stream/new"):
            import queue
            key = self.path.split("/")[-1]
            q = queue.Queue(maxsize=30)
            with clients_lock:
                clients[key].append(q)
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
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
            except Exception:
                with clients_lock:
                    if q in clients[key]:
                        clients[key].remove(q)
        else:
            self.send_response(404)
            self.end_headers()


def detection_loop():
    print(f"[Compare] Loading old: {OLD_MODEL}")
    try:
        old = Model(wakeword_models=[OLD_MODEL])
    except TypeError:
        old = Model(wakeword_model_paths=[OLD_MODEL])

    print(f"[Compare] Loading new: {NEW_MODEL}")
    try:
        new = Model(wakeword_models=[NEW_MODEL])
    except TypeError:
        new = Model(wakeword_model_paths=[NEW_MODEL])

    if sys.platform == "darwin":
        cmd = ["sox", "-t", "coreaudio", "AIRHUG 21", "-r", "16000", "-c", "1",
               "-e", "signed-integer", "-b", "16", "-t", "raw", "-"]
    else:
        cmd = ["arecord", "-D", "plughw:A21", "-r", "16000", "-c", "1",
               "-f", "S16_LE", "-t", "raw", "-q"]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    print("[Compare] Mic open, streaming...")

    frame = 0
    try:
        while True:
            raw = proc.stdout.read(1280 * 2)
            if not raw:
                break
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            audio = np.clip(audio * 3.0, -32768, 32767).astype(np.int16)

            op = old.predict(audio)
            np_ = new.predict(audio)

            frame += 1
            if frame <= 10:
                continue

            old_score = float(max(op.values()))
            new_score = float(max(np_.values()))

            broadcast("old", json.dumps({"score": round(old_score, 4), "detected": old_score >= THRESHOLD}))
            broadcast("new", json.dumps({"score": round(new_score, 4), "detected": new_score >= THRESHOLD}))
    finally:
        proc.kill()
        proc.wait()


if __name__ == "__main__":
    print(f"[Compare] Old: {OLD_MODEL}")
    print(f"[Compare] New: {NEW_MODEL}")
    print(f"[Compare] Open: http://0.0.0.0:{PORT}")

    t = threading.Thread(target=detection_loop, daemon=True)
    t.start()

    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
