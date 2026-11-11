import asyncio
import json

import aiohttp

HA_WS = "ws://localhost:8123/api/websocket"

with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]

LIVE_DASHBOARDS = {
    "dashboard-hall": "Hall",
    "dashboard-kitchen": "Kitchen",
    "dashboard-dining": "Dining",
    "dashboard-common-areas": "Common Areas",
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

            for url_path, title in LIVE_DASHBOARDS.items():
                # 1. Revert background on the live dashboard
                resp = await call({"type": "lovelace/config", "url_path": url_path})
                config = resp["result"]
                for v in config["views"]:
                    v.pop("background", None)
                resp = await call({"type": "lovelace/config/save", "url_path": url_path, "config": config})
                print(url_path, "reverted:", resp.get("success"), resp.get("error"))

                # 2. Create a test-clone dashboard with the SAME (now-reverted) config
                test_url_path = f"{url_path}-test"
                test_title = f"{title} Test"
                resp = await call({
                    "type": "lovelace/dashboards/create",
                    "url_path": test_url_path,
                    "title": test_title,
                    "icon": "mdi:flask-outline",
                    "show_in_sidebar": True,
                    "require_admin": False,
                })
                print(test_url_path, "created:", resp.get("success"), resp.get("error"))

                resp = await call({
                    "type": "lovelace/config/save",
                    "url_path": test_url_path,
                    "config": config,
                })
                print(test_url_path, "seeded:", resp.get("success"), resp.get("error"))


asyncio.run(main())
