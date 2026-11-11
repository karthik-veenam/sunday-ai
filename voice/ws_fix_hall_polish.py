import asyncio
import json

import aiohttp

HA_WS = "ws://localhost:8123/api/websocket"

with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]


def heading(text):
    return {"type": "heading", "heading": text, "heading_style": "title"}


def icon_button(entity, service, icon):
    return {
        "type": "button",
        "show_name": False,
        "show_state": False,
        "icon": icon,
        "tap_action": {
            "action": "perform-action",
            "perform_action": service,
            "target": {"entity_id": entity},
        },
    }


def fan_icon_row(power_entity, power_service, swing_entity, swing_service, speed_entity, speed_service):
    return {
        "type": "grid",
        "columns": 3,
        "cards": [
            icon_button(power_entity, power_service, "mdi:power"),
            icon_button(swing_entity, swing_service, "mdi:swap-horizontal"),
            icon_button(speed_entity, speed_service, "mdi:fan-plus"),
        ],
    }


def light_tile(entity, name, icon="mdi:spotlight-beam"):
    return {
        "type": "tile",
        "entity": entity,
        "name": name,
        "icon": icon,
        "vertical": True,
        "tap_action": {"action": "toggle"},
    }


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

            # Right column next to AC: [fan_icon_row, lights_grid] -> add headings, fix lights labels
            ac_hstack = next(
                c for c in main_stack_cards
                if c.get("type") == "horizontal-stack" and any(cc.get("type") == "thermostat" for cc in c["cards"])
            )
            right_col = next(c for c in ac_hstack["cards"] if c.get("type") == "vertical-stack")

            new_right_cards = [
                heading("Recliners Fan"),
                fan_icon_row(
                    "fan.hall_fan", "fan.toggle",
                    "button.hall_fan_swing", "button.press",
                    "button.hall_fan_speed", "button.press",
                ),
                heading("Lights"),
                {
                    "type": "grid",
                    "columns": 3,
                    "square": False,
                    "cards": [
                        light_tile("light.hall_hall_tv_cove", "TV Cove", "mdi:led-strip-variant"),
                        light_tile("light.hall_hall_other_spots", "Other Spots"),
                        light_tile("light.hall_hall_plant_spots", "Plant Spots"),
                    ],
                },
            ]
            right_col["cards"] = new_right_cards

            # Sofa Fan section (full width, standalone) — add heading, keep buttons compact (no square)
            for i, c in enumerate(main_stack_cards):
                if c.get("type") == "grid" and len(c.get("cards", [])) == 3 and all(
                    cc.get("type") == "button" for cc in c["cards"]
                ):
                    # this is the sofa fan row (the only bare 3-button grid at top level)
                    main_stack_cards[i:i + 1] = [
                        heading("Sofa Fan"),
                        fan_icon_row(
                            "scene.fan_on_off", "scene.turn_on",
                            "scene.swing", "scene.turn_on",
                            "scene.fan_speed", "scene.turn_on",
                        ),
                    ]
                    break

            resp = await call({"type": "lovelace/config/save", "url_path": "dashboard-hall", "config": config})
            print("Hall polish save:", resp.get("success"), resp.get("error"))

            # Mirror into Common Areas' Hall tab
            resp = await call({"type": "lovelace/config", "url_path": "dashboard-common-areas"})
            common_config = resp["result"]
            common_config["views"][0]["cards"] = config["views"][0]["cards"]
            resp = await call({"type": "lovelace/config/save", "url_path": "dashboard-common-areas", "config": common_config})
            print("Common Areas Hall tab sync:", resp.get("success"), resp.get("error"))


asyncio.run(main())
