import asyncio
import json

import aiohttp

HA_WS = "ws://localhost:8123/api/websocket"

with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]

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
    "roka": {
        "display_name": "Roka",
        "order": ["dashboard-mobile", "dashboard-hall", "dashboard-dining", "dashboard-kitchen"],
    },
    "sian": {
        "display_name": "SiAn",
        "order": ["dashboard-sian", "dashboard-hall", "dashboard-dining", "dashboard-kitchen"],
    },
    "rani": {
        "display_name": "Rani",
        "order": ["dashboard-hall", "dashboard-kitchen", "dashboard-rani", "dashboard-dining"],
    },
}


async def main():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(HA_WS) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": TOKEN})
            msg = await ws.receive_json()
            assert msg["type"] == "auth_ok", msg

            msg_id = 1

            async def call(payload):
                nonlocal msg_id
                msg_id += 1
                payload["id"] = msg_id
                await ws.send_json(payload)
                while True:
                    resp = await ws.receive_json()
                    if resp.get("id") == msg_id:
                        return resp

            for username, info in USERS.items():
                print(f"=== {username} ===")
                r = await call({
                    "type": "config/auth/create",
                    "name": info["display_name"],
                    "group_ids": ["system-users"],
                })
                if not r.get("success"):
                    print("  user create FAILED:", r.get("error"))
                    continue
                user_id = r["result"]["user"]["id"]
                print("  created user_id:", user_id)

                r = await call({
                    "type": "config/auth_provider/homeassistant/create",
                    "user_id": user_id,
                    "username": username,
                    "password": username,
                })
                print("  credentials:", r.get("success"), r.get("error"))

                hidden = [p for p in ALL_PANELS if p not in info["order"]]

                r = await call({
                    "type": "frontend/set_user_data",
                    "user_id": user_id,
                    "key": "sidebarPanelOrder",
                    "value": info["order"],
                })
                print("  sidebarPanelOrder:", r.get("success"), r.get("error"))

                r = await call({
                    "type": "frontend/set_user_data",
                    "user_id": user_id,
                    "key": "sidebarHiddenPanels",
                    "value": hidden,
                })
                print("  sidebarHiddenPanels:", r.get("success"), r.get("error"))


asyncio.run(main())
