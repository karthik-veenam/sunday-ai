import asyncio
import copy
import json

import aiohttp

HA_WS = "ws://localhost:8123/api/websocket"

with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]


def load(path):
    with open(f"/home/djpi/sunday/src_{path}.json") as f:
        return json.load(f)


def view_for(src_config, src_view_index, title, path):
    v = copy.deepcopy(src_config["views"][src_view_index])
    v["title"] = title
    v["path"] = path
    v.pop("icon", None)
    return v


async def main():
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

            mobile = load("dashboard-mobile")
            hall = load("dashboard-hall")
            dining = load("dashboard-dining")
            kitchen = load("dashboard-kitchen")
            sian = load("dashboard-sian")
            rani = load("dashboard-rani")

            # --- RoKa combined dashboard: Sunday, Hall, Dining, Kitchen ---
            roka_resp = await call({"type": "lovelace/config", "url_path": "dashboard-roka"})
            roka_cfg = roka_resp["result"]
            roka_cfg["views"] = [
                view_for(mobile, 0, "Sunday", "sunday"),
                view_for(hall, 0, "Hall", "hall"),
                view_for(dining, 0, "Dining", "dining"),
                view_for(kitchen, 0, "Kitchen", "kitchen"),
            ]
            resp = await call({"type": "lovelace/config/save", "url_path": "dashboard-roka", "config": roka_cfg})
            print("dashboard-roka save:", resp.get("success"), resp.get("error"))

            # --- SiAn combined dashboard: SiAn, Hall, Dining, Kitchen ---
            sian_resp = await call({"type": "lovelace/config", "url_path": "dashboard-sian"})
            sian_cfg = sian_resp["result"]
            sian_cfg["views"] = [
                view_for(sian, 0, "SiAn", "sian"),
                view_for(hall, 0, "Hall", "hall"),
                view_for(dining, 0, "Dining", "dining"),
                view_for(kitchen, 0, "Kitchen", "kitchen"),
            ]
            resp = await call({"type": "lovelace/config/save", "url_path": "dashboard-sian", "config": sian_cfg})
            print("dashboard-sian save:", resp.get("success"), resp.get("error"))

            # --- Rani combined dashboard: Hall, Kitchen, Rani, Dining ---
            rani_resp = await call({"type": "lovelace/config", "url_path": "dashboard-rani"})
            rani_cfg = rani_resp["result"]
            rani_cfg["views"] = [
                view_for(hall, 0, "Hall", "hall"),
                view_for(kitchen, 0, "Kitchen", "kitchen"),
                view_for(rani, 0, "Rani", "rani"),
                view_for(dining, 0, "Dining", "dining"),
            ]
            resp = await call({"type": "lovelace/config/save", "url_path": "dashboard-rani", "config": rani_cfg})
            print("dashboard-rani save:", resp.get("success"), resp.get("error"))


asyncio.run(main())
