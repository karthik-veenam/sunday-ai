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
            async def call(payload):
                nonlocal mid
                mid += 1
                payload["id"] = mid
                await ws.send_json(payload)
                while True:
                    r = await ws.receive_json()
                    if r.get("id") == mid:
                        return r
            resp = await call({"type": "get_panels"})
            panels = resp["result"]
            for k, v in panels.items():
                print(k, "|", v.get("title"), "|", v.get("component_name"))
            print("---current admin user_data (sidebar keys)---")
            for key in ["sidebarPanelOrder", "sidebarHiddenPanels"]:
                r = await call({"type": "frontend/get_user_data", "key": key})
                print(key, "->", r.get("result"))
            print("---users list---")
            r = await call({"type": "config/auth/list"})
            for u in r["result"]:
                print(u.get("id"), u.get("name"), u.get("is_owner"), u.get("group_ids"), u.get("credentials"))

asyncio.run(main())
