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

            # Rename + place in Hall area
            resp = await call({
                "type": "config/entity_registry/update",
                "entity_id": "button.hall_fan_swing",
                "name": "Recliners Fan Swing",
                "area_id": "hall",
            })
            print("rename:", resp.get("success"), resp.get("error"))

            # Fetch current dashboard config, fix the swing row, save it back
            resp = await call({"type": "lovelace/config", "url_path": "dashboard-hall"})
            config = resp["result"]
            fan_card = config["views"][0]["cards"][0]["cards"][1]["cards"][1]["cards"][0]
            assert fan_card["type"] == "entities" and fan_card["title"] == "Recliners Fan", fan_card
            fan_card["entities"][2] = {
                "entity": "button.hall_fan_swing",
                "name": "Swing",
                "icon": "mdi:swap-horizontal",
            }

            resp = await call({
                "type": "lovelace/config/save",
                "url_path": "dashboard-hall",
                "config": config,
            })
            print("dashboard save:", resp.get("success"), resp.get("error"))


asyncio.run(main())
