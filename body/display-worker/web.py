"""
Lightweight aiohttp server — serves the Sunday UI and a WebSocket for real-time events.
  GET  /      → static/index.html
  GET  /ws    → WebSocket (bidirectional)
"""
import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path

from aiohttp import web, WSMsgType

_clients: set[web.WebSocketResponse] = set()
_command_handler = None  # async callable(text: str) set by main.py
_announce_handler = None  # async callable(message: str, channel: str) set by main.py
_ANNOUNCE_TOKEN = "REPLACE_ME"  # simple shared secret
_state_cache: dict[str, dict] = {}  # last seen event per type, replayed to new clients
_presence_last_seen: dict[str, float] = {"phone": 0.0}  # seeded → starts as away
_presence_zone: dict[str, str] = {"phone": "unknown"}   # last known zone per device
_PRESENCE_TIMEOUT = 2 * 60  # seconds


def get_presence() -> dict[str, bool]:
    now = time.time()
    return {device: (now - ts) < _PRESENCE_TIMEOUT for device, ts in _presence_last_seen.items()}


def get_presence_zone(label: str = "phone") -> str:
    """Return the last known zone string for a device (e.g. 'home', 'work', 'not_home')."""
    return _presence_zone.get(label, "unknown")


def update_presence(label: str, home: bool, zone: str | None = None) -> None:
    """Called by presence_loop to authoritatively set device presence and zone."""
    _presence_last_seen[label] = time.time() if home else 0.0
    if zone:
        _presence_zone[label] = zone

_SUGGESTIONS_FILE = os.path.join(os.path.dirname(__file__), "last_suggestions.json")

# Pre-populate suggestions from last run so dashboard is never empty on restart
try:
    with open(_SUGGESTIONS_FILE) as _f:
        _state_cache["suggestions"] = json.load(_f)
except Exception:
    pass


# ── Public API ────────────────────────────────────────────────────────────────

def set_command_handler(fn) -> None:
    """Register an async function to handle commands sent from the UI."""
    global _command_handler
    _command_handler = fn


def set_announce_handler(fn) -> None:
    """Register an async function to deliver announcements via voice/telegram/both."""
    global _announce_handler
    _announce_handler = fn


_ask_handler = None  # async callable(text: str) -> None  — LLM + TTS voice reply

def set_ask_handler(fn) -> None:
    """Register an async function to process a text query and reply via voice."""
    global _ask_handler
    _ask_handler = fn


async def emit(event: dict) -> None:
    """Broadcast an event to all connected UI clients."""
    if event.get('type') in ('wake','transcript','thinking','transcribing','idle'):
        from display_event import publish
        publish(event['type'],event.get('text',''))
    # Cache stateful events so new clients get current state immediately
    if event.get("type") in ("context", "suggestions", "wake", "thinking", "transcribing", "idle", "transcript"):
        _state_cache[event["type"]] = event
        if event.get("type") == "suggestions":
            try:
                with open(_SUGGESTIONS_FILE, "w") as f:
                    json.dump(event, f)
            except Exception:
                pass
    if not _clients:
        return
    data = json.dumps(event)
    dead = set()
    for ws in list(_clients):
        try:
            await ws.send_str(data)
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)


async def run(port: int = 8080) -> None:
    """Start the HTTP + WebSocket server. Run as an asyncio task."""
    app = web.Application()
    app.router.add_get("/ws", _ws_handler)
    app.router.add_get("/", _index_handler)
    app.router.add_get("/tg", _tg_handler)
    app.router.add_get("/presence", _presence_handler)
    app.router.add_post("/device", _device_handler)
    app.router.add_post("/ac", _ac_handler)
    app.router.add_get("/ac/state", _ac_state_handler)
    app.router.add_post("/ac/resync", _ac_resync_handler)
    app.router.add_get("/screen/state", _screen_state_handler)
    app.router.add_post("/screen", _screen_handler)
    app.router.add_post("/announce", _announce_handler_route)
    app.router.add_post("/ask", _ask_route)
    app.router.add_get("/.well-known/agent.json", _a2a_card_handler)
    app.router.add_post("/tasks/send", _a2a_task_handler)
    app.router.add_get("/wiki", _wiki_handler)
    app.router.add_get("/api/wiki", _wiki_api_handler)
    app.router.add_static("/static", os.path.join(os.path.dirname(__file__), "static"))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[Web] UI available at http://0.0.0.0:{port}")
    while True:
        await asyncio.sleep(3600)


