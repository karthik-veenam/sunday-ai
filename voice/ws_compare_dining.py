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

            resp = await call({"type": "lovelace/config", "url_path": "dashboard-common-areas"})
            common_views = resp["result"]["views"]
            dining_view = next(v for v in common_views if v.get("title") == "Dining")
            print("=== Common Areas Dining view (top-level keys) ===")
            print({k: v for k, v in dining_view.items() if k != "cards"})

            resp2 = await call({"type": "lovelace/config", "url_path": "dashboard-roka"})
            roka_views = resp2["result"]["views"]
            roka_dining = next(v for v in roka_views if v.get("title") == "Dining")
            print("=== RoKa Dining view (top-level keys) ===")
            print({k: v for k, v in roka_dining.items() if k != "cards"})

asyncio.run(main())
