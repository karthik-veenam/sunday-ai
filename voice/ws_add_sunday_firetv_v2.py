import asyncio
import json

import aiohttp

HA_WS = "ws://localhost:8123/api/websocket"

with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]

MP = "media_player.karthik_s_fire_tv"
REMOTE = "remote.karthik_s_fire_tv"
PROJECTOR = "switch.16amp_smart_plug_2_socket_1"

BTN_STYLE = (
    "ha-card {\n height: 54px; box-sizing: border-box; border: none; border-radius: 16px;\n"
    " background: #e7eef6; color: #294765; box-shadow: none;\n"
    " --primary-text-color: #294765; --state-icon-color: #294765;\n"
    " --ha-card-border-width: 0px;\n}\n"
    "ha-state-icon { --mdc-icon-size: 23px; width: 23px; height: 23px; }\n"
    ".name { font-size: 10px !important; font-weight: 600; letter-spacing: 0.2px; }\n"
    "ha-card { --mdc-icon-size: 24px; --ha-icon-size: 24px; }\n"
    "ha-state-icon, ha-icon, #icon { --mdc-icon-size: 24px !important; --ha-icon-size: 24px !important;"
    " width: 24px !important; height: 24px !important; }\n"
    "#name, .name { font-size: 11px !important; line-height: 16px !important; font-weight: 600 !important; }\n"
)

BTN_STYLE_BIG = BTN_STYLE + (
    "\nha-card { height: 66px !important; border-radius: 18px; }\n"
    "ha-state-icon, ha-icon, #icon { --mdc-icon-size: 26px !important; --ha-icon-size: 26px !important;"
    " width: 26px !important; height: 26px !important; }\nha-card { margin-top: 8px; margin-bottom: 8px; }"
)

HIDDEN_STYLE = "ha-card { visibility: hidden; height: 56px; border: none; box-shadow: none; }"


def remote_btn(name, icon, command, style=BTN_STYLE, extra_card_mod=""):
    return {
        "type": "button",
        "name": name,
        "icon": icon,
        "show_state": False,
        "show_name": False,
        "tap_action": {
            "action": "perform-action",
            "perform_action": "remote.send_command",
            "target": {"entity_id": REMOTE},
            "data": {"command": command},
        },
        "hold_action": {"action": "none"},
        "card_mod": {"style": style + extra_card_mod},
    }


def hidden_cell():
    return {
        "type": "button",
        "show_name": False,
        "show_icon": False,
        "tap_action": {"action": "none"},
        "card_mod": {"style": HIDDEN_STYLE},
    }


def media_btn(name, icon, service, style=BTN_STYLE_BIG):
    return {
        "type": "button",
        "name": name,
        "icon": icon,
        "show_state": False,
        "show_name": False,
        "tap_action": {
            "action": "perform-action",
            "perform_action": service,
            "target": {"entity_id": MP},
        },
        "hold_action": {"action": "none"},
        "card_mod": {"style": style},
    }


def dpad():
    ok_style = (
        BTN_STYLE.replace("background: #e7eef6", "background: #2869a8")
        .replace("--primary-text-color: #294765", "--primary-text-color: white")
        .replace("--state-icon-color: #294765", "--state-icon-color: white")
        + "\nha-card { height: 56px; border-radius: 50%; box-shadow: 0 4px 12px rgba(40,105,168,0.25); }"
        "\nha-card { color: white !important; --primary-text-color: white !important; --state-icon-color: white !important; }"
    )
    round_style = BTN_STYLE + "\nha-card { height: 56px; background: transparent; border-radius: 50%; } ha-state-icon { --mdc-icon-size: 30px; width: 30px; height: 30px; }"
    return {
        "type": "custom:mod-card",
        "card": {
            "type": "grid",
            "columns": 3,
            "square": True,
            "cards": [
                hidden_cell(),
                remote_btn("", "mdi:chevron-up", "up", style=round_style),
                hidden_cell(),
                remote_btn("", "mdi:chevron-left", "left", style=round_style),
                remote_btn("OK", "mdi:check", "select", style=ok_style),
                remote_btn("", "mdi:chevron-right", "right", style=round_style),
                hidden_cell(),
                remote_btn("", "mdi:chevron-down", "down", style=round_style),
                hidden_cell(),
            ],
        },
        "card_mod": {
            "style": (
                "ha-card {\n  width: 224px !important; height: 224px !important;\n"
                "  max-width: 100%; margin: 4px auto 12px;\n"
                "  padding: 12px; box-sizing: border-box; border-radius: 50%;\n"
                "  display: flex; align-items: center; justify-content: center;\n"
                "  background: #e4edf6; border: 1px solid #d0dfed;\n"
                "  box-shadow: inset 0 1px 0 rgba(255,255,255,.9);\n }\n ha-card > * { width: 100%; }\n"
            )
        },
    }


