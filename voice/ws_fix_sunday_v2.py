import asyncio
import json

import aiohttp

HA_WS = "ws://localhost:8123/api/websocket"

with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]


def find(node, pred):
    """DFS to find first dict matching pred, mutating in place via returned ref."""
    if isinstance(node, dict):
        if pred(node):
            return node
        for v in node.values():
            found = find(v, pred)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find(item, pred)
            if found is not None:
                return found
    return None


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

            # --- Fix 1: stop the mushroom-media-player-card's own collapse chevron
            # from eating the click meant for our power chip overlay, and make our
            # chip row paint on top regardless.
            mp_card = find(
                cards,
                lambda n: n.get("type") == "custom:mushroom-media-player-card"
                and n.get("entity") == "media_player.karthik_s_fire_tv",
            )
            assert mp_card is not None, "fire tv media player card not found"
            mp_card["collapsible_controls"] = False

            chip_row = find(
                cards,
                lambda n: n.get("type") == "custom:mushroom-chips-card"
                and any(c.get("icon") == "mdi:power" for c in n.get("chips", []))
                and any(c.get("icon") == "mdi:remote-tv" for c in n.get("chips", [])),
            )
            assert chip_row is not None, "power/remote chip row not found"
            chip_row["card_mod"]["style"] = (
                ":host { position: absolute; right: 10px; top: 10px; width: auto; z-index: 20; }\n"
                "ha-card { padding: 0; border: none; box-shadow: none; background: transparent; }"
            )

            # --- Fix 2: move the Devices section to just above "Ambient Lights"
            dev_cut = next(
                (i for i, c in enumerate(cards) if c.get("type") == "heading" and c.get("heading") == "Devices"),
                None,
            )
            assert dev_cut is not None, "Devices heading not found"
            devices_block = cards[dev_cut : dev_cut + 2]
            del cards[dev_cut : dev_cut + 2]

            ambient_idx = next(
                i for i, c in enumerate(cards) if c.get("type") == "heading" and c.get("heading") == "Ambient Lights"
            )
            cards[ambient_idx:ambient_idx] = devices_block

            resp = await call({"type": "lovelace/config/save", "url_path": "dashboard-mobile", "config": config})
            print("Sunday dashboard fix save:", resp.get("success"), resp.get("error"))


asyncio.run(main())
