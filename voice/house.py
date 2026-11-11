"""
House-wide Hogar device manager/dashboard.

Shows every device on the Hogar hub (not just one room), lets you toggle
each one to physically identify which room/fixture it is, and lets you
rename devices, group them into rooms, and favorite rooms/devices.

Standalone — runs on its own port, independent of main.py / the voice
assistant / the production dashboard on 8080.
"""
import asyncio
import json
from pathlib import Path

from aiohttp import web

import hogar

PORT = 8082
LAYOUT_PATH = Path(__file__).parent / "house_layout.json"

# All devid suffixes currently seen on the hub (via a full scan), identity-mapped
# so HogarClient tracks every device by its raw suffix instead of a curated name.
ALL_SUFFIXES = [
    "2-6", "2-7", "3-0", "4-2", "4-3", "4-5", "4-6", "4-7", "4-9", "5-0",
    "6-2", "6-5", "6-7", "7-0", "8-0", "9-1", "9-2", "9-3", "9-5", "9-6",
    "9-9", "10-1", "10-2", "10-3", "10-5", "10-6", "10-9", "11-5", "11-6",
    "11-7", "11-9", "12-0",
]
FULL_DEVICE_MAP = {s: s for s in ALL_SUFFIXES}

# What we already know for certain (Roka's room == room 9).
KNOWN_LABELS = {
    "9-1": "Light 1", "9-2": "Light 2", "9-3": "Spots",
    "9-5": "Foot Lamp", "9-6": "Cove", "9-9": "Fan",
}
KNOWN_ROOMS = {"9": "Roka's Room"}


def _room_of(suffix: str) -> str:
    return suffix.split("-")[0]


def _infer_type(state: dict) -> str:
    if "open" in state:
        return "screen"
    if "speed" in state:
        return "fan"
    if "brightness" in state:
        return "dimmer"
    return "switch"


def _default_layout(states: dict) -> dict:
    rooms, devices = {}, {}
    for suffix in ALL_SUFFIXES:
        rid = _room_of(suffix)
        room_key = f"room-{rid}"
        if room_key not in rooms:
            rooms[room_key] = {
                "label": KNOWN_ROOMS.get(rid, f"Room {rid}"),
                "favorite": rid in KNOWN_ROOMS,
                "order": int(rid),
                "common_area": False,
            }
        devices[suffix] = {
            "label": KNOWN_LABELS.get(suffix, f"Device {suffix.split('-')[1]}"),
            "room": room_key,
            "favorite": False,
            "type": _infer_type(states.get(suffix, {})),
            "order": ALL_SUFFIXES.index(suffix),
        }
    return {"rooms": rooms, "devices": devices, "users": {}}


def _load_layout(states: dict) -> dict:
    defaults = _default_layout(states)
    if LAYOUT_PATH.exists():
        try:
            layout = json.loads(LAYOUT_PATH.read_text())
        except Exception:
            return defaults
        layout.setdefault("rooms", {})
        layout.setdefault("devices", {})
        layout.setdefault("users", {})
        layout.setdefault("deleted_rooms", [])
        deleted = set(layout["deleted_rooms"])
        for k, v in defaults["rooms"].items():
            if k not in deleted:
                layout["rooms"].setdefault(k, v)
        for k, v in defaults["devices"].items():
            layout["devices"].setdefault(k, v)
        for suffix, dl in layout["devices"].items():
            dl.setdefault("order", ALL_SUFFIXES.index(suffix) if suffix in ALL_SUFFIXES else 999)
        for rl in layout["rooms"].values():
            rl.setdefault("common_area", False)
        return layout
    return defaults


def _save_layout(layout: dict) -> None:
    LAYOUT_PATH.write_text(json.dumps(layout, indent=2))


def _read_hogar_ip() -> str:
    cfg_path = Path(__file__).parent / "config.json"
    try:
        cfg = json.loads(cfg_path.read_text())
        return cfg.get("hogar_ip") or hogar._DEFAULT_IP
    except Exception:
        return hogar._DEFAULT_IP


_hogar: "hogar.HogarClient | None" = None
_layout: dict = {}


def _sync_layout_with_states(states: dict) -> None:
    """Backfill any newly-seen devices/rooms without clobbering saved edits."""
    defaults = _default_layout(states)
    deleted = set(_layout.get("deleted_rooms", []))
    for k, v in defaults["rooms"].items():
        if k not in deleted:
            _layout["rooms"].setdefault(k, v)
    for k, v in defaults["devices"].items():
        _layout["devices"].setdefault(k, v)
    for suffix, dl in _layout["devices"].items():
        dl.setdefault("order", ALL_SUFFIXES.index(suffix) if suffix in ALL_SUFFIXES else 999)
    for rl in _layout["rooms"].values():
        rl.setdefault("common_area", False)


async def _index(request: web.Request) -> web.Response:
    return web.FileResponse(Path(__file__).parent / "house_index.html")


async def _icon(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    return web.FileResponse(Path(__file__).parent / f"icon-{name}")


async def _manifest(request: web.Request) -> web.Response:
    return web.json_response({
        "name": "Vasu Nilayam",
        "short_name": "Vasu Nilayam",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0b0d12",
        "theme_color": "#0b0d12",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        ],
    })


async def _api_state(request: web.Request) -> web.Response:
    states = _hogar.get_all_states() if _hogar else {}
    _sync_layout_with_states(states)

    devices = []
    for suffix, dl in _layout["devices"].items():
        state = states.get(suffix, {})
        devices.append({
            "suffix": suffix,
            "label": dl["label"],
            "room": dl["room"],
            "favorite": dl["favorite"],
            "order": dl.get("order", 999),
            "type": dl.get("type") or _infer_type(state),
            "on": state.get("on", False),
            "brightness": state.get("brightness"),
            "speed": state.get("speed"),
            "open": state.get("open"),
        })
    rooms = [{"id": rid, **r} for rid, r in _layout["rooms"].items()]
    return web.json_response({"devices": devices, "rooms": rooms})


