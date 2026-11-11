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
            common_views = resp["result"]["views"]
            dining_view = next(v for v in common_views if v.get("title") == "Dining")
            print("Found Common Areas Dining view, cards:", len(dining_view.get("cards", [])))
            dining_cards = copy.deepcopy(dining_view["cards"])

            for target in ["dashboard-roka", "dashboard-sian", "dashboard-rani"]:
                tresp = await call({"type": "lovelace/config", "url_path": target})
                tconfig = tresp["result"]
                tview = next(v for v in tconfig["views"] if v.get("title") == "Dining")
                tview["cards"] = copy.deepcopy(dining_cards)
                tview["type"] = dining_view.get("type")
                sresp = await call({"type": "lovelace/config/save", "url_path": target, "config": tconfig})
                print(target, "dining view updated:", sresp.get("success"), sresp.get("error"))

asyncio.run(main())
