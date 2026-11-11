"""Home Assistant is the only smart-home execution backend."""
import asyncio
from contextvars import ContextVar
import json
import aiohttp

ha_context = ContextVar("ha_context", default=None)


class HAClient:
    def __init__(self, url, token, actions_enabled=True):
        self.url = url.rstrip("/")
        self.token = token
        self.actions_enabled = actions_enabled
        self.session = None

    async def request(self, method, path, data=None):
        if self.session is None:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=25),
                headers={"Authorization": f"Bearer {self.token}"})
        async with self.session.request(method, self.url + path, json=data) as response:
            if response.status >= 400:
                raise RuntimeError(f"HA {method} {path}: HTTP {response.status}")
            return await response.json()

    async def request_bounded(self,method,path,max_bytes=2500000):
        if method!='GET':raise ValueError('Historical adapter is read-only')
        if self.session is None:
            self.session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20),headers={'Authorization':'Bearer '+self.token})
        async with self.session.request(method,self.url+path) as response:
            if response.status>=400:raise RuntimeError('Historical HA source HTTP '+str(response.status))
            content=bytearray()
            async for chunk in response.content.iter_chunked(65536):
                content.extend(chunk)
                if len(content)>max_bytes:
                    from history_time import HistoryError
                    raise HistoryError('UNSUPPORTED_METRIC','Recorder response exceeds safe byte budget; narrow the window or use statistics.')
            return json.loads(content)

    async def get_state(self, entity_id):
        try:
            return await self.request("GET", "/api/states/" + entity_id)
        except RuntimeError:
            return {"entity_id": entity_id, "state": "unavailable", "attributes": {}}

    async def get_states(self):
        return await self.request("GET", "/api/states")

    async def get_history(self, entity_id, hours=12):
        from datetime import datetime, timedelta, timezone
        from urllib.parse import urlencode
        start = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        data = await self.request("GET", "/api/history/period/" + start + "?" +
                                  urlencode({"filter_entity_id": entity_id,
                                             "minimal_response": "true"}))
        return data[0] if data else []

    async def call_service(self, domain, service, data=None):
        if not self.actions_enabled:
            return "Device actions are disabled in this runtime."
        from output import current_output
        world=getattr(self,'world',None);action_id=None
        if world:
            try:action_id=world.action_requested(domain,service,data or {},current_output.get())
            except Exception:world.store.count('action_audit_errors')
        try:
            # One dispatch only. REST response contains HA-context-matched states.
            changed=await self.request("POST", f"/api/services/{domain}/{service}", data or {})
            if world and action_id:
                try:world.action_result(action_id,changed_states=changed if isinstance(changed,list) else [])
                except Exception:world.store.count('action_audit_errors')
            return "ok"
        except asyncio.CancelledError:
            if world and action_id:
                try:world.action_result(action_id,unknown=True,reason='cancelled_after_dispatch')
                except Exception:pass
            raise
        except Exception as error:
            if world and action_id:
                try:world.action_result(action_id,unknown=True,reason=type(error).__name__)
                except Exception:pass
            return f"HA action failed or outcome unknown: {type(error).__name__}"

    async def close(self):
        if self.session:
            await self.session.close()


ALIASES = {
    "light 1": "light.roka_s_room_roka_light_1",
    "light 2": "light.roka_s_room_roka_light_2",
    "spots": "light.roka_s_room_roka_spots",
    "foot lamp": "light.roka_s_room_roka_foot_lamp",
    "cove": "light.roka_s_room_roka_cove",
    "fan": "fan.roka_s_room_roka_fan",
    "moon": "light.moon", "top light": "light.top_light",
    "panels": "light.panels", "dashboard": "light.dashboard",
    "geyser": "switch.geyser_socket_1",
    "projector": "switch.16amp_smart_plug_2_socket_1",
    "humidifier": "switch.wifi_socket_socket_3",
    "tv": "media_player.tv", "soundbar": "media_player.soundbar_2",
    "dining room speaker": "media_player.googlehome5093",
    "sian's speaker": "media_player.googlehome9588",
    "display": "media_player.display", "ac": "climate.bedroom_ac",
}