# ── Handlers ──────────────────────────────────────────────────────────────────

async def _presence_handler(request: web.Request) -> web.Response:
    """
    Tasker hits this when phone connects/disconnects from home WiFi.
    GET /presence?device=phone&home=1   → phone is home
    GET /presence?device=phone&home=0   → phone left
    """
    device = request.rel_url.query.get("device", "phone")
    was_home = (time.time() - _presence_last_seen.get(device, 0)) < _PRESENCE_TIMEOUT
    _presence_last_seen[device] = time.time()
    if not was_home:
        print(f"[Presence] {device} → home")
    return web.Response(text="ok")


async def _device_handler(request: web.Request) -> web.Response:
    """
    POST /device  {"device": "fan", "action": "on"}
    Calls _control_device directly — no LLM, instant.
    """
    import tools as _tools
    try:
        body = await request.json()
        device = body.get("device", "").strip().lower()
        action = body.get("action", "").strip().lower()
        if not device or action not in ("on", "off"):
            return web.Response(status=400, text="bad request")
        result = await _tools._control_device(device, action, None, None, None)
        return web.Response(text=result)
    except Exception as e:
        return web.Response(status=500, text=str(e))




async def _ac_state_handler(request: web.Request) -> web.Response:
    import tools as _tools
    try:
        s = await _tools._ha.get_state('climate.bedroom_ac')
        attrs = s.get('attributes', {})
        import json as _json
        return web.Response(
            content_type='application/json',
            text=_json.dumps({
                'state':    s.get('state', 'off'),
                'temperature': attrs.get('temperature'),
                'fan_mode': attrs.get('fan_mode'),
            })
        )
    except Exception as e:
        return web.Response(status=500, text=str(e))



_SCREEN_STATE_FILE = os.path.join(os.path.dirname(__file__), "screen_state.json")

def _read_screen_state_file():
    import json as _j
    try:
        with open(_SCREEN_STATE_FILE) as f:
            return _j.load(f).get("state", "unknown")
    except Exception:
        return None

def _write_screen_state_file(state):
    import json as _j, time as _t
    try:
        with open(_SCREEN_STATE_FILE, "w") as f:
            _j.dump({"state": state, "at": _t.time()}, f)
    except Exception:
        pass

async def _screen_state_handler(request: web.Request) -> web.Response:
    import tools as _tools, json as _j
    try:
        down_s  = await _tools._ha.get_state("script.projector_screen_down")
        close_s = await _tools._ha.get_state("script.projector_screen_close")
        down_run  = (down_s.get("attributes",{}).get("current",0) or 0) > 0
        close_run = (close_s.get("attributes",{}).get("current",0) or 0) > 0
        if down_run:
            state = "moving_down"
        elif close_run:
            state = "moving_up"
        else:
            state = _read_screen_state_file() or "unknown"
        return web.Response(content_type="application/json", text=_j.dumps({"state": state}))
    except Exception as e:
        return web.Response(status=500, text=str(e))


async def _screen_handler(request: web.Request) -> web.Response:
    import tools as _tools
    try:
        body   = await request.json()
        action = body.get("action", "")
        if action not in ("down", "close"):
            return web.Response(status=400, text="action must be 'down' or 'close'")
        eid = "script.projector_screen_down" if action == "down" else "script.projector_screen_close"
        result = await _tools._ha.call_service("script", "turn_on", {"entity_id": eid})
        if result == "ok":
            _write_screen_state_file("down" if action == "down" else "up")
        return web.Response(text="ok" if result == "ok" else f"failed: {result}")
    except Exception as e:
        return web.Response(status=500, text=str(e))


