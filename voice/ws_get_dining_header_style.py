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
            config = resp["result"]
            kitchen_view = next(v for v in config["views"] if v.get("title") == "Kitchen")
            dining_view = next(v for v in config["views"] if v.get("title") == "Dining")
            print("KITCHEN style:\n", kitchen_view["cards"][0]["cards"][0]["card_mod"]["style"])
            print("\nDINING style:\n", dining_view["cards"][0]["cards"][0]["card_mod"]["style"])

asyncio.run(main())
