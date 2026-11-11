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

            await ws.send_json({"id": 2, "type": "frontend/get_themes"})
            while True:
                resp = await ws.receive_json()
                if resp.get("id") == 2:
                    break
            result = resp["result"]
            print("default_theme:", result.get("default_theme"))
            theme = result["themes"]["Caule Dark Blue"]
            keys = list(theme.keys())
            print("num keys:", len(keys))
            bg_related = [k for k in keys if "background" in k.lower() or "card" in k.lower()]
            print("background/card related keys:", bg_related)
            for k in bg_related[:20]:
                print(" ", k, "=", theme[k])
asyncio.run(main())
