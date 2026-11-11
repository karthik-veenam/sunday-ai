import asyncio
import json

import aiohttp

HA_WS = "ws://localhost:8123/api/websocket"

with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]

# Entities to wire: lights everywhere + the one real Hogar fan (Kitchen).
TARGET_ENTITIES = {
    "light.hall_hall_tv_cove",
    "light.hall_hall_other_spots",
    "light.hall_hall_plant_spots",
    "light.kitchen_kitchen_pooja",
    "light.kitchen_kitchen_utility",
    "light.kitchen_kitchen_dishwash_light",
    "light.kitchen_kitchen_cove_light",
    "light.kitchen_kitchen_spots",
    "light.kitchen_kitchen_centre_spots",
    "fan.kitchen_kitchen_fan",
    "light.dining_dining_walkway",
    "light.dining_dining_cove_light",
    "light.dining_dining_spots",
    "light.dining_dining_spots_2",
}


def vibrate_tap_action(entity_id):
    return {
        "action": "perform-action",
        "perform_action": "script.vibrate_toggle",
        "data": {"target_entity": entity_id},
    }


def walk_and_patch(node, patched):
    """Recursively find tile cards whose entity is in TARGET_ENTITIES and rewrite tap_action."""
    if isinstance(node, dict):
        if node.get("type") == "tile" and node.get("entity") in TARGET_ENTITIES:
            node["tap_action"] = vibrate_tap_action(node["entity"])
            patched.append(node["entity"])
        for v in node.values():
            walk_and_patch(v, patched)
    elif isinstance(node, list):
        for item in node:
            walk_and_patch(item, patched)


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

            for url_path in ["dashboard-hall", "dashboard-kitchen", "dashboard-dining"]:
                resp = await call({"type": "lovelace/config", "url_path": url_path})
                config = resp["result"]
                patched = []
                walk_and_patch(config, patched)
                print(url_path, "patched:", patched)
                resp = await call({"type": "lovelace/config/save", "url_path": url_path, "config": config})
                print(url_path, "save:", resp.get("success"), resp.get("error"))

            # Rebuild Common Areas from the freshly-patched individual dashboards
            resp = await call({"type": "lovelace/config", "url_path": "dashboard-hall"})
            hall_cards = resp["result"]["views"][0]["cards"]
            resp = await call({"type": "lovelace/config", "url_path": "dashboard-kitchen"})
            kitchen_cards = resp["result"]["views"][0]["cards"]
            resp = await call({"type": "lovelace/config", "url_path": "dashboard-dining"})
            dining_cards = resp["result"]["views"][0]["cards"]

            resp = await call({"type": "lovelace/config", "url_path": "dashboard-common-areas"})
            common_config = resp["result"]
            common_config["views"][0]["cards"] = hall_cards
            common_config["views"][1]["cards"] = kitchen_cards
            common_config["views"][2]["cards"] = dining_cards
            resp = await call({"type": "lovelace/config/save", "url_path": "dashboard-common-areas", "config": common_config})
            print("Common Areas sync:", resp.get("success"), resp.get("error"))


asyncio.run(main())