def resolve(device):
    device = device.lower().strip().removeprefix("the ")
    synonyms = {"cove light": "cove", "ceiling fan": "fan", "bedroom ac": "ac",
                "air conditioner": "ac", "sians room speaker": "sian's speaker"}
    device = synonyms.get(device, device)
    if device not in ALIASES:
        raise ValueError(f"Unknown device {device!r}; use get_devices for available devices.")
    return ALIASES[device]


async def control(client, device, action, volume=None, brightness=None, speed=None,
                  hue=None, saturation=None, color_temp=None):
    if action not in ("on", "off"):
        return "Action must be on or off."
    if device.lower().strip() == "all lights":
        names = [name for name, eid in ALIASES.items() if eid.startswith("light.")]
        results = await asyncio.gather(*(control(client, n, action, brightness=brightness)
                                         for n in names))
        return " ".join(results)
    try:
        eid = resolve(device)
    except ValueError as error:
        return str(error)
    state = await client.get_state(eid)
    if state["state"] in ("unavailable", "unknown"):
        return f"{device} is unavailable in Home Assistant; no action sent."
    domain = eid.split(".")[0]
    data = {"entity_id": eid}
    attrs = state.get("attributes", {})
    if domain == "climate":
        return "Use ac_control for AC mode, temperature and fan speed."
    if brightness is not None or hue is not None or color_temp is not None:
        if domain != "light":
            return f"{device} does not support lighting controls."
        modes = attrs.get("supported_color_modes", [])
        if modes == ["onoff"] and brightness is not None:
            return f"{device} exposes only on/off in HA; brightness was not changed."
        if brightness is not None:
            data["brightness_pct"] = max(0, min(100, int(brightness)))
        if hue is not None:
            if not any(m in modes for m in ("hs", "rgb", "rgbw", "rgbww", "xy")):
                return f"{device} does not expose color control in HA."
            data["hs_color"] = [float(hue) % 360, max(0, min(100, float(100 if saturation is None else saturation)))]
        if color_temp is not None:
            if "color_temp" not in modes:
                return f"{device} does not expose color temperature in HA."
            data["color_temp_kelvin"] = max(attrs.get("min_color_temp_kelvin", 2000),
                                            min(attrs.get("max_color_temp_kelvin", 9000), int(color_temp)))
    result = await client.call_service(domain, "turn_on" if action == "on" else "turn_off", data)
    if result != "ok":
        return result
    if speed is not None and action == "on":
        if domain != "fan" or int(speed) not in (1, 2, 3):
            return f"{device} switched {action}, but requested speed is unsupported."
        result = await client.call_service("fan", "set_preset_mode",
                    {"entity_id": eid, "preset_mode": {1: "low", 2: "medium", 3: "high"}[int(speed)]})
    if volume is not None and action == "on":
        if domain != "media_player":
            return f"{device} switched {action}, but volume is unsupported."
        result = await client.call_service("media_player", "volume_set",
                    {"entity_id": eid, "volume_level": max(0, min(100, int(volume))) / 100})
    return f"{device.capitalize()} {action}." if result == "ok" else result


async def devices(client):
    states = {s["entity_id"]: s for s in await client.get_states()}
    return json.dumps([{ "name": name, "entity_id": eid,
                        "state": states.get(eid, {}).get("state", "missing"),
                        "color_modes": states.get(eid, {}).get("attributes", {}).get("supported_color_modes", []),
                        "presets": states.get(eid, {}).get("attributes", {}).get("preset_modes", [])}
                       for name, eid in ALIASES.items()])

async def _ws_command(self, command, **fields):
    if self.session is None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25), headers={"Authorization":f"Bearer {self.token}"})
    async with asyncio.timeout(25):
        async with self.session.ws_connect(self.url + '/api/websocket') as ws:
            hello=await ws.receive_json()
            if hello.get('type')!='auth_required': raise RuntimeError('HA authentication protocol')
            await ws.send_json({'type':'auth','access_token':self.token})
            if (await ws.receive_json()).get('type')!='auth_ok': raise RuntimeError('HA authentication failed')
            await ws.send_json({'id':1,'type':command,**fields})
            while True:
                result=await ws.receive_json()
                if result.get('id')==1:
                    if not result.get('success'): raise RuntimeError('HA command unavailable: '+result.get('error',{}).get('code','unknown'))
                    return result.get('result')
HAClient.ws_command = _ws_command
