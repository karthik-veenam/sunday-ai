"""
Hogar local hub client — real-time device state via Socket.IO + REST.
Connects to the Z-wave hub on LAN, no cloud needed.
Auto-discovers hub by scanning for port 89 — survives IP changes.
"""
import asyncio
import json
import socket
import time
import ssl
import concurrent.futures
import aiohttp
import socketio

# ── Defaults (overridden by config.json) ─────────────────────────────────────
_DEFAULT_IP      = "192.168.88.30"
_DEFAULT_HOME_ID = 4783
_DEFAULT_USER_ID = "REPLACE_ME"
_DEFAULT_TOKEN   = (
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzM4NCJ9"
    ".eyJpc3MiOiJodHRwOlwvXC9sdWNpLnZubiIsImF1ZCI6Imh0dHA6XC9cL2x1Y2kudm5jIiwiZXhwIjoyMDY1NTM2MzQzLCJqdGkiOjEzNTE0LCJob21lX2lkIjo0NzgzfQ"
    ".a33fi1YlNZGVarXFRWN_VMNldF8jEO0t9tEn_BuSKabCliShC_xkpLPS7RSNlcEu"
)
_DEFAULT_DEVICE_MAP = {
    "light 1":   "9-1",
    "light 2":   "9-2",
    "spots":     "9-3",
    "foot lamp": "9-5",
    "cove":       "9-6",
    "fan":       "9-9",
}

_HOGAR_PORT = 89


