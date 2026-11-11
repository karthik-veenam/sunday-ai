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
            await ws.send_json({"type": "lovelace/config", "url_path": "dashboard-mobile", "id": 2})
            resp = await ws.receive_json()
            with open("/home/djpi/sunday/sunday_dashboard_full2.json", "w") as out:
                json.dump(resp["result"], out, indent=2)
            print("saved")

asyncio.run(main())
