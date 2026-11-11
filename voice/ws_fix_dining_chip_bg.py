import asyncio, json, aiohttp
HA_WS = "ws://localhost:8123/api/websocket"
with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]

EXTRA = "\n:host { --chip-background-color: #e7eef7 !important; --chip-box-color: #e7eef7 !important; }\nmushroom-template-chip { --background: #e7eef7 !important; }\n"

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

            for target in ["dashboard-common-areas", "dashboard-roka", "dashboard-sian", "dashboard-rani"]:
                resp = await call({"type": "lovelace/config", "url_path": target})
                config = resp["result"]
                dining_view = next(v for v in config["views"] if v.get("title") == "Dining")
                header = dining_view["cards"][0]["cards"][0]
                if "--chip-background-color" not in header["card_mod"]["style"]:
                    header["card_mod"]["style"] += EXTRA
                sresp = await call({"type": "lovelace/config/save", "url_path": target, "config": config})
                print(target, "chip bg fix:", sresp.get("success"), sresp.get("error"))

asyncio.run(main())
