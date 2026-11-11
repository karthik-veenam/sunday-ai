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

            ac_hstack = next(
                c for c in main_stack_cards
                if c.get("type") == "horizontal-stack" and any(cc.get("type") == "thermostat" for cc in c["cards"])
            )
            right_col = next(c for c in ac_hstack["cards"] if c.get("type") == "vertical-stack")
            cards = right_col["cards"]
            # current order: [heading Recliners Fan, fan_row, heading Lights, lights_grid]
            assert len(cards) == 4
            right_col["cards"] = [cards[2], cards[3], cards[0], cards[1]]

            resp = await call({"type": "lovelace/config/save", "url_path": "dashboard-hall", "config": config})
            print("Hall swap save:", resp.get("success"), resp.get("error"))

            resp = await call({"type": "lovelace/config", "url_path": "dashboard-common-areas"})
            common_config = resp["result"]
            common_config["views"][0]["cards"] = config["views"][0]["cards"]
            resp = await call({"type": "lovelace/config/save", "url_path": "dashboard-common-areas", "config": common_config})
            print("Common Areas sync:", resp.get("success"), resp.get("error"))


asyncio.run(main())
