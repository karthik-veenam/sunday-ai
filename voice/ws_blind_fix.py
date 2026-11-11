import asyncio, json, copy, aiohttp
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

            # Get Hall's exact header card and exact thermostat features (source of truth)
            resp = await call({"type": "lovelace/config", "url_path": "dashboard-common-areas"})
            common = resp["result"]
            hall_view = next(v for v in common["views"] if v.get("title") == "Hall")
            hall_header = hall_view["cards"][0]["cards"][0]
            hall_thermo_matches = find_all(hall_view, lambda n: n.get("type") == "thermostat")
            hall_features = hall_thermo_matches[0][1]["features"]
            print("Hall features:", json.dumps(hall_features))
            print("Hall header card_mod style (verbatim) stored for reuse.")

            for target in ["dashboard-common-areas", "dashboard-roka", "dashboard-sian", "dashboard-rani", "dashboard-dining"]:
                resp = await call({"type": "lovelace/config", "url_path": target})
                config = resp["result"]
                if target == "dashboard-dining":
                    dining_view = config["views"][0]
                else:
                    dining_view = next(v for v in config["views"] if v.get("title") == "Dining")

                # 1) blindly copy Hall's header card_mod style verbatim (keep Dining's own chips content)
                header = dining_view["cards"][0]["cards"][0]
                header["card_mod"] = copy.deepcopy(hall_header["card_mod"])

                # 2) remove the extra target-temperature buttons feature so AC matches Hall's thermostat exactly
                thermo_matches = find_all(dining_view, lambda n: n.get("type") == "thermostat")
                for _, thermo in thermo_matches:
                    thermo["features"] = copy.deepcopy(hall_features)

                sresp = await call({"type": "lovelace/config/save", "url_path": target, "config": config})
                print(target, "blind fix:", sresp.get("success"), sresp.get("error"))

asyncio.run(main())
