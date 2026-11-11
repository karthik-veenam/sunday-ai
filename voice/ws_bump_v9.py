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
                        "url": "/local/curtain-tile-card.js?v=9",
                    })
                    print("resource bump v9:", ur.get("success"), ur.get("error"))

asyncio.run(main())
