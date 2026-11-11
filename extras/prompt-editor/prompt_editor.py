#!/usr/bin/env python3
"""Sunday prompt editor: a small web page to edit and save Sunday's prompt files.

  http://<pi>:8095/      (no login by default: it only answers devices on the home network; set SPE_AUTH=token to require the token file)

What it protects you from:
  * every save first copies the previous version to a history folder (restore any time, diff against any version)
  * if the file changed on disk after you opened it (e.g. another agent edited it), the save is refused instead of overwriting
  * writes are atomic (a crash never leaves a half-written prompt); only the whitelisted files can be touched
  * "Save & apply" restarts the brain ONLY when Sunday is not in a conversation (the brain reads the prompt once, at start)

Config by environment: SPE_PROMPT_DIR, SPE_HISTORY_DIR, SPE_TOKEN_FILE, SPE_HOST, SPE_PORT, SPE_BRAIN_HEALTH, SPE_BRAIN_UNIT.
"""
import asyncio
import difflib
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import ClientSession, ClientTimeout, web

HOME = Path.home()
FILES = {
    "SYSTEM_PROMPT.md": "Main persona and rules — used for text and voice",
    "VOICE_PROMPT.md": "Extra rules that apply only to spoken replies",
}
MAX_BYTES = 200_000
SESSION_SECONDS = 12 * 3600
KEY_CFG = web.AppKey("cfg", dict)
KEY_SESS = web.AppKey("sessions", dict)
KEY_FAILS = web.AppKey("fails", dict)


def default_cfg():
    e = os.environ.get
    return {
        "prompt_dir": Path(e("SPE_PROMPT_DIR", HOME / "sunday2-rebuild" / "docs")),
        "history_dir": Path(e("SPE_HISTORY_DIR", HOME / "sunday-prompt-editor" / "history")),
        "token_file": Path(e("SPE_TOKEN_FILE", HOME / "sunday-prompt-editor" / "token")),
        "host": e("SPE_HOST", "0.0.0.0"),
        "port": int(e("SPE_PORT", "8095")),
        "brain_health": e("SPE_BRAIN_HEALTH", "http://127.0.0.1:8090/health"),
        "brain_unit": e("SPE_BRAIN_UNIT", "sunday2.service"),
        "auth": e("SPE_AUTH", "none"),            # "none" (home network only, no login) or "token" (login with the token file)
        "allowed_hosts": set(e("SPE_ALLOWED_HOSTS", "localhost,djpi,djpi.local").lower().split(",")),
        "restart_cmd": None,                      # tests inject a fake; None = real `sudo -n systemctl restart <unit>`
    }


