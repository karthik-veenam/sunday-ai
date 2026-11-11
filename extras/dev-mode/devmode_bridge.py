"""Sunday Dev Mode bridge: browser page <-> Codex app-server (unix socket WebSocket).

Standalone service; does not import or touch ~/sunday/main.py.
Run: ~/momo-env/bin/python ~/sunday-dev-mode/devmode_bridge.py
"""
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

import aiohttp
from aiohttp import web
from devmode_speech import Speech
from devmode_usage import project_limits

HERE = Path(__file__).resolve().parent
SOCK = os.path.expanduser("~/.codex/app-server-control/app-server-control.sock")
SNAPSHOT = Path(os.path.expanduser(
    "~/sunday2-rebuild/artifacts/sunday-architecture/architecture-snapshot.json"))
STATIC = HERE / "static"
DISPLAY = HERE / "display"          # Codex writes current.html here; the page shows it on the right
DISPLAY.mkdir(exist_ok=True)
PORT = int(os.environ.get("DEVMODE_PORT", "8775"))
CWD = os.environ.get("DEVMODE_CWD", os.path.expanduser("~/sunday2-rebuild"))

log = logging.getLogger("devmode")

LISTEN_UNIT = "sunday-devmode-listen.service"   # mic listener: runs only while dev mode is listening
# Spoken control phrases (matched on the transcript before it reaches Codex).
END_RE = re.compile(r"\b(?:end|exit|stop|close|quit|leave)\s+dev(?:elopment)?\s*mode\b", re.I)
PAUSE_RE = re.compile(r"\b(?:stop|pause)\s+listening\b|\bdon'?t\s+listen\b", re.I)
mode = {"on": False, "listening": False, "since": None}
LISTEN_DELAY = float(os.environ.get("DEVMODE_LISTEN_DELAY", "6"))   # let Sunday finish speaking before the mic opens
_listen_task = None
FIRETV = os.environ.get("DEVMODE_FIRETV", "192.168.88.35:5555")       # the projector's Fire TV (same ADB server as production)
PAGE_URL = os.environ.get("DEVMODE_PAGE", "http://192.168.88.108:8775/")
SILK = "com.amazon.cloud9/com.google.android.apps.chrome.IntentDispatcher"

DISPLAY_NOTE = f"""You are running inside Sunday Dev Mode. The user is watching a large projector screen. \
The left half shows this chat. The RIGHT half is a display that you control completely.
To show something there, write a single self-contained HTML file to {DISPLAY}/current.html \
(inline CSS and JS, no external network, no relative links). The screen refreshes within a second of each write. \
Use it freely whenever a visual helps: diagrams, SVG, tables, code or diff views, plans, progress, results, notes. \
Design for a 1920x1080 projector: dark background (#0b1020), large readable text (18px or more), high contrast. \
Overwrite the file to update or replace what is shown. Do not ask permission to use the display. \
Keep chat replies short because the display is where detail goes."""

# Codex runs hands-off with full access, so every request must carry this token (?t=...).
TOKEN_FILE = HERE / "token"
TOKEN = TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else ""

# Notifications worth forwarding to the page; deltas stream the chat, items drive the steps list.
FORWARD = {
    "thread/started", "thread/status/changed", "turn/started", "turn/completed",
    "turn/diff/updated", "turn/plan/updated", "item/started", "item/completed",
    "item/agentMessage/delta", "item/commandExecution/outputDelta",
    "item/fileChange/patchUpdated", "item/reasoning/summaryTextDelta", "error", "warning",
}


