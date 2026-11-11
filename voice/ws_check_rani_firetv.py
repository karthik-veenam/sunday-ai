import asyncio
import json

import aiohttp

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

            resp = await call({"type": "config/entity_registry/list"})
            entities = resp["result"]
            rani_entities = [e for e in entities if "rani" in e.get("entity_id", "").lower() and (
                "fire" in e.get("entity_id", "").lower() or "tv" in e.get("entity_id", "").lower()
            )]
            for e in rani_entities:
                print(e["entity_id"], "| platform:", e.get("platform"), "| config_entry_id:", e.get("config_entry_id"), "| unique_id:", e.get("unique_id"))

            config_entry_ids = set(e.get("config_entry_id") for e in rani_entities if e.get("config_entry_id"))

            resp = await call({"type": "config_entries/get"})
            all_entries = resp["result"]
            for ce in all_entries:
                if ce.get("entry_id") in config_entry_ids:
                    print("\nConfig entry:", json.dumps(ce, indent=2))


asyncio.run(main())
