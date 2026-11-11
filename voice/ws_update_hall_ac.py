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

            # Find the entity registry entry for climate.hall_ac
            resp = await call({"type": "config/entity_registry/list"})
            entities = resp["result"]
            entry = next(e for e in entities if e["entity_id"] == "climate.hall_ac")
            print("entity entry:", json.dumps(entry, indent=2)[:500])

            device_id = entry.get("device_id")

            # Rename the entity's display name
            resp = await call({
                "type": "config/entity_registry/update",
                "entity_id": "climate.hall_ac",
                "name": "Central AC",
            })
            print("entity rename result:", resp.get("success"))

            if device_id:
                resp = await call({
                    "type": "config/device_registry/update",
                    "device_id": device_id,
                    "area_id": "hall",
                    "name_by_user": "Central AC",
                })
                print("device area/name update result:", resp.get("success"), resp.get("error"))
            else:
                print("No device_id found for this entity!")


asyncio.run(main())
