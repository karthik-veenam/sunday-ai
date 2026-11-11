import asyncio
import json

import aiohttp

HA_WS = "ws://localhost:8123/api/websocket"

with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]


def chip(entity, service, icon, icon_color):
    return {
        "type": "template",
        "icon": icon,
        "icon_color": icon_color,
        "tap_action": {
            "action": "perform-action",
            "perform_action": service,
            "target": {"entity_id": entity},
        },
    }


def chips_row(power_entity, power_service, swing_entity, swing_service, speed_entity, speed_service):
    return {
        "type": "custom:mushroom-chips-card",
        "alignment": "center",
        "chips": [
            chip(power_entity, power_service, "mdi:power", "blue"),
            chip(swing_entity, swing_service, "mdi:swap-horizontal", "purple"),
            chip(speed_entity, speed_service, "mdi:fan-plus", "amber"),
        ],
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

            resp = await call({"type": "lovelace/config", "url_path": "dashboard-hall-test"})
            config = resp["result"]
            main_stack_cards = config["views"][0]["cards"][0]["cards"]

            ac_hstack = next(
                c for c in main_stack_cards
                if c.get("type") == "horizontal-stack" and any(cc.get("type") == "thermostat" for cc in c["cards"])
            )
            right_col = next(c for c in ac_hstack["cards"] if c.get("type") == "vertical-stack")
            rc = right_col["cards"]
            assert len(rc) == 5, len(rc)
            rc[2] = chips_row(
                "fan.hall_fan", "fan.toggle",
                "button.hall_fan_swing", "button.press",
                "button.hall_fan_speed", "button.press",
            )
            rc[4] = chips_row(
                "scene.fan_on_off", "scene.turn_on",
                "scene.swing", "scene.turn_on",
                "scene.fan_speed", "scene.turn_on",
            )

            resp = await call({"type": "lovelace/config/save", "url_path": "dashboard-hall-test", "config": config})
            print("Hall Test save:", resp.get("success"), resp.get("error"))


asyncio.run(main())
