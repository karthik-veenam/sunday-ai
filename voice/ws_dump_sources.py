import asyncio, json, aiohttp
HA_WS = "ws://localhost:8123/api/websocket"
with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]

PATHS = ["dashboard-mobile", "dashboard-hall", "dashboard-dining", "dashboard-kitchen",
         "dashboard-sian", "dashboard-rani", "dashboard-roka"]

async def main():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(HA_WS) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": TOKEN})
            msg = await ws.receive_json()
            assert msg["type"] == "auth_ok", msg
            mid = 1
            for p in PATHS:
                mid += 1
                await ws.send_json({"type": "lovelace/config", "url_path": p, "id": mid})
                resp = await ws.receive_json()
                views = resp["result"].get("views", [])
                print(p, "-> views:", [(v.get("title"), v.get("path"), v.get("type"), len(v.get("cards", []))) for v in views])
                with open(f"/home/djpi/sunday/src_{p}.json", "w") as f:
                    json.dump(resp["result"], f)

asyncio.run(main())