async def _ac_handler(request: web.Request) -> web.Response:
    """
    POST /ac  {"action": "on"|"off", "temperature": 24, "fan_mode": "low"|"mid"|"high"|"auto", "mode": "cool"|"dry"|"fan_only"}
    Calls _ac_control directly — no LLM.
    """
    import tools as _tools
    try:
        body = await request.json()
        action = body.get('action')
        temperature = body.get('temperature')
        fan_mode = body.get('fan_mode')
        mode = body.get('mode')
        if temperature is not None:
            temperature = int(temperature)
        result = await _tools._ac_control(action, mode, temperature, fan_mode)
        return web.Response(text=result)
    except Exception as e:
        return web.Response(status=500, text=str(e))



async def _ac_resync_handler(request: web.Request) -> web.Response:
    import tools as _tools
    try:
        s = await _tools._ha.get_state("climate.bedroom_ac")
        temp = int((s.get("attributes") or {}).get("temperature") or 24)
        if s.get("state", "off") == "off":
            return web.Response(text="AC is off")
        result = await _tools._ac_control(None, None, temp, None)
        return web.Response(text=result)
    except Exception as e:
        return web.Response(status=500, text=str(e))

async def _ask_route(request: web.Request) -> web.Response:
    """
    POST /ask
      {"text": "what is the temperature?", "token": "REPLACE_ME"}
    Sunday runs the full LLM pipeline and speaks the reply aloud.
    """
    if _ask_handler is None:
        return web.Response(status=503, text="ask handler not registered")
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="bad json")
    token = body.get("token") or ""
    if token != _ANNOUNCE_TOKEN:
        return web.Response(status=401, text="bad token")
    text = (body.get("text") or "").strip()
    if not text:
        return web.Response(status=400, text="empty text")
    asyncio.create_task(_ask_handler(text))
    return web.Response(text="ok")


async def _announce_handler_route(request: web.Request) -> web.Response:
    """
    POST /announce
      {
        "message": "Turned off humidifier — too humid (65%).",
        "channel": "voice" | "telegram" | "both",
        "token":   "REPLACE_ME"
      }
    """
    if _announce_handler is None:
        return web.Response(status=503, text="announce handler not registered")
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="bad json")
    message = (body.get("message") or "").strip()
    channel = (body.get("channel") or "voice").strip().lower()
    token   = body.get("token") or ""
    if token != _ANNOUNCE_TOKEN:
        return web.Response(status=401, text="bad token")
    if not message:
        return web.Response(status=400, text="empty message")
    if channel not in ("voice", "telegram", "both"):
        return web.Response(status=400, text="bad channel")
    try:
        await _announce_handler(message, channel)
        from display_event import publish
        if channel in ('voice','both'):publish('announcement',message)
        return web.Response(text="ok")
    except Exception as e:
        print(f"[Web] announce error: {e}")
        return web.Response(status=500, text=str(e))


async def _index_handler(request: web.Request) -> web.Response:
    static = os.path.join(os.path.dirname(__file__), "static", "index.html")
    return web.FileResponse(static)


async def _wiki_handler(request: web.Request) -> web.Response:
    return web.FileResponse(os.path.join(os.path.dirname(__file__), "static", "wiki.html"))


async def _wiki_api_handler(request: web.Request) -> web.Response:
    wiki_dir = Path(__file__).parent / "wiki"
    pages = []
    for md_file in sorted(wiki_dir.glob("*.md")):
        if md_file.name.startswith("."):
            continue
        content = md_file.read_text()
        pages.append({"id": md_file.stem, "content": content})

    # Build edges: parse [[page_name]] links in content
    page_ids = {p["id"] for p in pages}
    links = []
    seen = set()
    for page in pages:
        for match in re.findall(r"\[\[([^\]]+)\]\]", page["content"]):
            target = match.strip().lower()
            if target in page_ids and target != page["id"]:
                key = (page["id"], target)
                if key not in seen:
                    links.append({"source": page["id"], "target": target})
                    seen.add(key)

    return web.json_response({"pages": pages, "links": links})


