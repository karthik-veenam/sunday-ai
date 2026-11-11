import asyncio
import json

import aiohttp

HA_WS = "ws://localhost:8123/api/websocket"

with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]

with open("/home/pi/homeassistant/.storage/lovelace.dashboard_hall") as f:
    dashboard_config = json.load(f)["data"]["config"]


async def main():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(HA_WS) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": TOKEN})
            msg = await ws.receive_json()
            assert msg["type"] == "auth_ok", msg

            await ws.send_json({
                "id": 2,
                "type": "lovelace/config/save",
                "url_path": "dashboard-hall",
                "config": dashboard_config,
            })
            while True:
                resp = await ws.receive_json()
                if resp.get("id") == 2:
                    print("save result:", resp.get("success"), resp.get("error"))
                    break


asyncio.run(main())
