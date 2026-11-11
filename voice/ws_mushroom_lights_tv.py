import asyncio
import json

import aiohttp

HA_WS = "ws://localhost:8123/api/websocket"

with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]


def mushroom_light(entity, name, icon_color="amber"):
    return {
        "type": "custom:mushroom-light-card",
        "entity": entity,
        "name": name,
        "icon_color": icon_color,
        "show_brightness_control": True,
        "collapsible_controls": True,
    }


def mushroom_entity(entity, name, icon):
    return {
        "type": "custom:mushroom-entity-card",
        "entity": entity,
        "name": name,
        "icon": icon,
        "icon_color": "blue-grey",
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

            # Lights grid -> Mushroom light cards + entity card for All Off
            for i, c in enumerate(main_stack_cards):
                if c.get("type") == "grid" and any(
                    cc.get("entity") == "light.hall_hall_tv_cove" for cc in c.get("cards", [])
                ):
                    c["cards"] = [
                        mushroom_light("light.hall_hall_tv_cove", "TV Cove"),
                        mushroom_light("light.hall_hall_other_spots", "Other Spots"),
                        mushroom_light("light.hall_hall_plant_spots", "Plant Spots"),
                        mushroom_entity("button.hall_all_lights_off", "All Off", "mdi:lightbulb-off-outline"),
                    ]
                    break

            # TV card -> mushroom-media-player-card, no art-based color extraction
            for i, c in enumerate(main_stack_cards):
                if c.get("type") == "media-control" and c.get("entity") == "media_player.bravia_kd_65x75k":
                    main_stack_cards[i] = {
                        "type": "custom:mushroom-media-player-card",
                        "entity": "media_player.bravia_kd_65x75k",
                        "use_media_info": False,
                        "icon_color": "red",
                        "media_controls": ["previous", "play_pause", "next"],
                        "volume_controls": ["volume_mute", "volume_set"],
                        "collapsible_controls": True,
                    }
                    break

            resp = await call({"type": "lovelace/config/save", "url_path": "dashboard-hall-test", "config": config})
            print("Hall Test save:", resp.get("success"), resp.get("error"))


asyncio.run(main())
