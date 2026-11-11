"""Tiny retrieval-test UI. Standalone — does not touch Sunday's main process.

Run on Pi:  /home/djpi/momo-env/bin/python /home/djpi/sunday/retrieval_test_server.py
Visit:      http://djpi.local:8090
"""
import sys
from pathlib import Path

sys.path.insert(0, "/home/djpi/sunday")
from aiohttp import web
import wiki_io
from config import Config

PORT = 8090

HTML = """<!doctype html>
<html><head>
<meta charset="utf-8"><title>Sunday — Retrieval Test</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; max-width: 1000px; margin: 2em auto; padding: 0 1em; color: #222; }
  h1 { font-size: 1.4em; }
  textarea { width: 100%; height: 80px; font-size: 1.05em; padding: .6em; border: 1px solid #ccc; border-radius: 6px; box-sizing: border-box; }
  button { padding: .6em 1.4em; font-size: 1em; border: 0; border-radius: 6px; background: #2563eb; color: white; cursor: pointer; margin-top: .6em; }
  button:disabled { background: #93c5fd; cursor: wait; }
  .result { margin-top: 1.5em; }
  .section { background: #f5f7fa; border-radius: 6px; padding: 1em; margin-bottom: 1em; }
  .section h2 { font-size: 1em; margin: 0 0 .8em 0; color: #2563eb; }
  .pages { display: flex; gap: .4em; flex-wrap: wrap; }
  .page-chip { background: #2563eb; color: white; padding: .25em .7em; border-radius: 999px; font-size: .85em; }
  .distilled { background: #ecfdf5; border-left: 4px solid #10b981; padding: 1em; border-radius: 6px; margin-top: .5em; }
  .distilled pre { white-space: pre-wrap; font-family: ui-monospace, monospace; font-size: .85em; margin: 0; }
  details { margin-top: .5em; }
  details summary { cursor: pointer; color: #666; font-size: .85em; }
  .page-content { margin-top: .8em; padding: .8em; background: white; border: 1px solid #e5e7eb; border-radius: 6px; }
  .page-content h3 { margin: 0 0 .4em 0; font-size: .85em; color: #2563eb; }
  .page-content pre { white-space: pre-wrap; font-family: ui-monospace, monospace; font-size: .8em; margin: 0; color: #444; }
  .meta { color: #666; font-size: .8em; margin-bottom: .8em; }
  .empty { color: #888; font-style: italic; }
  input { padding: .4em; font-size: .9em; border: 1px solid #ccc; border-radius: 4px; width: 60px; }
</style>
</head><body>
  <h1>Sunday — Retrieval Test</h1>
  <p style="color:#666; font-size:.9em;">
    Stage 1 (<code>gpt-5.4-nano</code>): router picks pages from INDEX.<br>
    Stage 2 (<code>gpt-5.4-nano</code>): extractor distils picked pages to only relevant bullets — this is what gets injected.
  </p>
  <textarea id="q" placeholder="e.g. movie time, what's my AC default temp, dim the moon"></textarea>
  <div style="margin-top:.4em;">
    <label style="font-size:.85em;color:#666;">Max pages: <input type="number" id="max" value="3" min="1" max="7"></label>
    <button id="go" onclick="run()">Retrieve</button>
  </div>
  <div id="out"></div>
<script>
async function run() {
  const q = document.getElementById('q').value.trim();
  if (!q) return;
  const max = parseInt(document.getElementById('max').value) || 3;
  const btn = document.getElementById('go');
  const out = document.getElementById('out');
  btn.disabled = true; btn.textContent = 'Retrieving…';
  out.innerHTML = '';
  try {
    const t0 = performance.now();
    const r = await fetch('/api/retrieve', {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({text: q, max_pages: max})
    });
    const data = await r.json();
    const dt = Math.round(performance.now() - t0);

    let html = '<div class="result">';

    // Stage 1: pages picked
    html += '<div class="section"><h2>Stage 1 · Router picked pages</h2>';
    html += `<div class="meta">⏱️ ${data.t_router} ms</div>`;
    if (!data.pages || data.pages.length === 0) {
      html += '<div class="empty">No pages matched.</div>';
    } else {
      html += '<div class="pages">' + data.pages.map(p => `<span class="page-chip">${p}</span>`).join('') + '</div>';
    }
    html += '</div>';

    // Stage 2: distilled
    html += '<div class="section"><h2>Stage 2 · Distilled snippets <span style="font-weight:normal;font-size:.75em;color:#666;">(what Sunday actually receives)</span></h2>';
    html += `<div class="meta">⏱️ ${data.t_extract} ms · ${(data.distilled || '').length} chars</div>`;
    if (!data.distilled) {
      html += '<div class="empty">Nothing distilled — empty injection.</div>';
    } else {
      html += `<div class="distilled"><pre>${data.distilled.replace(/</g,'&lt;')}</pre></div>`;
    }
    html += '</div>';

    // Optional: original full pages
    if (data.contents && Object.keys(data.contents).length) {
      html += '<details><summary>Show full source pages (for comparison)</summary>';
      for (const [name, body] of Object.entries(data.contents)) {
        html += `<div class="page-content"><h3>${name}</h3><pre>${body.replace(/</g,'&lt;')}</pre></div>`;
      }
      html += '</details>';
    }

    html += `<div class="meta" style="margin-top:.5em;">Total ${dt} ms</div>`;
    html += '</div>';
    out.innerHTML = html;
  } catch (e) {
    out.innerHTML = `<div class="section" style="background:#fef2f2;color:#991b1b;">Error: ${e}</div>`;
  } finally {
    btn.disabled = false; btn.textContent = 'Retrieve';
  }
}
document.getElementById('q').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) run();
});
</script>
</body></html>
"""


async def index(request):
    return web.Response(text=HTML, content_type="text/html")


async def retrieve(request):
    import time
    body = await request.json()
    text = body.get("text", "")
    max_pages = int(body.get("max_pages", 3))

    t0 = time.perf_counter()
    pages = await wiki_io.fetch_relevant_pages(text, max_pages=max_pages)
    t1 = time.perf_counter()
    distilled = await wiki_io.extract_snippets(text, pages) if pages else ""
    t2 = time.perf_counter()

    contents = {}
    for p in pages:
        path = Path("/home/djpi/sunday/wiki") / p
        if path.exists():
            contents[p] = path.read_text().rstrip()

    return web.json_response({
        "pages": pages,
        "distilled": distilled,
        "contents": contents,
        "t_router": round((t1 - t0) * 1000),
        "t_extract": round((t2 - t1) * 1000),
    })


def main():
    cfg = Config.load("/home/djpi/sunday/config.json")
    if not cfg.openai_api_key:
        print("FAIL: no openai_api_key in config")
        sys.exit(1)
    wiki_io.init(cfg.openai_api_key)
    print(f"[retrieval-test] init OK; serving on http://0.0.0.0:{PORT}")
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_post("/api/retrieve", retrieve)
    web.run_app(app, host="0.0.0.0", port=PORT, print=lambda _: None)


if __name__ == "__main__":
    main()