async def _tg_handler(request: web.Request) -> web.Response:
    static = os.path.join(os.path.dirname(__file__), "static", "tg.html")
    return web.FileResponse(static)


async def _a2a_card_handler(request: web.Request) -> web.Response:
    card = {
        "name": "Sunday",
        "description": "Home voice assistant for Karthik's home in Hyderabad. Controls smart home devices, knows Karthik's presence and location zone, reads his calendar, sets alarms and timers, searches the web, and maintains long-term memory of home patterns.",
        "url": "http://localhost:8080",
        "version": "1.0.0",
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "capabilities": {"streaming": False},
        "skills": [
            {
                "id": "presence",
                "name": "Check Presence & Zone",
                "description": "Check if Karthik is home, away, at work, at badminton, or any other HA zone. Returns the exact zone name.",
                "tags": ["presence", "location", "zone", "home", "away"],
                "examples": ["is karthik home", "where is karthik", "what zone is karthik in"],
            },
            {
                "id": "calendar",
                "name": "Get Calendar",
                "description": "Get Karthik's calendar events for today or tomorrow — meetings, appointments, badminton sessions",
                "tags": ["calendar", "schedule", "meetings", "events"],
                "examples": ["calendar today", "calendar tomorrow", "any meetings today", "does karthik have badminton tomorrow"],
            },
            {
                "id": "device-control",
                "name": "Control Smart Home",
                "description": "Turn devices on/off or adjust settings: fan, AC, lights (cove, foot lamp, spots, light 1, light 2), geyser, projector",
                "tags": ["devices", "smart home", "lights", "fan", "ac", "geyser"],
                "examples": ["turn on the fan", "turn off all lights", "turn on ac", "turn on geyser"],
            },
            {
                "id": "alarm",
                "name": "Set Alarm",
                "description": "Set a morning alarm on Karthik's phone via Home Assistant",
                "tags": ["alarm", "wake up", "morning"],
                "examples": ["set alarm for 7:30 AM", "set alarm for 8 AM tomorrow"],
            },
            {
                "id": "timer",
                "name": "Set Timer",
                "description": "Start a countdown timer that announces when done",
                "tags": ["timer", "countdown", "reminder"],
                "examples": ["set a 10 minute timer", "timer for 30 minutes"],
            },
            {
                "id": "home-info",
                "name": "Get Home Status",
                "description": "Get current device states, room temperature, humidity, and overall home status",
                "tags": ["status", "temperature", "humidity", "devices"],
                "examples": ["what's the room temperature", "are any devices on", "home status"],
            },
        ],
    }
    return web.json_response(card)


async def _a2a_task_handler(request: web.Request) -> web.Response:
    import tools as _tools
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    task_id = body.get("id", str(uuid.uuid4()))
    parts = body.get("message", {}).get("parts", [])
    text = " ".join(p.get("text", "") for p in parts if p.get("type") == "text").lower().strip()

    try:
        if any(w in text for w in ("presence", "is karthik", "home", "away", "where", "zone", "location")):
            result = await _tools._get_presence()
        elif any(w in text for w in ("calendar", "schedule", "meeting", "tomorrow", "today", "event")):
            date_str = "tomorrow" if "tomorrow" in text else "today"
            result = await _tools._get_calendar(date_str)
        else:
            result = await _tools._get_presence()
    except Exception as e:
        result = f"Error: {e}"

    return web.json_response({
        "id": task_id,
        "status": {"state": "completed"},
        "artifacts": [{"parts": [{"type": "text", "text": result}]}],
    })


async def _ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=15)  # ping every 15s — keeps SSH tunnels alive
    await ws.prepare(request)
    _clients.add(ws)
    # Replay cached state so the new client sees current data immediately
    for cached in _state_cache.values():
        try:
            await ws.send_str(json.dumps(cached))
        except Exception:
            pass
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT and _command_handler:
                try:
                    data = json.loads(msg.data)
                    if data.get("type") == "command" and data.get("text"):
                        asyncio.create_task(_command_handler(data["text"]))
                except Exception:
                    pass
    finally:
        _clients.discard(ws)
    return ws
