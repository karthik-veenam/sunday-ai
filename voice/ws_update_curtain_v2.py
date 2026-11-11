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
            for res in r["result"]:
                if "curtain-tile-card" in res["url"]:
                    ur = await call({
                        "type": "lovelace/resources/update",
                        "resource_id": res["id"],
                        "url": "/local/curtain-tile-card.js?v=2",
                    })
                    print("resource bump:", ur.get("success"), ur.get("error"))

            new_tile = {
                "type": "custom:curtain-tile-card",
                "entity": "cover.dining_dining_balcony",
                "name": "Balcony",
                "open_duration": 13.8,
                "close_duration": 13.7,
            }

            for target in ["dashboard-roka", "dashboard-sian", "dashboard-rani"]:
                resp = await call({"type": "lovelace/config", "url_path": target})
                config = resp["result"]
                dining_view = next(v for v in config["views"] if v.get("title") == "Dining")
                cards = dining_view["cards"][0]["cards"]
                idx = next(
                    i for i, c in enumerate(cards)
                    if c.get("type") == "custom:curtain-tile-card" and c.get("entity") == "cover.dining_dining_balcony"
                )
                cards[idx] = new_tile
                sresp = await call({"type": "lovelace/config/save", "url_path": target, "config": config})
                print(target, "balcony tile v2 update:", sresp.get("success"), sresp.get("error"))

asyncio.run(main())