def etag_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def ensure_token(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    if not path.exists():
        path.write_text(secrets.token_urlsafe(24) + "\n")
        os.chmod(path, 0o600)
    return path.read_text().strip()


def safe_name(name):
    if name not in FILES:
        raise web.HTTPBadRequest(text=json.dumps({"error": "unknown file"}), content_type="application/json")
    return name


def jerr(status, msg, **extra):
    return web.json_response(dict(error=msg, **extra), status=status)


# ------------------------------------------------------------------ auth
def remote_allowed(remote):
    """Only devices on the home network (private / loopback / link-local addresses) may use the editor."""
    try:
        ip = ipaddress.ip_address(remote)
    except (ValueError, TypeError):
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local


def host_allowed(host_header, cfg):
    """Blocks DNS-rebinding: the Host header must be this machine's IP address or one of its known names."""
    h = (host_header or "").strip().lower()
    h = h[1:h.index("]")] if h.startswith("[") else h.split(":")[0]
    if h in cfg["allowed_hosts"]:
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


@web.middleware
async def auth_mw(request, handler):
    cfg = request.app[KEY_CFG]
    if not remote_allowed(request.remote) or not host_allowed(request.headers.get("Host"), cfg):
        return jerr(403, "not allowed from here")
    if request.method not in ("GET", "HEAD"):
        if request.headers.get("X-Requested-With") != "sunday-editor":
            return jerr(403, "missing header")                       # a plain cross-site form/fetch cannot set this header
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return jerr(403, "cross-site request refused")
    if cfg["auth"] != "token" or request.path in ("/", "/login", "/healthz"):
        return await handler(request)
    sid = request.cookies.get("spe")
    exp = request.app[KEY_SESS].get(sid)
    if not exp or exp < time.time():
        return jerr(401, "not logged in")
    return await handler(request)


async def login(request):
    if request.app[KEY_CFG]["auth"] != "token":
        return web.json_response({"ok": True, "login": "not required"})
    ip = request.remote or "?"
    fails = request.app[KEY_FAILS]
    recent = [t for t in fails.get(ip, []) if t > time.time() - 60]
    if len(recent) >= 5:
        return jerr(429, "too many attempts, wait a minute")
    try:
        data = await request.json()
    except Exception:
        return jerr(400, "bad request")
    token = ensure_token(request.app[KEY_CFG]["token_file"])
    if not hmac.compare_digest(str(data.get("token", "")), token):
        fails[ip] = recent + [time.time()]
        await asyncio.sleep(0.8)
        return jerr(401, "wrong token")
    sid = secrets.token_urlsafe(24)
    request.app[KEY_SESS][sid] = time.time() + SESSION_SECONDS
    resp = web.json_response({"ok": True})
    resp.set_cookie("spe", sid, max_age=SESSION_SECONDS, httponly=True, samesite="Strict", path="/")
    return resp


async def logout(request):
    request.app[KEY_SESS].pop(request.cookies.get("spe"), None)
    resp = web.json_response({"ok": True}); resp.del_cookie("spe", path="/")
    return resp


# ------------------------------------------------------------------ files and history
def prompt_path(cfg, name):
    return cfg["prompt_dir"] / safe_name(name)


def list_history(cfg, name):
    d = cfg["history_dir"] / safe_name(name)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.md"), reverse=True):
        out.append({"id": p.stem, "size": p.stat().st_size, "saved_at": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")})
    return out


def backup_current(cfg, name, data: bytes) -> str:
    d = cfg["history_dir"] / name
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(cfg["history_dir"], 0o700); os.chmod(d, 0o700)
    bid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + etag_of(data)[:8]
    (d / (bid + ".md")).write_bytes(data)
    return bid


def atomic_write(path: Path, data: bytes):
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    tmp = path.with_name(path.name + ".editor-tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def brain_start_time():
    try:
        out = subprocess.run(["systemctl", "show", "-p", "ActiveEnterTimestamp", "sunday2.service"], capture_output=True, text=True, timeout=5).stdout
        stamp = out.split("=", 1)[1].strip().split()      # e.g. Thu 2026-10-29 09:52:22 IST
        return datetime.strptime(" ".join(stamp[1:3]), "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        return None


async def files(request):
    cfg = request.app[KEY_CFG]
    started = cfg.get("brain_started_override", None) if "brain_started_override" in cfg else brain_start_time()
    out = []
    for name, desc in FILES.items():
        p = prompt_path(cfg, name)
        st = p.stat()
        out.append({"name": name, "description": desc, "size": st.st_size, "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "versions": len(list_history(cfg, name)), "applied": (started is None) or st.st_mtime <= started})
    return web.json_response({"files": out})


async def get_file(request):
    cfg = request.app[KEY_CFG]; name = safe_name(request.query.get("name", ""))
    data = prompt_path(cfg, name).read_bytes()
    return web.json_response({"name": name, "content": data.decode("utf-8", "replace"), "etag": etag_of(data)})


def validate(content):
    if not isinstance(content, str):
        raise ValueError("content must be text")
    raw = content.replace("\r\n", "\n").encode("utf-8")
    if not raw.strip():
        raise ValueError("a prompt cannot be empty")
    if b"\x00" in raw:
        raise ValueError("binary data in prompt")
    if len(raw) > MAX_BYTES:
        raise ValueError("prompt is larger than %d KB" % (MAX_BYTES // 1000))
    return raw


async def save(request):
    cfg = request.app[KEY_CFG]
    try:
        d = await request.json(); name = safe_name(d.get("name", ""))
        raw = validate(d.get("content"))
    except ValueError as e:
        return jerr(400, str(e))
    p = prompt_path(cfg, name)
    cur = p.read_bytes()
    if etag_of(cur) != d.get("etag"):
        return jerr(409, "The file changed on disk after you opened it (another editor or agent). Reload it, re-apply your edit, and save again.", etag=etag_of(cur))
    if raw == cur:
        return web.json_response({"ok": True, "unchanged": True, "etag": etag_of(cur)})
    bid = backup_current(cfg, name, cur)
    atomic_write(p, raw)
    print("saved", name, len(raw), "bytes; previous version kept as", bid, flush=True)
    return web.json_response({"ok": True, "etag": etag_of(raw), "backup": bid})


async def history(request):
    return web.json_response({"versions": list_history(request.app[KEY_CFG], request.query.get("name", ""))})


def read_version(cfg, name, vid):
    if not vid.replace("-", "").isalnum():
        raise web.HTTPBadRequest(text=json.dumps({"error": "bad version id"}), content_type="application/json")
    p = cfg["history_dir"] / safe_name(name) / (vid + ".md")
    if not p.is_file():
        raise web.HTTPNotFound(text=json.dumps({"error": "no such version"}), content_type="application/json")
    return p.read_bytes()


async def diff(request):
    cfg = request.app[KEY_CFG]; name = safe_name(request.query.get("name", ""))
    old = read_version(cfg, name, request.query.get("id", "")).decode("utf-8", "replace").splitlines()
    new = prompt_path(cfg, name).read_text(errors="replace").splitlines()
    lines = list(difflib.unified_diff(old, new, "that version", "current file", lineterm="", n=2))
    return web.json_response({"diff": "\n".join(lines) if lines else "(identical)"})


async def restore(request):
    cfg = request.app[KEY_CFG]; d = await request.json(); name = safe_name(d.get("name", ""))
    old = read_version(cfg, name, d.get("id", ""))
    p = prompt_path(cfg, name); cur = p.read_bytes()
    if etag_of(cur) != d.get("etag"):
        return jerr(409, "The file changed on disk. Reload first.", etag=etag_of(cur))
    bid = backup_current(cfg, name, cur)
    atomic_write(p, old)
    print("restored", name, "from", d.get("id"), "; previous kept as", bid, flush=True)
    return web.json_response({"ok": True, "etag": etag_of(old), "backup": bid})


# ------------------------------------------------------------------ apply (restart the brain when idle)
async def brain_health(cfg):
    try:
        async with ClientSession(timeout=ClientTimeout(total=4)) as s:
            async with s.get(cfg["brain_health"]) as r:
                return await r.json(content_type=None)
    except Exception:
        return None


async def status(request):
    cfg = request.app[KEY_CFG]; h = await brain_health(cfg)
    return web.json_response({"brain": (h or {}).get("status"), "active_turns": (h or {}).get("active_turns"), "reachable": h is not None})


async def apply(request):
    cfg = request.app[KEY_CFG]
    h = await brain_health(cfg)
    if h is None:
        return jerr(503, "Cannot reach the brain, so I did not restart anything.")
    if h.get("active_turns", 1) != 0:
        return jerr(409, "Sunday is in a conversation right now — try again in a moment.")
    cmd = cfg["restart_cmd"] or ["sudo", "-n", "systemctl", "restart", cfg["brain_unit"]]
    r = await asyncio.get_running_loop().run_in_executor(None, lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=60))
    if r.returncode != 0:
        return jerr(500, "restart failed: " + (r.stderr or r.stdout).strip()[:200])
    for _ in range(40):                                        # wait up to ~40 s for the brain to be ready again
        await asyncio.sleep(1)
        h2 = await brain_health(cfg)
        if h2 and h2.get("status") == "ready":
            return web.json_response({"ok": True, "restarted": True})
    return jerr(504, "Restarted, but the brain did not report ready within 40 s — check it.")


# ------------------------------------------------------------------ page
PAGE = r"""<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Sunday prompt editor</title>
<style>
:root{--bg:#0e1020;--card:#171a30;--ink:#e8ebff;--mute:#8e95c0;--line:#2a2f57;--acc:#7c8cff;--ok:#5ad19a;--warn:#ffb454;--bad:#ff6b7a}
@media(prefers-color-scheme:light){:root{--bg:#f4f5fb;--card:#fff;--ink:#1b1d33;--mute:#5d6491;--line:#d9dcf0;--acc:#4a5bff;--ok:#12805a;--warn:#a96600;--bad:#c4243a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,sans-serif}
header{display:flex;gap:12px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line);flex-wrap:wrap}
h1{font-size:17px;margin:0 8px 0 0}.pill{padding:2px 10px;border-radius:99px;font-size:12px;border:1px solid var(--line);color:var(--mute)}
main{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:14px;padding:14px 16px;max-width:1300px;margin:auto}
@media(max-width:900px){main{grid-template-columns:1fr}}
.tabs{display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap}.tab{padding:7px 12px;border:1px solid var(--line);border-radius:9px;background:var(--card);color:var(--ink);cursor:pointer}
.tab.on{border-color:var(--acc);outline:1px solid var(--acc)}.tab small{display:block;color:var(--mute);font-size:11px}
textarea{width:100%;height:68vh;resize:vertical;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:12px;font:13.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;tab-size:2}
.bar{display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap}
button{padding:8px 14px;border-radius:9px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer;font:inherit}
button.p{background:var(--acc);border-color:var(--acc);color:#fff}button:disabled{opacity:.45;cursor:default}
#msg{margin-left:auto;font-size:13px}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.mute{color:var(--mute)}
aside{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;align-self:start}
aside h3{margin:0 0 8px;font-size:14px}.ver{display:flex;gap:6px;align-items:center;justify-content:space-between;padding:6px 0;border-top:1px solid var(--line);font-size:12.5px}
.ver:first-of-type{border-top:0}.ver button{padding:3px 8px;font-size:12px}
pre#diff{white-space:pre-wrap;font:12px/1.45 ui-monospace,monospace;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px;max-height:40vh;overflow:auto;display:none}
.a{color:var(--ok)}.d{color:var(--bad)}.login{max-width:360px;margin:18vh auto;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px}
input[type=password]{width:100%;padding:10px;border-radius:9px;border:1px solid var(--line);background:var(--bg);color:var(--ink);margin:10px 0}
</style>
<div id=app></div>
<script>
const $=s=>document.querySelector(s),H={'X-Requested-With':'sunday-editor','Content-Type':'application/json'};
let cur=null,etag=null,orig='',files=[];
async function api(p,o={}){const r=await fetch(p,{credentials:'same-origin',headers:H,...o});let j={};try{j=await r.json()}catch(e){}if(r.status===401){loginView();throw 0}return{ok:r.ok,status:r.status,j}}
function say(t,c){const m=$('#msg');if(m){m.textContent=t;m.className=c||''}}
function loginView(){$('#app').innerHTML=`<div class=login><h1>Sunday prompt editor</h1><div class=mute>Enter the access token (the file <code>~/sunday-prompt-editor/token</code> on the Pi).</div>
<input id=tk type=password placeholder=token autofocus><button class=p id=go>Open</button><div id=lm class=bad></div></div>`;
 const go=async()=>{const r=await fetch('/login',{method:'POST',headers:H,body:JSON.stringify({token:$('#tk').value})});if(r.ok)start();else $('#lm').textContent=(await r.json()).error}
 $('#go').onclick=go;$('#tk').onkeydown=e=>e.key==='Enter'&&go()}
async function start(){
 $('#app').innerHTML=`<header><h1>Sunday prompt editor</h1><span class=pill id=brain>brain …</span><span class=pill id=pend></span><span style=flex:1></span><button id=out style=display:none>Log out</button></header>
 <main><section><div class=tabs id=tabs></div><textarea id=ed spellcheck=false></textarea>
 <div class=bar><button class=p id=save disabled>Save</button><button id=sa disabled>Save &amp; apply</button><button id=rev disabled>Revert</button><span class=mute id=cnt></span><span id=msg></span></div>
 <div class="mute" style="margin-top:8px;font-size:12.5px">The brain reads the prompt only when it starts. <b>Save</b> keeps the file (and a backup); <b>Save &amp; apply</b> also restarts the brain — only when Sunday is not in a conversation.</div></section>
 <aside><h3>Earlier versions</h3><div id=hist class=mute>—</div><pre id=diff></pre></aside></main>`;
 fetch('/api/status').then(()=>{});$('#out').onclick=async()=>{await api('/logout',{method:'POST'});loginView()};
 $('#ed').oninput=dirty;$('#save').onclick=()=>doSave(false);$('#sa').onclick=()=>doSave(true);$('#rev').onclick=()=>open(cur);
 addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key==='s'){e.preventDefault();if(!$('#save').disabled)doSave(false)}});
 addEventListener('beforeunload',e=>{if($('#ed')&&$('#ed').value!==orig){e.preventDefault();e.returnValue=''}});
 await refresh();await open(files[0].name);setInterval(status,15000);status()}
async function refresh(){const r=await api('/api/files');files=r.j.files;
 $('#tabs').innerHTML=files.map(f=>`<div class="tab ${f.name===cur?'on':''}" data-n="${f.name}">${f.name}<small>${f.description}${f.applied?'':' · <b class=warn>saved, not applied</b>'}</small></div>`).join('');
 document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{if($('#ed').value!==orig&&!confirm('Discard unsaved changes?'))return;open(t.dataset.n)});
 const n=files.filter(f=>!f.applied).length;$('#pend').textContent=n?n+' file(s) saved but not applied yet':'all saved changes are applied';$('#pend').className='pill '+(n?'warn':'')}
async function open(name){const r=await api('/api/file?name='+encodeURIComponent(name));cur=name;etag=r.j.etag;orig=r.j.content;$('#ed').value=orig;
 document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('on',t.dataset.n===name));dirty();say('');$('#diff').style.display='none';loadHist()}
function dirty(){const d=$('#ed').value!==orig;$('#save').disabled=$('#sa').disabled=$('#rev').disabled=!d;const v=$('#ed').value;$('#cnt').textContent=v.split('\n').length+' lines · '+v.length+' characters'+(d?' · unsaved changes':'');}
async function doSave(apply){say('saving…','mute');const r=await api('/api/save',{method:'POST',body:JSON.stringify({name:cur,content:$('#ed').value,etag})});
 if(!r.ok){say(r.j.error||'save failed','bad');return}
 etag=r.j.etag;orig=$('#ed').value;dirty();say(r.j.unchanged?'no changes to save':'saved (previous version kept)','ok');await refresh();await loadHist();
 if(apply){say('restarting the brain…','mute');const a=await api('/api/apply',{method:'POST',body:'{}'});say(a.ok?'saved and applied — the brain restarted':(a.j.error||'apply failed'),a.ok?'ok':'bad');await refresh()}}
async function loadHist(){const r=await api('/api/history?name='+encodeURIComponent(cur));const v=r.j.versions;
 $('#hist').innerHTML=v.length?v.map(x=>`<div class=ver><span>${x.saved_at}<br><span class=mute>${x.size} B</span></span><span><button data-d="${x.id}">Diff</button> <button data-r="${x.id}">Restore</button></span></div>`).join(''):'No earlier versions yet — the first save creates one.';
 document.querySelectorAll('[data-d]').forEach(b=>b.onclick=()=>showDiff(b.dataset.d));document.querySelectorAll('[data-r]').forEach(b=>b.onclick=()=>restore(b.dataset.r))}
async function showDiff(id){const r=await api('/api/diff?name='+encodeURIComponent(cur)+'&id='+id);const p=$('#diff');p.style.display='block';
 p.innerHTML=r.j.diff.split('\n').map(l=>{l=l.replace(/&/g,'&amp;').replace(/</g,'&lt;');return l.startsWith('+')&&!l.startsWith('+++')?'<span class=a>'+l+'</span>':l.startsWith('-')&&!l.startsWith('---')?'<span class=d>'+l+'</span>':l}).join('\n')}
async function restore(id){if(!confirm('Restore this version? The current file is backed up first.'))return;if($('#ed').value!==orig&&!confirm('You have unsaved edits that will be lost. Continue?'))return;
 const r=await api('/api/restore',{method:'POST',body:JSON.stringify({name:cur,id,etag})});if(!r.ok){say(r.j.error,'bad');return}await open(cur);await refresh();say('restored — use Save & apply to put it in use','ok')}
async function status(){const r=await api('/api/status');const b=$('#brain');if(!b)return;b.textContent=!r.j.reachable?'brain unreachable':(r.j.active_turns?'brain busy (in a conversation)':'brain ready');b.className='pill '+(r.j.reachable&&!r.j.active_turns?'ok':'warn')}
api('/api/files').then(start).catch(()=>{});
</script></html>"""


async def index(request):
    return web.Response(text=PAGE, content_type="text/html", headers={"Cache-Control": "no-store", "X-Frame-Options": "SAMEORIGIN"})


async def healthz(request):
    return web.json_response({"ok": True})


def make_app(cfg=None):
    app = web.Application(middlewares=[auth_mw], client_max_size=MAX_BYTES * 2)
    app[KEY_CFG] = cfg or default_cfg(); app[KEY_SESS] = {}; app[KEY_FAILS] = {}
    app.router.add_get("/", index); app.router.add_get("/healthz", healthz)
    app.router.add_post("/login", login); app.router.add_post("/logout", logout)
    app.router.add_get("/api/files", files); app.router.add_get("/api/file", get_file); app.router.add_post("/api/save", save)
    app.router.add_get("/api/history", history); app.router.add_get("/api/diff", diff); app.router.add_post("/api/restore", restore)
    app.router.add_get("/api/status", status); app.router.add_post("/api/apply", apply)
    return app


def main():
    cfg = default_cfg()
    if cfg["auth"] == "token":
        ensure_token(cfg["token_file"])
    print("prompt editor on http://%s:%d  (auth: %s)" % (cfg["host"], cfg["port"], cfg["auth"]), flush=True)
    web.run_app(make_app(cfg), host=cfg["host"], port=cfg["port"], print=None)


if __name__ == "__main__":
    main()
