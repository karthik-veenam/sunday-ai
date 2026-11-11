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

            r = await call({"type": "lovelace/config", "url_path": "dashboard-roka"})
            views = r["result"].get("views", [])
            print("dashboard-roka views now:", [(v.get("title"), v.get("path")) for v in views])

            r = await call({"type": "get_panels"})
            p = r["result"].get("dashboard-roka")
            print("panel entry:", json.dumps(p, indent=1))

            r = await call({"type": "lovelace/dashboards/list"})
            for d in r["result"]:
                if d.get("url_path") in ("dashboard-roka","dashboard-sian","dashboard-rani"):
                    print(d)

asyncio.run(main())
