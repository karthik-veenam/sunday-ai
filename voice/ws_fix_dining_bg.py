import asyncio, json, aiohttp
HA_WS = "ws://localhost:8123/api/websocket"
with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]

BG = "linear-gradient(155deg, #edf4fa 0%, #e2ebf5 55%, #eef2f8 100%) fixed"

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

            for target in ["dashboard-roka", "dashboard-sian", "dashboard-rani"]:
                tresp = await call({"type": "lovelace/config", "url_path": target})
                tconfig = tresp["result"]
                tview = next(v for v in tconfig["views"] if v.get("title") == "Dining")
                tview["background"] = BG
                sresp = await call({"type": "lovelace/config/save", "url_path": target, "config": tconfig})
                print(target, "dining background fix:", sresp.get("success"), sresp.get("error"))

asyncio.run(main())
