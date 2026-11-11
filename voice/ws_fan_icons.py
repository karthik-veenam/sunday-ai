import asyncio
import json

import aiohttp

HA_WS = "ws://localhost:8123/api/websocket"

with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]


def icon_grid(power_entity, power_service, swing_entity, swing_service, speed_entity, speed_service):
    def btn(entity, service, icon):
        domain, action = service.split(".")
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

    return {
        "type": "grid",
        "columns": 3,
        "square": True,
        "cards": [
            btn(power_entity, power_service, "mdi:power"),
            btn(swing_entity, swing_service, "mdi:swap-horizontal"),
            btn(speed_entity, speed_service, "mdi:fan-plus"),
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

            resp = await call({"type": "lovelace/config", "url_path": "dashboard-hall"})
            config = resp["result"]
            main_stack_cards = config["views"][0]["cards"][0]["cards"]

            # Recliners Fan — nested inside AC horizontal-stack's vertical-stack
            ac_hstack = next(
                c for c in main_stack_cards
                if c.get("type") == "horizontal-stack" and any(cc.get("type") == "thermostat" for cc in c["cards"])
            )
            vstack = next(c for c in ac_hstack["cards"] if c.get("type") == "vertical-stack")
            for i, c in enumerate(vstack["cards"]):
                if c.get("type") == "entities" and c.get("title") == "Recliners Fan":
                    vstack["cards"][i] = icon_grid(
                        "fan.hall_fan", "fan.toggle",
                        "button.hall_fan_swing", "button.press",
                        "button.hall_fan_speed", "button.press",
                    )
                    break

            # Sofa Fan — top-level card in the main stack
            for i, c in enumerate(main_stack_cards):
                if c.get("type") == "entities" and c.get("title") == "Sofa Fan":
                    main_stack_cards[i] = icon_grid(
                        "scene.fan_on_off", "scene.turn_on",
                        "scene.swing", "scene.turn_on",
                        "scene.fan_speed", "scene.turn_on",
                    )
                    break

            resp = await call({
                "type": "lovelace/config/save",
                "url_path": "dashboard-hall",
                "config": config,
            })
            print("dashboard save:", resp.get("success"), resp.get("error"))


asyncio.run(main())
