import asyncio, json, aiohttp
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
            mid = 1
            async def call(p):
                nonlocal mid
                mid += 1
                p["id"] = mid
                await ws.send_json(p)
                while True:
                    r = await ws.receive_json()
                    if r.get("id") == mid:
                        return r

            for target in ["dashboard-common-areas", "dashboard-roka", "dashboard-sian", "dashboard-rani"]:
                resp = await call({"type": "lovelace/config", "url_path": target})
                config = resp["result"]
                dining_view = next(v for v in config["views"] if v.get("title") == "Dining")
                cards = dining_view["cards"][0]["cards"]
                before = len(cards)
                cards[:] = [c for c in cards if not (c.get("type") == "tile" and c.get("entity") == "button.dining_all_lights_off")]
                after = len(cards)
                sresp = await call({"type": "lovelace/config/save", "url_path": target, "config": config})
                print(target, f"removed {before-after} tile(s):", sresp.get("success"), sresp.get("error"))

asyncio.run(main())
