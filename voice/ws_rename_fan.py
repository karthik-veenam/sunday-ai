import asyncio
import json

import aiohttp

HA_WS = "ws://localhost:8123/api/websocket"

with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]


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

            for entity_id, name in [
                ("fan.hall_fan", "Recliners Fan"),
                ("button.hall_fan_speed", "Recliners Fan Speed +"),
            ]:
                resp = await call({
                    "type": "config/entity_registry/update",
                    "entity_id": entity_id,
                    "name": name,
                    "area_id": "hall",
                })
                print(entity_id, "->", name, "| success:", resp.get("success"), resp.get("error"))


asyncio.run(main())
