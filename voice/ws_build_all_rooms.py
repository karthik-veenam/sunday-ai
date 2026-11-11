import asyncio, json, copy, aiohttp
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

            def view_from(config, title_match, new_title, new_path):
                v = next((vv for vv in config["views"] if vv.get("title") == title_match), config["views"][0])
                v = copy.deepcopy(v)
                v["title"] = new_title
                v["path"] = new_path
                v.pop("icon", None)
                return v

            hall_cfg = (await call({"type": "lovelace/config", "url_path": "dashboard-hall"}))["result"]
            kitchen_cfg = (await call({"type": "lovelace/config", "url_path": "dashboard-kitchen"}))["result"]
            dining_cfg = (await call({"type": "lovelace/config", "url_path": "dashboard-dining"}))["result"]
            rani_cfg = (await call({"type": "lovelace/config", "url_path": "dashboard-rani"}))["result"]
            sian_cfg = (await call({"type": "lovelace/config", "url_path": "dashboard-sian"}))["result"]
            mobile_cfg = (await call({"type": "lovelace/config", "url_path": "dashboard-mobile"}))["result"]

            views = [
                view_from(hall_cfg, "Hall", "Hall", "hall"),
                view_from(kitchen_cfg, "Kitchen", "Kitchen", "kitchen"),
                view_from(dining_cfg, "Dining", "Dining", "dining"),
                view_from(rani_cfg, "Rani", "Rani", "rani"),
                view_from(sian_cfg, "SiAn", "SiAn", "sian"),
                view_from(mobile_cfg, "Sunday", "Sunday", "sunday"),
            ]

            new_config = {"title": "All Rooms", "views": views}

            # Create the dashboard if it doesn't already exist
            dlist = (await call({"type": "lovelace/dashboards/list"}))["result"]
            if not any(d.get("url_path") == "dashboard-all-rooms" for d in dlist):
                cr = await call({
                    "type": "lovelace/dashboards/create",
                    "url_path": "dashboard-all-rooms",
                    "title": "All Rooms",
                    "icon": "mdi:home-group",
                    "show_in_sidebar": True,
                    "require_admin": False,
                    "mode": "storage",
                })
                print("dashboard create:", cr.get("success"), cr.get("error"))
            else:
                print("dashboard already exists")

            sresp = await call({"type": "lovelace/config/save", "url_path": "dashboard-all-rooms", "config": new_config})
            print("config save:", sresp.get("success"), sresp.get("error"))

asyncio.run(main())
