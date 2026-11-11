import asyncio
import json

import aiohttp

HA_WS = "ws://localhost:8123/api/websocket"

with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]

GEYSER = "switch.geyser_socket_1"
PROJECTOR = "switch.16amp_smart_plug_2_socket_1"


def entity_tile(entity, name, icon):
    return {
        "type": "custom:mushroom-entity-card",
        "entity": entity,
        "name": name,
        "icon": icon,
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

            resp = await call({"type": "lovelace/config", "url_path": "dashboard-mobile"})
            config = resp["result"]
            bedroom_view = next(v for v in config["views"] if v.get("path") == "bedroom")
            cards = bedroom_view["cards"]

            # Idempotent: drop a previous run's "Devices" section if present
            cut = next(
                (i for i, c in enumerate(cards) if c.get("type") == "heading" and c.get("heading") == "Devices"),
                None,
            )
            if cut is not None:
                del cards[cut : cut + 2]

            cards.append({"type": "heading", "heading": "Devices", "icon": "mdi:power-socket-eu"})
            cards.append(
                {
                    "type": "grid",
                    "columns": 2,
                    "square": False,
                    "cards": [
                        entity_tile(GEYSER, "Geyser", "mdi:water-boiler"),
                        entity_tile(PROJECTOR, "Projector", "mdi:projector"),
                    ],
                }
            )

            resp = await call({"type": "lovelace/config/save", "url_path": "dashboard-mobile", "config": config})
            print("Sunday dashboard devices save:", resp.get("success"), resp.get("error"))


asyncio.run(main())