class Codex:
    """One long-lived connection to the app-server, one working thread."""

    def __init__(self):
        self.ws = None
        self.thread_id = None
        self.next_id = 1
        self.pending = {}
        self.clients = set()
        self.ready = asyncio.Event()
        self.usage = {"type": "usage", "windows": [], "available": False, "stale": True}
        self.display_v = 0
        self.turn_id = None

    async def run(self):
        while True:
            try:
                await self._session()
            except Exception as e:  # reconnect forever; the daemon may restart
                log.warning("codex connection lost: %s", e)
            self.ready.clear()
            self.ws = None
            self.thread_id = None
            self.turn_id = None
            for fut in self.pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("codex disconnected"))
            self.pending.clear()
            await self.broadcast({"type": "status", "codex": "disconnected"})
            await asyncio.sleep(3)

    async def _session(self):
        conn = aiohttp.UnixConnector(path=SOCK)
        async with aiohttp.ClientSession(connector=conn) as s:
            async with s.ws_connect("http://localhost/", max_msg_size=16 * 1024 * 1024) as ws:
                self.ws = ws
                reader = asyncio.create_task(self._read(ws))
                await self.request("initialize", {"clientInfo": {
                    "name": "sunday-dev-mode", "title": "Sunday Dev Mode", "version": "0.1"}})
                await ws.send_json({"method": "initialized"})
                handoff = HERE / 'speech-handoff.json'
                if handoff.exists():
                    saved = json.loads(handoff.read_text())
                    if saved.get('thread'):
                        await self.request('thread/resume', {'threadId': saved['thread'], 'cwd': CWD})
                        self.thread_id = saved['thread']
                    mode.update(saved.get('mode', {}))
                    handoff.unlink()
                    if mode['on'] and mode['listening']:
                        await systemctl('start')
                self.ready.set()
                await self.broadcast({"type": "status", "codex": "connected"})
                await reader

    async def _read(self, ws):
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            if "method" in m and "id" in m:
                await self._server_request(m)
            elif "method" in m:
                if m['method'] == 'account/rateLimits/updated':
                    self.usage = project_limits(m.get('params', {}))
                    await self.broadcast(self.usage)
                if m["method"] == "turn/started":
                    self.turn_id = (m.get("params", {}).get("turn") or {}).get("id")
                elif m["method"] == "turn/completed":
                    self.turn_id = None
                if m['method'] == 'item/completed' and mode['on']:
                    speech.enqueue(m.get('params', {}).get('item', {}))
                if m["method"] in FORWARD:
                    await self.broadcast({"type": "codex", "method": m["method"],
                                          "params": m.get("params", {})})
            elif "id" in m:
                fut = self.pending.pop(m["id"], None)
                if fut and not fut.done():
                    if "error" in m:
                        fut.set_exception(RuntimeError(json.dumps(m["error"])))
                    else:
                        fut.set_result(m.get("result"))

    async def _server_request(self, m):
        # Fully hands-off: approve whatever Codex asks for. The sandbox/approval policy in
        # ~/.codex/config.toml is already never/danger-full-access, so this is a safety net.
        log.info("auto-approving %s", m["method"])
        await self.ws.send_json({"id": m["id"], "result": {"decision": "accept"}})
        await self.broadcast({"type": "auto_approved", "method": m["method"]})

    async def request(self, method, params):
        rid = self.next_id
        self.next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        await self.ws.send_json({"id": rid, "method": method, "params": params})
        return await asyncio.wait_for(fut, 30)

    async def ensure_thread(self):
        if self.thread_id:
            return self.thread_id
        r = await self.request("thread/start", {"cwd": CWD, "serviceName": "sunday-dev-mode",
                                                "developerInstructions": DISPLAY_NOTE})
        self.thread_id = r["thread"]["id"]
        await self.broadcast({"type": "thread", "id": self.thread_id})
        return self.thread_id

    async def say(self, text):
        await asyncio.wait_for(self.ready.wait(), 10)
        tid = await self.ensure_thread()
        inp = [{"type": "text", "text": text, "text_elements": []}]
        if self.turn_id:  # Codex is mid-turn: steer it instead of starting a second turn
            await self.request("turn/steer", {"threadId": tid, "expectedTurnId": self.turn_id, "input": inp})
        else:
            await self.request("turn/start", {"threadId": tid, "input": inp})

    async def interrupt(self):
        if self.thread_id:
            await self.request("turn/interrupt", {"threadId": self.thread_id})

    async def broadcast(self, obj):
        for ws in list(self.clients):
            try:
                await ws.send_json(obj)
            except Exception:
                self.clients.discard(ws)


codex = Codex()


