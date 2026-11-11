"""Adds an 'Edit Sunday's prompt' button to the 'sunday-body' dashboard (read-modify-write, timestamped backup, idempotent)."""
import asyncio, json, os, time, aiohttp
TOKEN = json.load(open(os.path.expanduser("~/sunday/config.json")))["ha_token"]
URL = "http://192.168.88.108:8095/"
CARDS = [{"type": "heading", "heading": "Sunday's brain", "heading_style": "title", "icon": "mdi:brain"},
         {"type": "custom:mushroom-template-card", "primary": "Sunday prompt", "secondary": "Edit and save her instructions",
          "icon": "mdi:text-box-edit-outline", "icon_color": "indigo", "tap_action": {"action": "url", "url_path": URL}}]
async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("ws://localhost:8123/api/websocket") as ws:
            await ws.receive_json(); await ws.send_json({"type": "auth", "access_token": TOKEN}); assert (await ws.receive_json())["type"] == "auth_ok"
            n = 1
            async def call(p):
                nonlocal n
                n += 1; p["id"] = n; await ws.send_json(p)
                while True:
                    r = await ws.receive_json()
                    if r.get("id") == n:
                        assert r.get("success"), r.get("error"); return r.get("result")
            cfg = await call({"type": "lovelace/config", "url_path": "sunday-body"})
            cards = cfg["views"][0]["cards"]
            if any(c.get("tap_action", {}).get("url_path") == URL for c in cards):
                print("link already on the dashboard"); return
            bak = os.path.expanduser("~/sunday2-rebuild/state/sunday-body-dashboard.backup-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
            json.dump(cfg, open(bak, "w"), indent=1); print("previous dashboard saved to", bak)
            cfg["views"][0]["cards"] = cards + CARDS
            await call({"type": "lovelace/config/save", "url_path": "sunday-body", "config": cfg})
            print("button added to the Sunday Body dashboard")
asyncio.run(main())
