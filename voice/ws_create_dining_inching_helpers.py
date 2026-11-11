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

            r = await call({
                "type": "input_boolean/create",
                "name": "Dining AC Inching",
                "icon": "mdi:timer-outline",
            })
            print("input_boolean dining_ac_inching:", r.get("success"), r.get("result"), r.get("error"))

            r = await call({
                "type": "input_boolean/create",
                "name": "Dining AC Inching Armed",
                "icon": "mdi:timer-outline",
            })
            print("input_boolean dining_ac_inching_armed:", r.get("success"), r.get("result"), r.get("error"))

            r = await call({
                "type": "input_number/create",
                "name": "Dining AC Inching Minutes",
                "min": 1,
                "max": 1439,
                "step": 1,
                "mode": "box",
                "unit_of_measurement": "min",
                "icon": "mdi:timer-cog-outline",
            })
            print("input_number dining_ac_inching_minutes:", r.get("success"), r.get("result"), r.get("error"))

            r = await call({
                "type": "input_datetime/create",
                "name": "Dining AC Inching Deadline",
                "has_date": True,
                "has_time": True,
                "icon": "mdi:clock-end",
            })
            print("input_datetime dining_ac_inching_deadline:", r.get("success"), r.get("result"), r.get("error"))

            # turn the new inching boolean ON by default to match Hall's current enabled state
            r = await call({
                "type": "input_number/set_value",
                "entity_id": "input_number.dining_ac_inching_minutes",
                "value": 30,
            })
            print("set default minutes:", r.get("success"), r.get("error"))

asyncio.run(main())
