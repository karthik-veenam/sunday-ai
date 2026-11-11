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

            resp = await call({"type": "lovelace/config", "url_path": "dashboard-dining"})
            dining_cards = resp["result"]["views"][0]["cards"]

            # Common Areas: Dining is one of its views
            resp = await call({"type": "lovelace/config", "url_path": "dashboard-common-areas"})
            common_config = resp["result"]
            dining_view = next(v for v in common_config["views"] if v.get("title") == "Dining")
            dining_view["cards"] = copy.deepcopy(dining_cards)
            sresp = await call({"type": "lovelace/config/save", "url_path": "dashboard-common-areas", "config": common_config})
            print("common-areas dining sync:", sresp.get("success"), sresp.get("error"))

            for target in ["dashboard-roka", "dashboard-sian", "dashboard-rani"]:
                resp = await call({"type": "lovelace/config", "url_path": target})
                tconfig = resp["result"]
                tview = next(v for v in tconfig["views"] if v.get("title") == "Dining")
                tview["cards"] = copy.deepcopy(dining_cards)
                sresp = await call({"type": "lovelace/config/save", "url_path": target, "config": tconfig})
                print(target, "dining sync:", sresp.get("success"), sresp.get("error"))

asyncio.run(main())
