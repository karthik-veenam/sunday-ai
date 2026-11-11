import asyncio
import json

import aiohttp

HA_WS = "ws://localhost:8123/api/websocket"

with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]


async def main():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(HA_WS) as ws:
            msg = await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": TOKEN})
            msg = await ws.receive_json()
            assert msg["type"] == "auth_ok", msg

            await ws.send_json({"id": 2, "type": "config/entity_registry/list"})
            while True:
                resp = await ws.receive_json()
                if resp.get("id") == 2:
                    break
            entry = next(e for e in resp["result"] if e["entity_id"] == "climate.hall_ac")
            print("entity_id:", entry["entity_id"])
            print("area_id:", entry.get("area_id"))
            print("name override:", entry.get("name"))


asyncio.run(main())
