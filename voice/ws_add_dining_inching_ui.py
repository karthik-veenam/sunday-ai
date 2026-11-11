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

            r = await call({"type": "lovelace/resources"})
            if not any("dining-ac-inching-card" in res["url"] for res in r["result"]):
                cr = await call({
                    "type": "lovelace/resources/create",
                    "url": "/local/dining-ac-inching-card.js",
                    "res_type": "module",
                })
                print("resource create:", cr.get("success"), cr.get("error"))
            else:
                print("resource already present")

            resp = await call({"type": "lovelace/config", "url_path": "dashboard-dining"})
            config = resp["result"]
            matches = find_all(config, lambda n: n.get("type") == "thermostat" and n.get("entity") == "climate.dining_ac")
            assert len(matches) == 1, matches
            _, thermo_card = matches[0]

            # Find the mod-card that directly wraps this thermostat as its "card"
            wrapper_matches = find_all(config, lambda n: n.get("type") == "custom:mod-card" and n.get("card") is thermo_card)
            assert len(wrapper_matches) == 1
            _, mod_card = wrapper_matches[0]

            inching_chip_row = {
                "type": "custom:mushroom-chips-card",
                "alignment": "end",
                "chips": [
                    {
                        "type": "template",
                        "entity": "input_boolean.dining_ac_inching",
                        "icon": "{{ 'mdi:timer-check-outline' if is_state('input_boolean.dining_ac_inching','on') else 'mdi:timer-outline' }}",
                        "icon_color": "{{ 'blue' if is_state('input_boolean.dining_ac_inching','on') else 'blue-grey' }}",
                        "content": "",
                        "tap_action": {
                            "action": "fire-dom-event",
                            "browser_mod": {
                                "service": "browser_mod.popup",
                                "data": {
                                    "title": "Dining AC · Auto-off",
                                    "content": {"type": "custom:dining-ac-inching-card"},
                                },
                            },
                        },
                    }
                ],
                "card_mod": {
                    "style": ":host { position: absolute; top: 6px; right: 2px; z-index: 2; --chip-height: 40px; --chip-icon-size: 25px; --chip-background: #e6eff8; --chip-border-color: transparent; --chip-box-shadow: none; } ha-card { border: none; box-shadow: none; background: transparent; }\n:host { --mush-chip-height: 40px; --mush-chip-icon-size: 25px; --mush-chip-background: #e6eff8; --mush-chip-border-color: transparent; --mush-chip-box-shadow: none; }\n"
                },
            }

            mod_card["card"] = {
                "type": "vertical-stack",
                "cards": [thermo_card, inching_chip_row],
                "card_mod": {"style": ":host { display: block; position: relative; }"},
            }
            # the mod-card's own card_mod sizing rules referenced hui-thermostat-card; broaden to also cover the stack
            if "card_mod" in mod_card and "style" in mod_card["card_mod"]:
                mod_card["card_mod"]["style"] += "\nha-card > hui-vertical-stack-card { display: block; width: 100%; max-width: 260px; }"

            sresp = await call({"type": "lovelace/config/save", "url_path": "dashboard-dining", "config": config})
            print("dashboard-dining save:", sresp.get("success"), sresp.get("error"))

asyncio.run(main())
