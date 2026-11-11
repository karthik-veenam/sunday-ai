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

            r = await call({"type": "config/entity_registry/list"})
            ents = [e for e in r["result"] if e.get("platform") == "androidtv_remote"]
            for e in ents:
                print(e["entity_id"], "| platform:", e.get("platform"))

            r = await call({"type": "get_services"})
            services = r["result"]
            print("--- remote domain full service list (all platforms share this namespace) ---")
            for name, meta in services.get("remote", {}).items():
                print(name, "| fields:", list(meta.get("fields", {}).keys()))

            # check if there's any androidtv_remote-specific service namespace
            for dom in services:
                if "androidtv" in dom.lower():
                    print(dom, services[dom])

asyncio.run(main())
