import asyncio, json, aiohttp
HA_WS = "ws://localhost:8123/api/websocket"
with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]

def find_all(node, pred, path=""):
    results = []
    if isinstance(node, dict):
        if pred(node):
            results.append((path, node))
        for k, v in node.items():
            results.extend(find_all(v, pred, path + "/" + str(k)))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            results.extend(find_all(item, pred, path + f"[{i}]"))
    return results

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
            config = resp["result"]
            with open("/home/djpi/sunday/dashboard_dining_full.json", "w") as f:
                json.dump(config, f, indent=1)
            matches = find_all(config, lambda n: n.get("entity") == "climate.dining_ac" and n.get("type") == "thermostat")
            for path, node in matches:
                print("THERMOSTAT PATH:", path)

asyncio.run(main())
