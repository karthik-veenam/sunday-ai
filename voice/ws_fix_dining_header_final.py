import asyncio, json, aiohttp
HA_WS = "ws://localhost:8123/api/websocket"
with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]

CLEAN_STYLE = """:host {
   --chip-background: #e7eef7;
   --chip-border-color: transparent;
   --chip-box-shadow: none;
   --chip-height: 36px;
   --chip-spacing: 8px;
   --chip-font-size: 12px;
   --chip-font-weight: 600;
   --primary-text-color: #233247;
 }
 ha-card {
   padding: 12px 8px;
   background: #f9fbfe;
   border: 1px solid #d6e0eb;
   border-radius: 14px;
   box-shadow: 0 2px 8px rgba(43,65,91,0.06);
 }
 .chip-container { gap: 8px; }
 """

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
                header["card_mod"]["style"] = CLEAN_STYLE
                sresp = await call({"type": "lovelace/config/save", "url_path": target, "config": config})
                print(target, "header style replaced:", sresp.get("success"), sresp.get("error"))

asyncio.run(main())
