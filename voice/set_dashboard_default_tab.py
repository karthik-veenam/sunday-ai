import asyncio
import json
import sys

import aiohttp

HA_WS = "ws://localhost:8123/api/websocket"

with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]

TARGETS = {
    "dashboard-roka": {"night": "Sunday", "day": "Hall"},
    "dashboard-sian": {"night": "SiAn", "day": "Hall"},
    "dashboard-rani": {"night": "Rani", "day": "Hall"},
}


def move_to_front(views, title):
    match = [v for v in views if v.get("title") == title]
    rest = [v for v in views if v.get("title") != title]
    return match + rest


async def main():
    period = sys.argv[1]
    assert period in ("night", "day"), "usage: set_dashboard_default_tab.py night|day"

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(HA_WS) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": TOKEN})
            msg = await ws.receive_json()
            assert msg["type"] == "auth_ok", msg

            msg_id = 1

            async def call(payload):
                nonlocal msg_id
                msg_id += 1
                payload["id"] = msg_id
                await ws.send_json(payload)
                while True:
                    resp = await ws.receive_json()
                    if resp.get("id") == msg_id:
                        return resp

            for url_path, periods in TARGETS.items():
                target_title = periods[period]
                resp = await call({"type": "lovelace/config", "url_path": url_path})
                config = resp["result"]
                config["views"] = move_to_front(config["views"], target_title)
                sresp = await call({"type": "lovelace/config/save", "url_path": url_path, "config": config})
                print(url_path, "->", target_title, "first:", sresp.get("success"), sresp.get("error"))


asyncio.run(main())
