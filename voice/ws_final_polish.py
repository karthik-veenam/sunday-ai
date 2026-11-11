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

            # 1. TV card -> full width (unwrap from its horizontal-stack with All Lights Off)
            tv_idx = None
            for i, c in enumerate(main_stack_cards):
                if c.get("type") == "horizontal-stack" and any(cc.get("type") == "media-control" for cc in c["cards"]):
                    tv_idx = i
                    break
            assert tv_idx is not None
            tv_card = next(cc for cc in main_stack_cards[tv_idx]["cards"] if cc["type"] == "media-control")
            main_stack_cards[tv_idx] = tv_card

            # 2. Lights heading row -> add small icon-only All Lights Off button inline
            lights_heading_idx = None
            for i, c in enumerate(main_stack_cards):
                if c.get("type") == "heading" and c.get("heading") == "Lights":
                    lights_heading_idx = i
                    break
            assert lights_heading_idx is not None
            main_stack_cards[lights_heading_idx] = {
                "type": "horizontal-stack",
                "cards": [
                    {"type": "heading", "heading": "Lights", "heading_style": "title"},
                    {
                        "type": "button",
                        "show_name": False,
                        "show_state": False,
                        "icon": "mdi:lightbulb-off-outline",
                        "tap_action": {
                            "action": "perform-action",
                            "perform_action": "button.press",
                            "target": {"entity_id": "button.hall_all_lights_off"},
                        },
                    },
                ],
            }

            # 3. Both fan grids -> square:true so they fill more vertical height in the narrow column
            ac_hstack = next(
                c for c in main_stack_cards
                if c.get("type") == "horizontal-stack" and any(cc.get("type") == "thermostat" for cc in c["cards"])
            )
            right_col = next(c for c in ac_hstack["cards"] if c.get("type") == "vertical-stack")
            for c in right_col["cards"]:
                if c.get("type") == "grid":
                    c["square"] = True

            resp = await call({"type": "lovelace/config/save", "url_path": "dashboard-hall", "config": config})
            print("Hall final polish save:", resp.get("success"), resp.get("error"))

            resp = await call({"type": "lovelace/config", "url_path": "dashboard-common-areas"})
            common_config = resp["result"]
            common_config["views"][0]["cards"] = config["views"][0]["cards"]
            resp = await call({"type": "lovelace/config/save", "url_path": "dashboard-common-areas", "config": common_config})
            print("Common Areas sync:", resp.get("success"), resp.get("error"))


asyncio.run(main())