def _scan_for_hub(subnet: str = "192.168.88", timeout: float = 0.35) -> str | None:
    """Scan subnet for Hogar hub by probing port 89. Returns IP or None."""
    def _try(i):
        ip = f"{subnet}.{i}"
        try:
            s = socket.create_connection((ip, _HOGAR_PORT), timeout=timeout)
            s.close()
            return ip
        except Exception:
            return None

    print(f"[Hogar] Scanning {subnet}.1-254 for hub on port {_HOGAR_PORT}...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=60) as ex:
        results = ex.map(_try, range(1, 255))
    found = [r for r in results if r]
    if found:
        print(f"[Hogar] Hub found at: {found[0]}")
        return found[0]
    print("[Hogar] Hub not found on subnet.")
    return None


class HogarClient:
    """
    Maintains real-time device state for all mapped devices.
    Call start() to begin polling + WebSocket listening.
    Auto-discovers hub if configured IP fails — survives DHCP changes.
    """

    def __init__(self, ip: str = _DEFAULT_IP, home_id: int = _DEFAULT_HOME_ID,
                 user_id: str = _DEFAULT_USER_ID, token: str = _DEFAULT_TOKEN,
                 device_map: dict | None = None):
        if device_map is None:
            device_map = _DEFAULT_DEVICE_MAP
        self._home_id  = home_id
        self._user_id  = user_id
        self._token    = token

        _mac_pfx = "70:2c:1f:37:a8:4f_zwave-cb4b64ae:"
        raw_map = device_map or {}
        self._device_map: dict[str, str] = {
            k.lower(): (_mac_pfx + v if not v.startswith("70:") else v)
            for k, v in raw_map.items()
        }
        self._id_to_name: dict[str, str] = {v: k for k, v in self._device_map.items()}

        self._states: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self._sio = socketio.AsyncClient(ssl_verify=False, logger=False, engineio_logger=False)
        self._running = False
        self._consecutive_poll_fails = 0
        self._register_handlers()
        self._set_ip(ip)

    def _set_ip(self, ip: str) -> None:
        self._hub_ip   = ip
        self._hub_ws   = f"http://{ip}:8009"
        self._hub_rest = f"https://{ip}:{_HOGAR_PORT}"
        print(f"[Hogar] Using hub at {ip}")

    # ── Public API ────────────────────────────────────────────────────────────

    def get_state(self, name: str) -> dict | None:
        return self._states.get(name.lower())

    def get_all_states(self) -> dict[str, dict]:
        return dict(self._states)

    def is_on(self, name: str) -> bool | None:
        s = self._states.get(name.lower())
        return s["on"] if s else None

    async def set_device(self, name: str, on: bool, brightness: int | None = None, speed: int | None = None) -> bool:
        devid = self._device_map.get(name.lower())
        if not devid:
            print(f"[Hogar] Unknown device: {name!r}")
            return False

        executions = [{"command": "OnOff", "params": {"on": on}}]
        if brightness is not None:
            executions.append({"command": "Brightness", "params": {"brightness": brightness}})
        if speed is not None:
            executions.append({"command": "Speed", "params": {"speed": speed}})

        for execution in executions:
            body = {
                "homeid": self._home_id,
                "payload": {
                    "cmd": "set",
                    "reqid": f"sunday-{int(time.time())}",
                    "objects": [{"type": "devices", "data": [devid], "execution": execution}],
                },
            }
            ok = await self._rest_post("/home-control/push-control-to-thing", body)
            if not ok:
                # Command failed — try rediscovery then retry once
                new_ip = await asyncio.get_event_loop().run_in_executor(None, _scan_for_hub)
                if new_ip and new_ip != self._hub_ip:
                    self._set_ip(new_ip)
                    ok = await self._rest_post("/home-control/push-control-to-thing", body)
                if not ok:
                    return False
        return True

    async def set_screen(self, name: str, opened: bool | None) -> bool:
        """Operate a Hogar screen using its OpenClose capability."""
        devid = self._device_map.get(name.lower())
        if not devid:
            return False
        body = {
            "homeid": self._home_id,
            "payload": {
                "cmd": "set",
                "reqid": f"curtain-{time.time_ns()}",
                "objects": [{"type": "devices", "data": [devid],
                             "execution": ({"command": "StartStop", "params": {"start": False}} if opened is None else {"command": "OpenClose", "params": {"open": opened}})}],
            },
        }
        return await self._rest_post("/home-control/push-control-to-thing", body)

    async def start(self) -> None:
        """Poll states once (with auto-discovery fallback) then start loops."""
        self._running = True
        # Try configured IP; if it fails, scan for hub
        success = await self._poll_all_states()
        if not success:
            print("[Hogar] Configured IP unreachable — running discovery...")
            new_ip = await asyncio.get_event_loop().run_in_executor(None, _scan_for_hub)
            if new_ip:
                self._set_ip(new_ip)
                await self._poll_all_states()
        asyncio.create_task(self._ws_loop())
        asyncio.create_task(self._poll_loop())

    # ── REST ─────────────────────────────────────────────────────────────────

    async def _rest_post(self, path: str, body: dict) -> bool:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(f"{self._hub_rest}{path}", json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    data = await r.json(content_type=None)
                    return data.get("success", False)
        except Exception as e:
            print(f"[Hogar] REST error {path}: {e}")
            return False

    async def _poll_all_states(self) -> bool:
        """Poll all device states. Returns True on success."""
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        body = {
            "homeid": self._home_id,
            "payload": {"cmd": "get", "reqid": f"sunday-poll-{int(time.time())}", "objects": [{"type": "devices", "data": []}]},
        }
        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(f"{self._hub_rest}/device/get-status-devices", json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    data = await r.json(content_type=None)
            for dev in data.get("data", []):
                self._ingest(dev)
            print(f"[Hogar] Polled {len(self._states)} device states.")
            self._consecutive_poll_fails = 0
            return True
        except Exception as e:
            print(f"[Hogar] Poll error: {e}")
            self._consecutive_poll_fails += 1
            return False

    async def _poll_loop(self) -> None:
        """Re-poll REST every 15s. After 3 consecutive failures, rediscover hub."""
        while self._running:
            await asyncio.sleep(15)
            success = await self._poll_all_states()
            if not success and self._consecutive_poll_fails >= 3:
                print("[Hogar] 3 consecutive poll failures — rediscovering hub...")
                new_ip = await asyncio.get_event_loop().run_in_executor(None, _scan_for_hub)
                if new_ip and new_ip != self._hub_ip:
                    self._set_ip(new_ip)
                    self._consecutive_poll_fails = 0
                    # Reconnect WebSocket to new IP
                    try:
                        await self._sio.disconnect()
                    except Exception:
                        pass

    # ── WebSocket ─────────────────────────────────────────────────────────────

    def _register_handlers(self) -> None:
        sio = self._sio

        @sio.event
        async def connect():
            print("[Hogar] WS connected, joining room...")
            await sio.emit("joinRoom", [self._user_id, self._home_id])

        @sio.on("status")
        async def on_status(data):
            if isinstance(data, str):
                data = json.loads(data)
            objects = data.get("payload", {}).get("objects", [])
            for obj in objects:
                if obj.get("type") == "devices":
                    for dev in obj.get("data", []):
                        self._ingest(dev)

        @sio.event
        async def disconnect():
            print("[Hogar] WS disconnected.")

    async def _ws_loop(self) -> None:
        """WebSocket loop — reconnects automatically, rediscovers hub after 5 WS failures."""
        ws_fail_count = 0
        while self._running:
            try:
                await self._sio.connect(
                    f"{self._hub_ws}?auth_token={self._token}",
                    transports=["websocket"],
                    wait_timeout=10,
                )
                ws_fail_count = 0
                await self._sio.wait()
            except Exception as e:
                ws_fail_count += 1
                print(f"[Hogar] WS error ({ws_fail_count}): {e} — reconnecting in 5s")
                if ws_fail_count >= 5:
                    print("[Hogar] Repeated WS failures — rediscovering hub...")
                    new_ip = await asyncio.get_event_loop().run_in_executor(None, _scan_for_hub)
                    if new_ip and new_ip != self._hub_ip:
                        self._set_ip(new_ip)
                        ws_fail_count = 0
                await asyncio.sleep(5)

    # ── State ingestion ───────────────────────────────────────────────────────

    def _ingest(self, dev: dict) -> None:
        devid = dev.get("devid", "")
        name = self._id_to_name.get(devid)
        if not name:
            return
        states = dev.get("states", {})
        entry: dict = {}
        if "OnOff" in states:
            entry["on"] = states["OnOff"].get("on", False)
        if "Brightness" in states:
            entry["brightness"] = states["Brightness"].get("brightness", 0)
        if "Speed" in states:
            entry["speed"] = states["Speed"].get("speed", 0)
        if "OpenClose" in states:
            entry["open"] = states["OpenClose"].get("open", False)
        if entry:
            self._states[name] = entry
            print(f"[Hogar] {name} → {entry}")
