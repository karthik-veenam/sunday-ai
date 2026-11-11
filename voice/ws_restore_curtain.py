import asyncio, json, aiohttp
HA_WS = "ws://localhost:8123/api/websocket"
with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]

CURTAIN_TILE = {
    "type": "custom:curtain-tile-card",
    "entity": "cover.dining_dining_balcony",
    "name": "Balcony",
    "open_duration": 13.8,
    "close_duration": 13.7,
}

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
                # avoid double-inserting if re-run
                if any(c.get("type") == "custom:curtain-tile-card" for c in cards):
                    print(target, "already has curtain tile, skipping")
                    continue
                # insert right before the trailing "All Lights Off" tile card
                insert_at = len(cards)
                for i, c in enumerate(cards):
                    if c.get("type") == "tile":
                        insert_at = i
                        break
                cards.insert(insert_at, CURTAIN_TILE)
                sresp = await call({"type": "lovelace/config/save", "url_path": target, "config": config})
                print(target, "curtain restore:", sresp.get("success"), sresp.get("error"))

asyncio.run(main())
