"""Side-by-side comparison page for two wake word models."""
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 7779

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
  .frames {
    display: flex;
    flex: 1;
    gap: 1px;
    background: #111;
    padding: 1px;
  }
  iframe {
    flex: 1;
    border: none;
    background: #0a0a0a;
  }
</style>
</head>
<body>
<header>
  <div class="label"><strong>sunday.onnx</strong>old model</div>
  <div class="label"><strong>sun_day.onnx</strong>new model</div>
</header>
<div class="frames">
  <iframe src="http://localhost:7777"></iframe>
  <iframe src="http://localhost:7778"></iframe>
</div>
</body>
</html>
"""

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(HTML.encode())

if __name__ == "__main__":
    print(f"[Compare] Open: http://localhost:{PORT}")
    HTTPServer(("", PORT), Handler).serve_forever()
