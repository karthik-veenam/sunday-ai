import asyncio
import json

import aiohttp

BASE = "http://localhost:8123"
CLIENT_ID = "http://localhost:8123/"
REDIRECT_URI = "http://localhost:8123/"

ALL_PANELS = [
    "config", "map", "dashboard-roka", "dashboard-mobile", "dashboard-hall",
    "dashboard-kitchen", "dashboard-dining", "dashboard-common-areas",
    "dashboard-hall-test", "dashboard-kitchen-test", "dashboard-dining-test",
    "dashboard-common-areas-test", "dashboard-sian", "dashboard-rani", "lovelace",
    "light", "security", "climate", "home", "maintenance", "media-browser",
    "logbook", "history", "hacs", "energy", "todo", "browser-mod",
    "browser-mod-config", "hair",
]

USERS = {
    "roka": "dashboard-roka",
    "sian": "dashboard-sian",
    "rani": "dashboard-rani",
}


async def login_get_token(session, username, password):
    async with session.post(
        f"{BASE}/auth/login_flow",
        json={"client_id": CLIENT_ID, "handler": ["homeassistant", None], "redirect_uri": REDIRECT_URI},
    ) as r:
        flow = await r.json()
        assert r.status == 200, flow
    flow_id = flow["flow_id"]

    async with session.post(
        f"{BASE}/auth/login_flow/{flow_id}",
        json={"client_id": CLIENT_ID, "username": username, "password": password},
    ) as r:
        result = await r.json()
        assert r.status == 200 and result.get("type") == "create_entry", result
    code = result["result"]

    async with session.post(
        f"{BASE}/auth/token",
        data={"grant_type": "authorization_code", "code": code, "client_id": CLIENT_ID},
    ) as r:
        tok = await r.json()
        assert r.status == 200, tok
    return tok["access_token"]


async def set_sidebar_for(username, own_dashboard):
    async with aiohttp.ClientSession() as session:
        token = await login_get_token(session, username, username)
        async with session.ws_connect(f"ws://localhost:8123/api/websocket") as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": token})
            msg = await ws.receive_json()
            assert msg["type"] == "auth_ok", msg

            mid = 1

            async def call(payload):
                nonlocal mid
                mid += 1
                payload["id"] = mid
                await ws.send_json(payload)
                while True:
                    resp = await ws.receive_json()
                    if resp.get("id") == mid:
                        return resp

            hidden = [p for p in ALL_PANELS if p != own_dashboard]
            r = await call({"type": "frontend/set_user_data", "key": "sidebarPanelOrder", "value": [own_dashboard]})
            print(f"  [{username}] sidebarPanelOrder:", r.get("success"), r.get("error"))
            r = await call({"type": "frontend/set_user_data", "key": "sidebarHiddenPanels", "value": hidden})
            print(f"  [{username}] sidebarHiddenPanels:", r.get("success"), r.get("error"))

            # verify
            r1 = await call({"type": "frontend/get_user_data", "key": "sidebarPanelOrder"})
            r2 = await call({"type": "frontend/get_user_data", "key": "sidebarHiddenPanels"})
            print(f"  [{username}] verify order=", r1["result"]["value"])
            print(f"  [{username}] verify hidden_count=", len(r2["result"]["value"]))


async def main():
    for username, dash in USERS.items():
        print(f"=== {username} -> {dash} ===")
        await set_sidebar_for(username, dash)


asyncio.run(main())
