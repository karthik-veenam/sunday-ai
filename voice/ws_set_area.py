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
            assert msg["type"] == "auth_required"
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

            resp = await call({
                "type": "config/entity_registry/update",
                "entity_id": "climate.hall_ac",
                "area_id": "hall",
            })
            print("area update result:", resp.get("success"), resp.get("error"))
            print("new entry area_id:", resp.get("result", {}).get("entity_entry", {}).get("area_id"))


asyncio.run(main())
