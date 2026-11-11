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

            # Replace the broken [heading, button] horizontal-stack with a plain "Lights" heading
            for i, c in enumerate(main_stack_cards):
                if c.get("type") == "horizontal-stack" and any(
                    cc.get("type") == "heading" and cc.get("heading") == "Lights" for cc in c.get("cards", [])
                ):
                    main_stack_cards[i] = {"type": "heading", "heading": "Lights", "heading_style": "title"}
                    break

            # Add All Lights Off as a 4th tile inside the lights grid
            for c in main_stack_cards:
                if c.get("type") == "grid" and any(
                    cc.get("entity") == "light.hall_hall_tv_cove" for cc in c.get("cards", [])
                ):
                    c["cards"].append({
                        "type": "tile",
                        "entity": "button.hall_all_lights_off",
                        "name": "All Off",
                        "icon": "mdi:lightbulb-off-outline",
                        "vertical": True,
                    })
                    c["columns"] = 4
                    break

            # Merge Recliners Fan + Sofa Fan under one "Fans" title with sub-labels
            ac_hstack = next(
                c for c in main_stack_cards
                if c.get("type") == "horizontal-stack" and any(cc.get("type") == "thermostat" for cc in c["cards"])
            )
            right_col = next(c for c in ac_hstack["cards"] if c.get("type") == "vertical-stack")
            rc = right_col["cards"]  # [heading Recliners Fan, row, heading Sofa Fan, row]
            assert len(rc) == 4
            recliners_row, sofa_row = rc[1], rc[3]
            right_col["cards"] = [
                {"type": "heading", "heading": "Fans", "heading_style": "title"},
                {"type": "heading", "heading": "Recliners", "heading_style": "subtitle"},
                recliners_row,
                {"type": "heading", "heading": "Sofa", "heading_style": "subtitle"},
                sofa_row,
            ]

            resp = await call({"type": "lovelace/config/save", "url_path": "dashboard-hall", "config": config})
            print("Hall fix save:", resp.get("success"), resp.get("error"))

            resp = await call({"type": "lovelace/config", "url_path": "dashboard-common-areas"})
            common_config = resp["result"]
            common_config["views"][0]["cards"] = config["views"][0]["cards"]
            resp = await call({"type": "lovelace/config/save", "url_path": "dashboard-common-areas", "config": common_config})
            print("Common Areas sync:", resp.get("success"), resp.get("error"))


asyncio.run(main())
