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
            await ws.send_json({"type": "lovelace/dashboards/list", "id": 2})
            resp = await ws.receive_json()
            print(json.dumps(resp["result"][:3], indent=1))
            print("--- get_panels for dashboard-roka ---")
            await ws.send_json({"type": "get_panels", "id": 3})
            resp2 = await ws.receive_json()
            print(json.dumps(resp2["result"]["dashboard-roka"], indent=1))
            print(json.dumps(resp2["result"]["dashboard-mobile"], indent=1))

asyncio.run(main())
