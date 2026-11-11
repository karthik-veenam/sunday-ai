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
            ents = [e for e in r["result"] if "bravia" in e.get("entity_id","").lower()]
            for e in ents:
                print(e["entity_id"], "| platform:", e.get("platform"), "| config_entry_id:", e.get("config_entry_id"))

            ce_ids = {e.get("config_entry_id") for e in ents if e.get("config_entry_id")}
            r = await call({"type": "config_entries/get"})
            for ce in r["result"]:
                if ce.get("entry_id") in ce_ids:
                    print("config entry:", ce.get("domain"), ce.get("title"))

            # List available services on remote and media_player domains related to bravia
            r = await call({"type": "get_services"})
            services = r["result"]
            print("--- remote domain services ---")
            print(list(services.get("remote", {}).keys()))
            print("--- any braviatv-specific service namespace ---")
            for dom in services:
                if "bravia" in dom.lower() or "sony" in dom.lower():
                    print(dom, list(services[dom].keys()))

asyncio.run(main())
