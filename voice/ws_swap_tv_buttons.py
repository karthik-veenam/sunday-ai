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

            # Find the TV horizontal-stack (media-control + button vertical-stack)
            tv_hstack = None
            for c in main_stack_cards:
                if c.get("type") == "horizontal-stack" and any(
                    cc.get("type") == "media-control" for cc in c.get("cards", [])
                ):
                    tv_hstack = c
                    break
            assert tv_hstack is not None, "TV horizontal-stack not found"

            button_vstack = next(c for c in tv_hstack["cards"] if c.get("type") == "vertical-stack")
            button_vstack["cards"] = [
                {
                    "type": "tile",
                    "name": "All Lights Off",
                    "icon": "mdi:lightbulb-off-outline",
                    "entity": "light.hall_hall_tv_cove",
                    "tap_action": {
                        "action": "perform-action",
                        "perform_action": "light.turn_off",
                        "target": {
                            "entity_id": [
                                "light.hall_hall_tv_cove",
                                "light.hall_hall_other_spots",
                                "light.hall_hall_plant_spots",
                            ]
                        },
                    },
                }
            ]

            resp = await call({
                "type": "lovelace/config/save",
                "url_path": "dashboard-hall",
                "config": config,
            })
            print("dashboard save:", resp.get("success"), resp.get("error"))


asyncio.run(main())