def popup_content():
    return {
        "type": "custom:mod-card",
        "card_mod": {
            "style": "ha-card { background: #f5f8fc !important; border-radius: 16px; padding: 14px; box-shadow: none; border: none; }"
        },
        "card": {
            "type": "vertical-stack",
            "cards": [
                {
                    "type": "custom:mod-card",
                    "card": {
                        "type": "vertical-stack",
                        "cards": [
                            {
                                "type": "custom:mushroom-template-card",
                                "primary": "Fire TV",
                                "secondary": "Karthik's Room",
                                "icon": "mdi:fire",
                                "icon_color": "blue",
                                "tap_action": {"action": "none"},
                                "card_mod": {
                                    "style": (
                                        "ha-card { background: transparent; border: none; box-shadow: none; padding: 0;"
                                        " --primary-text-color: #1e3248; --secondary-text-color: #6e8093; }\n"
                                        "ha-card { padding: 0 52px 0 0 !important; }"
                                    )
                                },
                            },
                            {
                                "type": "custom:mushroom-chips-card",
                                "chips": [
                                    {
                                        "type": "action",
                                        "icon": "mdi:power",
                                        "tap_action": {
                                            "action": "perform-action",
                                            "perform_action": "switch.toggle",
                                            "target": {"entity_id": PROJECTOR},
                                        },
                                    }
                                ],
                                "card_mod": {
                                    "style": (
                                        ":host { position: absolute; right: 0; top: 6px; width: 40px; height: 40px;"
                                        " --chip-height: 40px; --chip-icon-size: 22px; --chip-background: #f8e8e8;"
                                        " --chip-icon-color: #b44d52; --primary-text-color: #b44d52; }\n"
                                        " ha-card { padding: 0; border: none; box-shadow: none; }\n"
                                    )
                                },
                            },
                        ],
                        "card_mod": {"style": "#root { position: relative; gap: 0; } #root > * { margin: 0 !important; }"},
                    },
                    "card_mod": {"style": "ha-card { position: relative; background: transparent; border: none; box-shadow: none; padding: 0 0 8px; }"},
                },
                dpad(),
                {
                    "type": "grid",
                    "columns": 3,
                    "square": False,
                    "cards": [
                        remote_btn("Home", "mdi:home", "home", style=BTN_STYLE_BIG),
                        remote_btn("Back", "mdi:arrow-u-left-top", "back", style=BTN_STYLE_BIG),
                        remote_btn("Menu", "mdi:menu", "menu", style=BTN_STYLE_BIG),
                    ],
                },
                {
                    "type": "custom:mod-card",
                    "card": {
                        "type": "grid",
                        "columns": 3,
                        "square": False,
                        "cards": [
                            media_btn("", "mdi:volume-minus", "media_player.volume_down"),
                            remote_btn("Mute", "mdi:volume-off", "mute", style=BTN_STYLE_BIG),
                            media_btn("", "mdi:volume-plus", "media_player.volume_up"),
                        ],
                    },
                    "card_mod": {"style": "ha-card { background: #e7eef6; border: 1px solid #dae4ef; border-radius: 18px; padding: 2px; box-shadow: none; }\nha-card { margin: 14px 0 !important; padding: 6px 2px; }"},
                },
            ],
            "card_mod": {
                "style": (
                    ":host { --primary-text-color: #233247; --secondary-text-color: #6e8093;"
                    " --ha-card-background: #f5f8fc; --card-background-color: #f5f8fc; --divider-color: #d6e0eb; }"
                    " #root { gap: 12px; }\n#root { gap: 10px !important; }\n"
                    "#root { gap: 22px !important; } #root > * { margin-top: 0 !important; margin-bottom: 0 !important; }"
                )
            },
        },
    }


