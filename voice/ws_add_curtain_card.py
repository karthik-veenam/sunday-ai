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

            r = await call({"type": "lovelace/resources"})
            existing = [res["url"] for res in r["result"]]
            print("existing resources:", existing)
            if not any("curtain-tile-card" in u for u in existing):
                r = await call({
                    "type": "lovelace/resources/create",
                    "url": "/local/curtain-tile-card.js",
                    "res_type": "module",
                })
                print("resource create:", r.get("success"), r.get("error"))
            else:
                print("resource already registered")

            new_tile = {
                "type": "custom:curtain-tile-card",
                "entity": "cover.dining_dining_balcony",
                "name": "Balcony",
                "duration": 8,
            }

            for target in ["dashboard-roka", "dashboard-sian", "dashboard-rani"]:
                resp = await call({"type": "lovelace/config", "url_path": target})
                config = resp["result"]
                dining_view = next(v for v in config["views"] if v.get("title") == "Dining")
                cards = dining_view["cards"][0]["cards"]
                idx = next(
                    i for i, c in enumerate(cards)
                    if c.get("entity") == "cover.dining_dining_balcony" and c.get("type") == "custom:mushroom-cover-card"
                )
                cards[idx] = new_tile
                sresp = await call({"type": "lovelace/config/save", "url_path": target, "config": config})
                print(target, "balcony tile swap:", sresp.get("success"), sresp.get("error"))

asyncio.run(main())
