"""
Wake word score UI — custom pipeline matching training.
Open http://djpi.local:7778 in browser.
"""
import sys, subprocess, threading, json, queue
import numpy as np
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import onnxruntime as ort

MODEL_PATH = sys.argv[1] if len(sys.argv) > 1 else "/home/djpi/openwakeword-models/sunday_v18.onnx"
THRESHOLD  = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5
PORT       = int(sys.argv[3]) if len(sys.argv) > 3 else 7778

OWW = Path("/home/djpi/momo-env/lib/python3.13/site-packages/openwakeword/resources/models")
CHUNK, MEL_WINDOW, MEL_BINS, EMB_DIM, EMB_WINDOW = 1280, 76, 32, 96, 16

clients = []
clients_lock = threading.Lock()

HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Wake Word — Sunday</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body {
  background:#080808; color:#fff;
  font-family:-apple-system,BlinkMacSystemFont,sans-serif;
  display:flex; flex-direction:column;
  align-items:center; justify-content:center;
  height:100vh; gap:36px;
}
#circle {
  width:240px; height:240px; border-radius:50%;
  background:#111; border:3px solid #222;
  display:flex; align-items:center; justify-content:center;
  font-size:16px; color:#555; letter-spacing:3px; text-transform:uppercase;
  transition:background .1s, border-color .1s, box-shadow .1s;
}
#circle.active  { background:#1a1a2e; border-color:#4f8ef7; box-shadow:0 0 60px #4f8ef7aa; color:#a0c4ff; }
#circle.wake    { background:#0d2a0d; border-color:#4cff72; box-shadow:0 0 80px #4cff72cc; color:#b0ffbb; }
#bar-wrap { width:340px; background:#1a1a1a; border-radius:8px; height:10px; overflow:hidden; }
#bar { height:100%; width:0%; background:#4f8ef7; border-radius:8px; transition:width .07s,background .07s; }
#bar.wake { background:#4cff72; }
#score { font-size:14px; color:#555; letter-spacing:1px; }
#score span { color:#888; font-variant-numeric:tabular-nums; }
#info { font-size:11px; color:#333; }
</style>
</head>
<body>
<div id="circle">listening</div>
<div id="bar-wrap"><div id="bar"></div></div>
<div id="score">score: <span id="sv">0.0000</span></div>
<div id="info">threshold: THRESH_VAL &nbsp;·&nbsp; model: MODEL_NAME</div>
<script>
const thresh = THRESH_JS;
const circle = document.getElementById('circle');
const bar    = document.getElementById('bar');
const sv     = document.getElementById('sv');
let timer = null;
const es = new EventSource('/stream');
es.onmessage = e => {
  const {score, detected} = JSON.parse(e.data);
  sv.textContent = score.toFixed(4);
  bar.style.width = Math.min(score/thresh*100, 100) + '%';
  if (detected) {
    circle.className = 'wake'; circle.textContent = 'sunday!';
    bar.className = 'wake';
    clearTimeout(timer);
    timer = setTimeout(() => {
      circle.className = score > 0.05 ? 'active' : '';
      circle.textContent = 'listening'; bar.className = '';
    }, 1000);
  } else if (score > 0.05) {
    if (circle.className !== 'wake') circle.className = 'active';
  } else {
    if (circle.className !== 'wake') { circle.className = ''; circle.textContent = 'listening'; }
  }
};
</script>
</body>
</html>
""".replace("THRESH_VAL", str(THRESHOLD)).replace("THRESH_JS", str(THRESHOLD)).replace("MODEL_NAME", Path(MODEL_PATH).name)

def broadcast(data):
    dead = []
    with clients_lock:
        for q in clients:
            try: q.put_nowait(data)
            except: dead.append(q)
        for q in dead: clients.remove(q)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/":
            self.send_response(200); self.send_header("Content-Type","text/html"); self.end_headers()
            self.wfile.write(HTML.encode())
        elif self.path == "/stream":
            import queue as Q
            q = Q.Queue(maxsize=40)
            with clients_lock: clients.append(q)
            self.send_response(200)
            self.send_header("Content-Type","text/event-stream")
            self.send_header("Cache-Control","no-cache")
            self.send_header("Access-Control-Allow-Origin","*")
            self.end_headers()
            try:
                while True:
                    try:
                        self.wfile.write(f"data: {q.get(timeout=5)}\n\n".encode()); self.wfile.flush()
                    except:
                        self.wfile.write(b": keepalive\n\n"); self.wfile.flush()
            except:
                with clients_lock:
                    if q in clients: clients.remove(q)
        else:
            self.send_response(404); self.end_headers()

def detection_loop():
    mel_sess = ort.InferenceSession(str(OWW / "melspectrogram.onnx"))
    emb_sess = ort.InferenceSession(str(OWW / "embedding_model.onnx"))
    cls_sess = ort.InferenceSession(MODEL_PATH)
    input_name = cls_sess.get_inputs()[0].name
    print(f"[WakeUI] Model loaded: {Path(MODEL_PATH).name} (input={input_name})")

    cmd = ["arecord", "-D", "plughw:A21", "-r", "16000", "-c", "1", "-f", "S16_LE", "-t", "raw", "-q"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    print("[WakeUI] Mic open, streaming...")

    mel_buf, emb_buf = [], []
    frame = 0
    try:
        while True:
            raw = proc.stdout.read(CHUNK * 2)
            if not raw: break
            audio = np.frombuffer(raw, dtype=np.int16).copy()
            af = audio.astype(np.float32) / 32768.0
            mel_out = mel_sess.run(None, {"input": af.reshape(1, -1)})[0]
            mel_buf.extend(mel_out[0, 0, :, :].tolist())
            if len(mel_buf) >= MEL_WINDOW:
                window = np.array(mel_buf[-MEL_WINDOW:], dtype=np.float32).reshape(1, MEL_WINDOW, MEL_BINS, 1)
                emb = emb_sess.run(None, {"input_1": window})[0].reshape(EMB_DIM)
                emb_buf.append(emb)
            if len(emb_buf) >= EMB_WINDOW:
                feat = np.stack(emb_buf[-EMB_WINDOW:]).reshape(1, EMB_WINDOW, EMB_DIM).astype(np.float32)
                score = float(cls_sess.run(None, {input_name: feat})[0].flat[0])
                frame += 1
                if frame <= 15: continue   # warmup
                if score >= 0.3:
                    print(f"[WakeUI] score={score:.3f}")
                broadcast(json.dumps({"score": round(score, 4), "detected": score >= THRESHOLD}))
    finally:
        proc.kill(); proc.wait()

if __name__ == "__main__":
    print(f"[WakeUI] Open: http://0.0.0.0:{PORT}")
    t = threading.Thread(target=detection_loop, daemon=True)
    t.start()
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
