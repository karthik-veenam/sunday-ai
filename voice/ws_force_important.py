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

FORCED_HEADER_STYLE = """:host {
   --chip-background: #e7eef7 !important;
   --chip-border-color: transparent !important;
   --chip-box-shadow: none !important;
   --chip-height: 36px;
   --chip-spacing: 8px;
   --chip-font-size: 12px;
   --chip-font-weight: 600;
   --primary-text-color: #233247 !important;
 }
 ha-card {
   padding: 12px 8px;
   background: #f9fbfe !important;
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

            for target in ["dashboard-common-areas", "dashboard-roka", "dashboard-sian", "dashboard-rani", "dashboard-dining"]:
                resp = await call({"type": "lovelace/config", "url_path": target})
                config = resp["result"]
                if target == "dashboard-dining":
                    dining_view = config["views"][0]
                else:
                    dining_view = next(v for v in config["views"] if v.get("title") == "Dining")

                header = dining_view["cards"][0]["cards"][0]
                header["card_mod"]["style"] = FORCED_HEADER_STYLE

                # also force the TV power/remote chip row light (the one that appeared dark too)
                tv_chip_rows = find_all(dining_view, lambda n: n.get("type") == "custom:mushroom-chips-card" and any(c.get("icon") == "mdi:power" for c in n.get("chips", [])))
                for _, row in tv_chip_rows:
                    style = row.get("card_mod", {}).get("style", "")
                    if "!important" not in style:
                        row.setdefault("card_mod", {})["style"] = style + "\n:host { --chip-background: #e6eff8 !important; } ha-card { background: transparent !important; }"

                sresp = await call({"type": "lovelace/config/save", "url_path": target, "config": config})
                print(target, "forced fix:", sresp.get("success"), sresp.get("error"))

asyncio.run(main())
