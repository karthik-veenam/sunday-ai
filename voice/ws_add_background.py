import asyncio
import json

import aiohttp

HA_WS = "ws://localhost:8123/api/websocket"

with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]

BACKGROUND = "center / cover no-repeat fixed linear-gradient(135deg, #1a1a2e 0%, #16213e 45%, #0f3460 100%)"


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

            for url_path in ["dashboard-hall", "dashboard-kitchen", "dashboard-dining", "dashboard-common-areas"]:
                resp = await call({"type": "lovelace/config", "url_path": url_path})
                config = resp["result"]
                for v in config["views"]:
                    v["background"] = BACKGROUND
                resp = await call({"type": "lovelace/config/save", "url_path": url_path, "config": config})
                print(url_path, "save:", resp.get("success"), resp.get("error"))


asyncio.run(main())
