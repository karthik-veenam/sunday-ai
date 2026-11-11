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

            resp = await call({"type": "lovelace/config", "url_path": "dashboard-hall"})
            config = resp["result"]
            main_stack_cards = config["views"][0]["cards"][0]["cards"]

            sofa_fan_card = {
                "type": "entities",
                "title": "Sofa Fan",
                "entities": [
                    {"entity": "scene.fan_on_off", "name": "Power", "icon": "mdi:fan"},
                    {"entity": "scene.fan_speed", "name": "Speed +", "icon": "mdi:fan-plus"},
                    {"entity": "scene.swing", "name": "Swing", "icon": "mdi:swap-horizontal"},
                ],
            }
            main_stack_cards.append(sofa_fan_card)

            resp = await call({
                "type": "lovelace/config/save",
                "url_path": "dashboard-hall",
                "config": config,
            })
            print("dashboard save:", resp.get("success"), resp.get("error"))


asyncio.run(main())
