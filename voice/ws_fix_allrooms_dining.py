import asyncio, json, copy, aiohttp
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

            resp = await call({"type": "lovelace/config", "url_path": "dashboard-common-areas"})
            common_config = resp["result"]
            source_dining = next(v for v in common_config["views"] if v.get("title") == "Dining")
            good_cards = copy.deepcopy(source_dining["cards"])

            resp = await call({"type": "lovelace/config", "url_path": "dashboard-all-rooms"})
            config = resp["result"]
            dining_view = next(v for v in config["views"] if v.get("title") == "Dining")
            dining_view["cards"] = good_cards
            if "background" in source_dining:
                dining_view["background"] = source_dining["background"]

            sresp = await call({"type": "lovelace/config/save", "url_path": "dashboard-all-rooms", "config": config})
            print("dashboard-all-rooms dining fix:", sresp.get("success"), sresp.get("error"))

            # sanity check: confirm curtain tile present, lights-off tile absent
            cards = dining_view["cards"][0]["cards"]
            print("curtain present:", any(c.get("type") == "custom:curtain-tile-card" for c in cards))
            print("lights-off present:", any(c.get("type") == "tile" and c.get("entity") == "button.dining_all_lights_off" for c in cards))

asyncio.run(main())