async def systemctl(action):
    p = await asyncio.create_subprocess_exec("systemctl", "--user", action, LISTEN_UNIT,
                                             stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    return await p.wait()


async def speech_gate(active):
    if active:
        await systemctl('stop')
    elif mode['on'] and mode['listening']:
        await systemctl('start')


speech = Speech(speech_gate, codex.broadcast)


async def _start_listener_later():
    await asyncio.sleep(LISTEN_DELAY)
    if mode["on"] and mode["listening"] and not speech.speaking:
        rc = await systemctl("start")
        if rc != 0:
            log.warning("systemctl start %s -> %s", LISTEN_UNIT, rc)


async def adb(*args):
    p = await asyncio.create_subprocess_exec("adb", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await asyncio.wait_for(p.communicate(), 20)
    return p.returncode, out.decode(errors="replace")


IDLE_SETTINGS = HERE / 'idle-settings.json'
IDLE_OVERRIDES = {'secure:screensaver_enabled': '0', 'secure:sleep_timeout': '0',
                  'system:screen_off_timeout': '2147483647'}


async def keep_display_awake(enabled):
    """Save original Fire TV idle settings once, restore them when dev mode ends."""
    await adb('connect', FIRETV)
    if enabled:
        if not IDLE_SETTINGS.exists():
            previous = {}
            for setting in IDLE_OVERRIDES:
                namespace, name = setting.split(':')
                rc, value = await adb('-s', FIRETV, 'shell', 'settings', 'get', namespace, name)
                if rc != 0:
                    raise RuntimeError('Cannot read Fire TV idle settings')
                previous[setting] = value.strip()
            IDLE_SETTINGS.write_text(json.dumps(previous))
        values = IDLE_OVERRIDES
    elif IDLE_SETTINGS.exists():
        values = json.loads(IDLE_SETTINGS.read_text())
    else:
        return
    for setting, value in values.items():
        namespace, name = setting.split(':')
        args = ('delete', namespace, name) if value == 'null' else ('put', namespace, name, value)
        rc, output = await adb('-s', FIRETV, 'shell', 'settings', *args)
        if rc != 0 or 'Error' in output or 'Exception' in output:
            raise RuntimeError('Cannot update Fire TV idle settings')
    if not enabled:
        IDLE_SETTINGS.unlink(missing_ok=True)


async def launch_display(wake=True):
    """Wake the Fire TV and open this page in Silk. Output is not logged: the intent echoes the URL, which holds the token."""
    await adb("connect", FIRETV)
    if mode["on"]:
        await keep_display_awake(True)
    if wake:
        await adb("-s", FIRETV, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
        await asyncio.sleep(2)
    rc, out = await adb("-s", FIRETV, "shell",
                        f"am start -a android.intent.action.VIEW -d '{PAGE_URL}?t={TOKEN}' -n {SILK}")
    ok = rc == 0 and "Error" not in out
    log.info("launch_display ok=%s", ok)
    return ok


async def set_mode(on=None, listening=None):
    """Dev mode on/off and mic listening on/off. The mic listener only runs while listening is true."""
    if on is not None:
        mode["on"] = bool(on)
        mode["since"] = time.time() if on else None
        if on and listening is None:
            listening = True
        if not on:
            listening = False
    if listening is not None:
        global _listen_task
        if _listen_task:
            _listen_task.cancel()
            _listen_task = None
        mode["listening"] = bool(listening) and mode["on"]
        if mode["listening"]:
            _listen_task = asyncio.create_task(_start_listener_later())
        else:
            await systemctl("stop")
    if on is False:
        try:
            await keep_display_awake(False)
        except Exception as error:
            log.warning('Restoring Fire TV idle settings failed: %s', error)
    await codex.broadcast({"type": "devmode", **mode})


async def handle_voice(text, final):
    """A transcript from the mic listener. Partials only feed the live transcript bar."""
    if not (mode["on"] and mode["listening"]):
        return {"ok": False, "reason": "not listening"}
    if speech.speaking:
        return {'ok': False, 'reason': 'speaker active'}
    await codex.broadcast({"type": "heard", "text": text, "final": final})
    if not final:
        return {"ok": True}
    if END_RE.search(text):
        await set_mode(on=False)
        return {"ok": True, "action": "end"}
    if PAUSE_RE.search(text):
        await set_mode(listening=False)
        return {"ok": True, "action": "pause"}
    await codex.broadcast({"type": "you", "text": text})
    await codex.say(text)
    return {"ok": True, "action": "say"}


@web.middleware
async def auth(req, handler):
    if req.path.startswith("/static/") or req.path == "/":
        return await handler(req)  # page shell only; it carries the token on to /ws and /api
    if not TOKEN or req.query.get("t") != TOKEN:
        raise web.HTTPUnauthorized()
    return await handler(req)


async def ws_handler(req):
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(req)
    codex.clients.add(ws)
    await ws.send_json(codex.usage)
    await ws.send_json({"type": "status", "codex": "connected" if codex.ready.is_set() else "disconnected",
                        "thread": codex.thread_id})
    await ws.send_json({"type": "devmode", **mode})
    if codex.display_v:
        await ws.send_json({"type": "display", "v": codex.display_v})
    try:
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            try:
                if m.get("type") == "say" and m.get("text", "").strip():
                    await codex.say(m["text"].strip())
                elif m.get("type") == "interrupt":
                    await codex.interrupt()
            except Exception as e:
                await ws.send_json({"type": "error", "message": str(e)})
    finally:
        codex.clients.discard(ws)
    return ws


async def architecture(req):
    try:
        return web.json_response(json.loads(SNAPSHOT.read_text()))
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def display_file(req):
    f = DISPLAY / "current.html"
    if not f.exists():
        raise web.HTTPNotFound()
    return web.FileResponse(f, headers={"Cache-Control": "no-store"})


async def watch_display():
    """Tell every page when current.html changes (poll mtime; cheap and dependency-free)."""
    f = DISPLAY / "current.html"
    while True:
        try:
            v = f.stat().st_mtime_ns if f.exists() else 0
            if v != codex.display_v:
                codex.display_v = v
                await codex.broadcast({"type": "display", "v": v})
        except Exception as e:
            log.warning("display watch: %s", e)
        await asyncio.sleep(0.5)


async def devmode_get(req):
    return web.json_response(mode)


async def devmode_post(req):
    body = await req.json() if req.can_read_body else {}
    action = req.match_info["action"]
    if action == "start":
        await set_mode(on=True)
    elif action == "stop":
        await set_mode(on=False)
    elif action == "display":
        return web.json_response({"launched": await launch_display(wake=bool(body.get("wake", True)))})
    elif action == "listen":
        await set_mode(listening=bool(body.get("on", True)))
    else:
        raise web.HTTPNotFound()
    return web.json_response(mode)


async def voice_post(req):
    body = await req.json()
    text = str(body.get("text", "")).strip()
    if not text:
        return web.json_response({"ok": False, "reason": "empty"})
    return web.json_response(await handle_voice(text, bool(body.get("final", True))))


async def health(req):
    return web.json_response({"codex": codex.ready.is_set(), "thread": codex.thread_id,
                              "clients": len(codex.clients)})


async def usage_get(req):
    return web.json_response(codex.usage, headers={'Cache-Control': 'no-store'})


async def watch_usage():
    while True:
        try:
            await codex.ready.wait()
            codex.usage = project_limits(await codex.request('account/rateLimits/read', {}))
        except Exception:
            codex.usage = {**codex.usage, 'stale': True}
        await codex.broadcast(codex.usage)
        await asyncio.sleep(60)


async def index(req):
    return web.FileResponse(STATIC / "index.html")


async def on_start(app):
    app["usage_task"] = asyncio.create_task(watch_usage())
    app["codex_task"] = asyncio.create_task(codex.run())
    app["speech_task"] = asyncio.create_task(speech.run())
    app["display_task"] = asyncio.create_task(watch_display())


async def on_shutdown(app):
    # Close browser sockets before aiohttp waits for request handlers.
    await asyncio.gather(*(ws.close(code=1001, message=b'Bridge updating') for ws in list(codex.clients)), return_exceptions=True)


async def on_stop(app):
    for name in ('speech_task', 'usage_task', 'display_task', 'codex_task'):
        app[name].cancel()
    await asyncio.gather(*(app[name] for name in ('speech_task', 'usage_task', 'display_task', 'codex_task')), return_exceptions=True)
    await systemctl("stop")


def make_app():
    app = web.Application(middlewares=[auth])
    app.on_startup.append(on_start)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_stop)
    app.add_routes([
        web.get("/", index),
        web.get("/ws", ws_handler),
        web.get("/api/architecture", architecture),
        web.get("/display", display_file),
        web.get("/api/health", health),
        web.get("/api/usage", usage_get),
        web.get("/api/devmode", devmode_get),
        web.post("/api/devmode/{action}", devmode_post),
        web.post("/api/voice", voice_post),
    ])
    if STATIC.is_dir():
        app.router.add_static("/static/", STATIC)
    return app


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit(f"missing token: create {TOKEN_FILE} (chmod 600) before starting")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    web.run_app(make_app(), host="0.0.0.0", port=PORT)
