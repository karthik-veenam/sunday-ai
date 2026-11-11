import asyncio
import json

import aiohttp

HA_WS = "ws://localhost:8123/api/websocket"

with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]


def light_tile(entity, name, icon="mdi:spotlight-beam"):
    return {
        "type": "tile",
        "entity": entity,
        "name": name,
        "icon": icon,
        "tap_action": {"action": "toggle"},
    }


def kitchen_cards():
    return [
        {
            "type": "markdown",
            "content": (
                '<div style="text-align:center; white-space:nowrap; font-size:15px; line-height:18px;">'
                "🌀 <b>{{ 'FAN ON' if is_state('fan.kitchen_kitchen_fan','on') else 'FAN OFF' }}</b>"
                "&nbsp;&nbsp;&nbsp;&nbsp;"
                "💡 <b>{{ expand('light.kitchen_kitchen_pooja','light.kitchen_kitchen_utility',"
                "'light.kitchen_kitchen_dishwash_light','light.kitchen_kitchen_cove_light',"
                "'light.kitchen_kitchen_spots','light.kitchen_kitchen_centre_spots') "
                "| selectattr('state','eq','on') | list | count }} ON</b>"
                "</div>"
            ),
        },
        {
            "type": "tile",
            "entity": "fan.kitchen_kitchen_fan",
            "name": "Kitchen Fan",
            "icon": "mdi:fan",
            "features": [{"type": "fan-preset-modes"}],
        },
        {
            "type": "grid",
            "columns": 3,
            "square": False,
            "cards": [
                light_tile("light.kitchen_kitchen_pooja", "Pooja", "mdi:lightbulb"),
                light_tile("light.kitchen_kitchen_utility", "Utility", "mdi:lightbulb"),
                light_tile("light.kitchen_kitchen_dishwash_light", "Dishwash Light", "mdi:lightbulb"),
                light_tile("light.kitchen_kitchen_cove_light", "Cove Light", "mdi:led-strip-variant"),
                light_tile("light.kitchen_kitchen_spots", "Spots"),
                light_tile("light.kitchen_kitchen_centre_spots", "Centre Spots"),
            ],
        },
        {
            "type": "tile",
            "entity": "button.kitchen_all_lights_off",
            "name": "All Lights Off",
            "icon": "mdi:lightbulb-off-outline",
        },
    ]


def dining_cards():
    return [
        {
            "type": "markdown",
            "content": (
                '<div style="text-align:center; white-space:nowrap; font-size:15px; line-height:18px;">'
                "💡 <b>{{ expand('light.dining_dining_walkway','light.dining_dining_cove_light',"
                "'light.dining_dining_spots','light.dining_dining_spots_2') "
                "| selectattr('state','eq','on') | list | count }} ON</b>"
                "&nbsp;&nbsp;&nbsp;&nbsp;"
                "🚪 <b>{{ 'Balcony Open' if is_state('binary_sensor.dining_dining_balcony','on') else 'Balcony Closed' }}</b>"
                "</div>"
            ),
        },
        {
            "type": "grid",
            "columns": 3,
            "square": False,
            "cards": [
                light_tile("light.dining_dining_walkway", "Walkway"),
                light_tile("light.dining_dining_cove_light", "Cove Light", "mdi:led-strip-variant"),
                light_tile("light.dining_dining_spots", "Spots 1"),
                light_tile("light.dining_dining_spots_2", "Spots 2"),
                {
                    "type": "tile",
                    "entity": "binary_sensor.dining_dining_balcony",
                    "name": "Balcony",
                    "icon": "mdi:window-shutter",
                },
            ],
        },
        {
            "type": "tile",
            "entity": "button.dining_all_lights_off",
            "name": "All Lights Off",
            "icon": "mdi:lightbulb-off-outline",
        },
    ]


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

            kitchen_view_cards = kitchen_cards()
            dining_view_cards = dining_cards()

            # Kitchen dashboard
            resp = await call({
                "type": "lovelace/config/save",
                "url_path": "dashboard-kitchen",
                "config": {
                    "title": "Kitchen",
                    "views": [{
                        "title": "Kitchen", "path": "kitchen-hub", "icon": "mdi:stove", "type": "panel",
                        "cards": [{"type": "vertical-stack", "cards": kitchen_view_cards}],
                    }],
                },
            })
            print("Kitchen dashboard save:", resp.get("success"), resp.get("error"))

            # Dining dashboard
            resp = await call({
                "type": "lovelace/config/save",
                "url_path": "dashboard-dining",
                "config": {
                    "title": "Dining",
                    "views": [{
                        "title": "Dining", "path": "dining-hub", "icon": "mdi:silverware-fork-knife", "type": "panel",
                        "cards": [{"type": "vertical-stack", "cards": dining_view_cards}],
                    }],
                },
            })
            print("Dining dashboard save:", resp.get("success"), resp.get("error"))

            # Reuse Hall's existing full config for the Common Areas "Hall" tab
            resp = await call({"type": "lovelace/config", "url_path": "dashboard-hall"})
            hall_cards = resp["result"]["views"][0]["cards"]

            common_config = {
                "title": "Common Areas",
                "views": [
                    {
                        "title": "Hall", "path": "hall", "icon": "mdi:sofa", "type": "panel",
                        "cards": hall_cards,
                    },
                    {
                        "title": "Kitchen", "path": "kitchen", "icon": "mdi:stove", "type": "panel",
                        "cards": [{"type": "vertical-stack", "cards": kitchen_view_cards}],
                    },
                    {
                        "title": "Dining", "path": "dining", "icon": "mdi:silverware-fork-knife", "type": "panel",
                        "cards": [{"type": "vertical-stack", "cards": dining_view_cards}],
                    },
                ],
            }
            resp = await call({
                "type": "lovelace/config/save",
                "url_path": "dashboard-common-areas",
                "config": common_config,
            })
            print("Common Areas dashboard save:", resp.get("success"), resp.get("error"))


asyncio.run(main())