async def _api_users(request: web.Request) -> web.Response:
    return web.json_response(_layout.get("users", {}))


async def _api_sleep(request: web.Request) -> web.Response:
    """Turn off every light (switch/dimmer) in the house. Callable from external
    automations (Home Assistant, cron, etc.) via GET or POST, no body required."""
    if not _hogar:
        return web.json_response({"ok": False, "error": "hogar not connected"}, status=503)
    common_room_ids = {rid for rid, rl in _layout["rooms"].items() if rl.get("common_area")}
    targets = [
        s for s in ALL_SUFFIXES
        if _layout["devices"].get(s, {}).get("type") in ("switch", "dimmer")
        and _layout["devices"].get(s, {}).get("room") in common_room_ids
    ]
    results = await asyncio.gather(*[_hogar.set_device(s, on=False) for s in targets], return_exceptions=True)
    turned_off = sum(1 for r in results if r is True)
    return web.json_response({"ok": True, "turned_off": turned_off, "total": len(targets)})


async def _api_toggle(request: web.Request) -> web.Response:
    body = await request.json()
    suffix = body.get("suffix")
    on = bool(body.get("on"))
    brightness = body.get("brightness")
    speed = body.get("speed")
    if not _hogar or suffix not in FULL_DEVICE_MAP:
        return web.json_response({"ok": False, "error": "unknown device"}, status=400)
    ok = await _hogar.set_device(suffix, on=on, brightness=brightness, speed=speed)
    return web.json_response({"ok": ok})


async def _api_layout(request: web.Request) -> web.Response:
    body = await request.json()
    kind = body.get("type")  # "device" | "room" | "new_room" | "delete_room"
    obj_id = body.get("id")
    patch = body.get("patch", {})

    if kind == "device" and obj_id in _layout["devices"]:
        _layout["devices"][obj_id].update(patch)
    elif kind == "room" and obj_id in _layout["rooms"]:
        _layout["rooms"][obj_id].update(patch)
    elif kind == "delete_room" and obj_id in _layout["rooms"]:
        if any(d["room"] == obj_id for d in _layout["devices"].values()):
            return web.json_response({"ok": False, "error": "room is not empty"}, status=400)
        del _layout["rooms"][obj_id]
        _layout.setdefault("deleted_rooms", [])
        if obj_id not in _layout["deleted_rooms"]:
            _layout["deleted_rooms"].append(obj_id)
    elif kind == "user":
        if not obj_id:
            return web.json_response({"ok": False, "error": "missing name"}, status=400)
        _layout.setdefault("users", {})
        existing = _layout["users"].get(obj_id, {})
        existing["room"] = patch.get("room")
        existing.setdefault("fav_devices", [])
        existing.setdefault("fav_rooms", [])
        _layout["users"][obj_id] = existing
    elif kind == "toggle_fav":
        name = body.get("name")
        fav_type = body.get("fav_type")
        target = body.get("target")
        favorite = bool(patch.get("favorite"))
        if not name or fav_type not in ("device", "room") or not target:
            return web.json_response({"ok": False, "error": "bad request"}, status=400)
        _layout.setdefault("users", {})
        u = _layout["users"].setdefault(name, {"room": None, "fav_devices": [], "fav_rooms": []})
        u.setdefault("fav_devices", [])
        u.setdefault("fav_rooms", [])
        key = "fav_devices" if fav_type == "device" else "fav_rooms"
        if favorite and target not in u[key]:
            u[key].append(target)
        elif not favorite and target in u[key]:
            u[key].remove(target)
    elif kind == "set_fav_order":
        name = body.get("name")
        fav_type = body.get("fav_type")
        order = body.get("order")
        if not name or fav_type not in ("device", "room") or not isinstance(order, list):
            return web.json_response({"ok": False, "error": "bad request"}, status=400)
        _layout.setdefault("users", {})
        u = _layout["users"].setdefault(name, {"room": None, "fav_devices": [], "fav_rooms": []})
        key = "fav_devices" if fav_type == "device" else "fav_rooms"
        u[key] = order
    elif kind == "new_room":
        new_id = f"room-custom-{int(asyncio.get_event_loop().time() * 1000)}"
        _layout["rooms"][new_id] = {
            "label": patch.get("label", "New Room"),
            "favorite": False,
            "order": 999,
        }
        _save_layout(_layout)
        return web.json_response({"ok": True, "id": new_id})
    else:
        return web.json_response({"ok": False, "error": "not found"}, status=404)

    _save_layout(_layout)
    return web.json_response({"ok": True})


async def main() -> None:
    global _hogar, _layout
    ip = _read_hogar_ip()
    _hogar = hogar.HogarClient(ip=ip, device_map=FULL_DEVICE_MAP)
    await _hogar.start()
    await asyncio.sleep(2)  # let the first poll land before building layout defaults
    _layout = _load_layout(_hogar.get_all_states())
    _save_layout(_layout)

    app = web.Application()
    app.router.add_get("/", _index)
    app.router.add_get("/manifest.json", _manifest)
    app.router.add_get("/icon-{name}", _icon)
    app.router.add_get("/api/state", _api_state)
    app.router.add_get("/api/users", _api_users)
    app.router.add_get("/api/sleep", _api_sleep)
    app.router.add_post("/api/sleep", _api_sleep)
    app.router.add_post("/api/toggle", _api_toggle)
    app.router.add_post("/api/layout", _api_layout)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"[House] Dashboard at http://0.0.0.0:{PORT}")
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