def firetv_card():
    return {
        "type": "custom:mod-card",
        "card_mod": {
            "style": (
                "ha-card {\n  background: {{ ('url(' ~ state_attr('" + MP + "','entity_picture') ~ ') center / cover no-repeat')"
                " if (is_state('" + MP + "','on') and state_attr('" + MP + "','entity_picture')) else"
                " ('linear-gradient(135deg, #56CCF2, #2F80ED)' if not is_state('" + MP + "','off') else 'var(--ha-card-background)') }} !important;\n}\n\n"
                ":host { --primary-text-color: #ffffff; --secondary-text-color: #edf6ff;"
                " --rgb-primary-text-color: 255, 255, 255; --rgb-secondary-text-color: 237, 246, 255; }\n"
                "ha-card { overflow: hidden; border-radius: 12px; border: 1px solid var(--ha-card-border-color); }\n"
                "ha-card { position: relative; }\nha-card { position: relative; }"
            )
        },
        "card": {
            "type": "vertical-stack",
            "cards": [
                {
                    "type": "custom:mushroom-media-player-card",
                    "entity": MP,
                    "use_media_info": False,
                    "media_controls": ["play_pause_stop"],
                    "volume_controls": ["volume_buttons"],
                    "collapsible_controls": True,
                    "card_mod": {
                        "style": (
                            "ha-card { background: transparent !important; border: none; box-shadow: none; }"
                            " :host { --primary-text-color: #fff; --secondary-text-color: #edf6ff;"
                            " --rgb-primary-text-color: 255,255,255; --rgb-secondary-text-color: 237,246,255; }\n"
                            "mushroom-state-item { padding-right: 90px; }\nmushroom-state-item { padding-right: 90px; }"
                        )
                    },
                },
                {
                    "type": "custom:mushroom-chips-card",
                    "alignment": "end",
                    "chips": [
                        {
                            "type": "action",
                            "icon": "mdi:power",
                            "tap_action": {
                                "action": "perform-action",
                                "perform_action": "switch.toggle",
                                "target": {"entity_id": PROJECTOR},
                            },
                        },
                        {
                            "type": "action",
                            "icon": "mdi:remote-tv",
                            "tap_action": {
                                "action": "fire-dom-event",
                                "browser_mod": {
                                    "service": "browser_mod.popup",
                                    "data": {"title": "TV Remote", "content": popup_content()},
                                },
                            },
                        },
                    ],
                    "card_mod": {
                        "style": (
                            ":host { position: absolute; right: 10px; top: 10px; width: auto; }\n"
                            "ha-card { padding: 0; border: none; box-shadow: none; background: transparent; }"
                        )
                    },
                },
            ],
        },
    }


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

            resp = await call({"type": "lovelace/config", "url_path": "dashboard-mobile"})
            config = resp["result"]
            bedroom_view = next(v for v in config["views"] if v.get("path") == "bedroom")

            # Remove any earlier attempt's cards: a standalone heading("Fire TV") + the
            # card that followed it (first version), or a previously-inserted conditional
            # block (this version, on a re-run).
            cards = bedroom_view["cards"]
            cards[:] = [c for c in cards if not (c.get("type") == "conditional" and c.get("_firetv_block"))]
            cut = next(
                (i for i, c in enumerate(cards) if c.get("type") == "heading" and c.get("heading") == "Fire TV"),
                None,
            )
            if cut is not None:
                del cards[cut : cut + 2]

            firetv_block = {
                "type": "conditional",
                "_firetv_block": True,
                "conditions": [{"entity": PROJECTOR, "state": "on"}],
                "card": {
                    "type": "vertical-stack",
                    "cards": [
                        {"type": "heading", "heading": "Fire TV", "icon": "mdi:remote-tv"},
                        firetv_card(),
                    ],
                },
            }

            cards.insert(0, firetv_block)

            resp = await call({"type": "lovelace/config/save", "url_path": "dashboard-mobile", "config": config})
            print("Sunday dashboard save:", resp.get("success"), resp.get("error"))


asyncio.run(main())
