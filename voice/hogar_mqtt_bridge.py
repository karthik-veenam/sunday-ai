"""
MQTT bridge: exposes Hogar devices as native Home Assistant entities via MQTT
Discovery, so they show up as first-class HA entities instead of only living
in the custom house.py dashboard. Covers Roka's Room (grouped as one HA
device) and the common areas — Hall, Kitchen, Dining — each grouped as its
own HA device with a suggested_area, so HA auto-creates and assigns the
matching Area. Entity names are the device's own label with no room name
baked in, since the Area/Device grouping already conveys the room.

Commands sent from HA relay to the real Hogar hub; live state changes push
back to HA so HA's UI stays in sync. Standalone process — does not touch
house.py, main.py, or any other running service.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hogar
import paho.mqtt.client as mqtt

MQTT_HOST = "localhost"
MQTT_PORT = 1883
DISCOVERY_PREFIX = "homeassistant"

DEVICE_INFO = {
    "identifiers": ["hogar_roka_room"],
    "name": "Roka's Room",
    "manufacturer": "Hogar",
    "model": "Z-Wave",
}

# Hogar devid suffix -> (HA object_id, friendly name)
LIGHTS = {
    "9-1": ("roka_light_1", "Roka Light 1"),
    "9-2": ("roka_light_2", "Roka Light 2"),
    "9-3": ("roka_spots", "Roka Spots"),
    "9-5": ("roka_foot_lamp", "Roka Foot Lamp"),
    "9-6": ("roka_cove", "Roka Cove"),
}
FAN = {"9-9": ("roka_fan", "Roka Fan")}

# Common-area devices (Hall, Kitchen, Dining) — one HA device (and, via
# suggested_area, one HA Area) per room. Entity names are the device's own
# label, with no room name baked in — the Area/Device grouping already
# conveys the room.
ROOM_DEVICE_INFO = {
    "hall": {"identifiers": ["hogar_hall"], "name": "Hall", "manufacturer": "Hogar",
             "model": "Z-Wave", "suggested_area": "Hall"},
    "kitchen": {"identifiers": ["hogar_kitchen"], "name": "Kitchen", "manufacturer": "Hogar",
                "model": "Z-Wave", "suggested_area": "Kitchen"},
    "dining": {"identifiers": ["hogar_dining"], "name": "Dining", "manufacturer": "Hogar",
               "model": "Z-Wave", "suggested_area": "Dining"},
    "sian": {"identifiers": ["hogar_sian_bedroom"], "name": "SiAn's Bedroom", "manufacturer": "Hogar",
             "model": "Z-Wave", "suggested_area": "SiAn Room"},
    "rani": {"identifiers": ["hogar_rani_bedroom"], "name": "Rani's Bedroom", "manufacturer": "Hogar",
             "model": "Z-Wave", "suggested_area": "Rani's Bedroom"},
}

# suffix -> (object_id, clean name, room_key)
COMMON_SWITCH_LIGHTS = {
    "2-6": ("hall_2_6", "TV Cove", "hall"),
    "4-2": ("kitchen_4_2", "Pooja", "kitchen"),
    "4-3": ("kitchen_4_3", "Utility", "kitchen"),
    "4-5": ("kitchen_4_5", "Dishwash Light", "kitchen"),
    "4-6": ("kitchen_4_6", "Cove Light", "kitchen"),
    "6-2": ("dining_6_2", "Walkway", "dining"),
    "6-5": ("dining_6_5", "Cove Light", "dining"),
    "10-1": ("sian_10_1", "Mirror Light", "sian"),
    "10-2": ("sian_10_2", "Spots 1", "sian"),
    "10-3": ("sian_10_3", "Spots 2", "sian"),
    "10-5": ("sian_10_5", "Foot Lamp", "sian"),
    "10-6": ("sian_10_6", "Cove", "sian"),
    "11-5": ("rani_11_5", "Foot Lamp", "rani"),
    "11-6": ("rani_11_6", "Cove", "rani"),
}
COMMON_DIMMER_LIGHTS = {
    "2-7": ("hall_2_7", "Other Spots", "hall"),
    "3-0": ("hall_3_0", "Plant Spots", "hall"),
    "4-7": ("kitchen_4_7", "Spots", "kitchen"),
    "5-0": ("kitchen_5_0", "Centre Spots", "kitchen"),
    "6-7": ("dining_6_7", "Spots", "dining"),
    "7-0": ("dining_7_0", "Spots", "dining"),
    "11-7": ("rani_11_7", "Spots 1", "rani"),
    "12-0": ("rani_12_0", "Spots 2", "rani"),
}
COMMON_FANS = {
    "4-9": ("kitchen_4_9", "Fan", "kitchen"),
    "10-9": ("sian_10_9", "Fan", "sian"),
    "11-9": ("rani_11_9", "Fan", "rani"),
}
COMMON_SCREENS = {"8-0": ("dining_8_0", "Balcony", "dining")}
COMMON_ALL_SUFFIXES = (
    list(COMMON_SWITCH_LIGHTS) + list(COMMON_DIMMER_LIGHTS) + list(COMMON_FANS) + list(COMMON_SCREENS)
)

ALL_SUFFIXES = list(LIGHTS.keys()) + list(FAN.keys()) + COMMON_ALL_SUFFIXES

SPEED_TO_PRESET = {1: "low", 2: "medium", 3: "high"}
PRESET_TO_SPEED = {v: k for k, v in SPEED_TO_PRESET.items()}

_hogar: "hogar.HogarClient | None" = None
_loop: asyncio.AbstractEventLoop | None = None
_last_published: dict[str, str] = {}

mq = mqtt.Client(client_id="hogar_bridge", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)


def _light_topics(object_id: str) -> dict:
    return {
        "config": f"{DISCOVERY_PREFIX}/light/{object_id}/config",
        "state": f"{DISCOVERY_PREFIX}/light/{object_id}/state",
        "command": f"{DISCOVERY_PREFIX}/light/{object_id}/set",
    }


def _fan_topics(object_id: str) -> dict:
    return {
        "config": f"{DISCOVERY_PREFIX}/fan/{object_id}/config",
        "state": f"{DISCOVERY_PREFIX}/fan/{object_id}/state",
        "command": f"{DISCOVERY_PREFIX}/fan/{object_id}/set",
        "preset_state": f"{DISCOVERY_PREFIX}/fan/{object_id}/preset_state",
        "preset_command": f"{DISCOVERY_PREFIX}/fan/{object_id}/preset_set",
    }


def _dimmer_topics(object_id: str) -> dict:
    t = _light_topics(object_id)
    t["brightness_state"] = f"{DISCOVERY_PREFIX}/light/{object_id}/brightness/state"
    t["brightness_command"] = f"{DISCOVERY_PREFIX}/light/{object_id}/brightness/set"
    return t


def _cover_topics(object_id: str) -> dict:
    return {k: f"{DISCOVERY_PREFIX}/cover/{object_id}/{v}"
            for k, v in {"config": "config", "state": "state", "command": "set"}.items()}


def _binary_sensor_topics(object_id: str) -> dict:
    return {
        "config": f"{DISCOVERY_PREFIX}/binary_sensor/{object_id}/config",
        "state": f"{DISCOVERY_PREFIX}/binary_sensor/{object_id}/state",
    }


def publish_discovery() -> None:
    for object_id, name in LIGHTS.values():
        t = _light_topics(object_id)
        payload = {
            "name": name,
            "unique_id": f"hogar_{object_id}",
            "state_topic": t["state"],
            "command_topic": t["command"],
            "payload_on": "ON",
            "payload_off": "OFF",
            "optimistic": True,
            "device": DEVICE_INFO,
        }
        mq.publish(t["config"], json.dumps(payload), retain=True)

    for object_id, name in FAN.values():
        t = _fan_topics(object_id)
        payload = {
            "name": name,
            "unique_id": f"hogar_{object_id}",
            "state_topic": t["state"],
            "command_topic": t["command"],
            "payload_on": "ON",
            "payload_off": "OFF",
            "preset_modes": ["low", "medium", "high"],
            "preset_mode_state_topic": t["preset_state"],
            "preset_mode_command_topic": t["preset_command"],
            "optimistic": True,
            "device": DEVICE_INFO,
        }
        mq.publish(t["config"], json.dumps(payload), retain=True)

    for object_id, name, room in COMMON_SWITCH_LIGHTS.values():
        t = _light_topics(object_id)
        payload = {
            "name": name,
            "unique_id": f"hogar_{object_id}",
            "state_topic": t["state"],
            "command_topic": t["command"],
            "payload_on": "ON",
            "payload_off": "OFF",
            "optimistic": True,
            "device": ROOM_DEVICE_INFO[room],
        }
        mq.publish(t["config"], json.dumps(payload), retain=True)

    for object_id, name, room in COMMON_DIMMER_LIGHTS.values():
        t = _dimmer_topics(object_id)
        payload = {
            "name": name,
            "unique_id": f"hogar_{object_id}",
            "state_topic": t["state"],
            "command_topic": t["command"],
            "payload_on": "ON",
            "payload_off": "OFF",
            "brightness_state_topic": t["brightness_state"],
            "brightness_command_topic": t["brightness_command"],
            "brightness_scale": 100,
            "optimistic": True,
            "device": ROOM_DEVICE_INFO[room],
        }
        mq.publish(t["config"], json.dumps(payload), retain=True)

    for object_id, name, room in COMMON_FANS.values():
        t = _fan_topics(object_id)
        payload = {
            "name": name,
            "unique_id": f"hogar_{object_id}",
            "state_topic": t["state"],
            "command_topic": t["command"],
            "payload_on": "ON",
            "payload_off": "OFF",
            "preset_modes": ["low", "medium", "high"],
            "preset_mode_state_topic": t["preset_state"],
            "preset_mode_command_topic": t["preset_command"],
            "optimistic": True,
            "device": ROOM_DEVICE_INFO[room],
        }
        mq.publish(t["config"], json.dumps(payload), retain=True)

    for object_id, name, room in COMMON_SCREENS.values():
        t = _cover_topics(object_id)
        mq.publish(t["config"], json.dumps({
            "name": name, "unique_id": f"hogar_{object_id}_cover",
            "state_topic": t["state"], "command_topic": t["command"],
            "payload_open": "OPEN", "payload_close": "CLOSE",
            "state_open": "open", "state_closed": "closed",
            "device_class": "curtain", "device": ROOM_DEVICE_INFO[room],
        }), retain=True)
        t = _binary_sensor_topics(object_id)
        payload = {
            "name": name,
            "unique_id": f"hogar_{object_id}",
            "state_topic": t["state"],
            "payload_on": "ON",
            "payload_off": "OFF",
            "device_class": "opening",
            "device": ROOM_DEVICE_INFO[room],
        }
        mq.publish(t["config"], json.dumps(payload), retain=True)


def _on_connect(client, userdata, flags, reason_code, properties=None) -> None:
    print(f"[MQTT] Connected (reason_code={reason_code})")
    publish_discovery()
    for object_id, _ in LIGHTS.values():
        client.subscribe(_light_topics(object_id)["command"])
    for object_id, _ in FAN.values():
        t = _fan_topics(object_id)
        client.subscribe(t["command"])
        client.subscribe(t["preset_command"])
    for object_id, _, _ in COMMON_SWITCH_LIGHTS.values():
        client.subscribe(_light_topics(object_id)["command"])
    for object_id, _, _ in COMMON_DIMMER_LIGHTS.values():
        t = _dimmer_topics(object_id)
        client.subscribe(t["command"])
        client.subscribe(t["brightness_command"])
    for object_id, _, _ in COMMON_FANS.values():
        t = _fan_topics(object_id)
        client.subscribe(t["command"])
        client.subscribe(t["preset_command"])
    for object_id, _, _ in COMMON_SCREENS.values():
        client.subscribe(_cover_topics(object_id)["command"])


def _on_message(client, userdata, msg) -> None:
    payload = msg.payload.decode()
    for suffix, (object_id, _, _) in COMMON_SCREENS.items():
        if msg.topic == _cover_topics(object_id)["command"]:
            if payload in {"OPEN", "CLOSE", "STOP"}:
                asyncio.run_coroutine_threadsafe(_hogar.set_screen(suffix, opened=(None if payload == "STOP" else payload == "OPEN")), _loop)
            return
    for suffix, (object_id, _) in LIGHTS.items():
        if msg.topic == _light_topics(object_id)["command"]:
            asyncio.run_coroutine_threadsafe(_hogar.set_device(suffix, on=(payload == "ON")), _loop)
            return
    for suffix, (object_id, _) in FAN.items():
        t = _fan_topics(object_id)
        if msg.topic == t["command"]:
            asyncio.run_coroutine_threadsafe(_hogar.set_device(suffix, on=(payload == "ON")), _loop)
            return
        if msg.topic == t["preset_command"]:
            speed = PRESET_TO_SPEED.get(payload, 1)
            asyncio.run_coroutine_threadsafe(_hogar.set_device(suffix, on=True, speed=speed), _loop)
            return
    for suffix, (object_id, _, _) in COMMON_SWITCH_LIGHTS.items():
        if msg.topic == _light_topics(object_id)["command"]:
            asyncio.run_coroutine_threadsafe(_hogar.set_device(suffix, on=(payload == "ON")), _loop)
            return
    for suffix, (object_id, _, _) in COMMON_DIMMER_LIGHTS.items():
        t = _dimmer_topics(object_id)
        if msg.topic == t["command"]:
            asyncio.run_coroutine_threadsafe(_hogar.set_device(suffix, on=(payload == "ON")), _loop)
            return
        if msg.topic == t["brightness_command"]:
            asyncio.run_coroutine_threadsafe(_hogar.set_device(suffix, on=True, brightness=int(payload)), _loop)
            return
    for suffix, (object_id, _, _) in COMMON_FANS.items():
        t = _fan_topics(object_id)
        if msg.topic == t["command"]:
            asyncio.run_coroutine_threadsafe(_hogar.set_device(suffix, on=(payload == "ON")), _loop)
            return
        if msg.topic == t["preset_command"]:
            speed = PRESET_TO_SPEED.get(payload, 1)
            asyncio.run_coroutine_threadsafe(_hogar.set_device(suffix, on=True, speed=speed), _loop)
            return


mq.on_connect = _on_connect
mq.on_message = _on_message


def _publish_if_changed(topic: str, value: str) -> None:
    if _last_published.get(topic) != value:
        mq.publish(topic, value, retain=True)
        _last_published[topic] = value


async def _state_publish_loop() -> None:
    while True:
        states = _hogar.get_all_states()
        for suffix, (object_id, _) in LIGHTS.items():
            s = states.get(suffix, {})
            t = _light_topics(object_id)
            _publish_if_changed(t["state"], "ON" if s.get("on") else "OFF")
        for suffix, (object_id, _) in FAN.items():
            s = states.get(suffix, {})
            t = _fan_topics(object_id)
            _publish_if_changed(t["state"], "ON" if s.get("on") else "OFF")
            _publish_if_changed(t["preset_state"], SPEED_TO_PRESET.get(s.get("speed"), "low"))
        for suffix, (object_id, _, _) in COMMON_SWITCH_LIGHTS.items():
            s = states.get(suffix, {})
            t = _light_topics(object_id)
            _publish_if_changed(t["state"], "ON" if s.get("on") else "OFF")
        for suffix, (object_id, _, _) in COMMON_DIMMER_LIGHTS.items():
            s = states.get(suffix, {})
            t = _dimmer_topics(object_id)
            _publish_if_changed(t["state"], "ON" if s.get("on") else "OFF")
            if s.get("brightness") is not None:
                _publish_if_changed(t["brightness_state"], str(s.get("brightness")))
        for suffix, (object_id, _, _) in COMMON_FANS.items():
            s = states.get(suffix, {})
            t = _fan_topics(object_id)
            _publish_if_changed(t["state"], "ON" if s.get("on") else "OFF")
            _publish_if_changed(t["preset_state"], SPEED_TO_PRESET.get(s.get("speed"), "low"))
        for suffix, (object_id, _, _) in COMMON_SCREENS.items():
            s = states.get(suffix, {})
            t = _binary_sensor_topics(object_id)
            _publish_if_changed(t["state"], "ON" if s.get("open") else "OFF")
            _publish_if_changed(_cover_topics(object_id)["state"], "open" if s.get("open") else "closed")
        await asyncio.sleep(0.5)


def _read_hogar_ip() -> str:
    cfg_path = Path(__file__).parent / "config.json"
    try:
        cfg = json.loads(cfg_path.read_text())
        return cfg.get("hogar_ip") or hogar._DEFAULT_IP
    except Exception:
        return hogar._DEFAULT_IP


async def main() -> None:
    global _hogar, _loop
    _loop = asyncio.get_event_loop()

    ip = _read_hogar_ip()
    _hogar = hogar.HogarClient(ip=ip, device_map={s: s for s in ALL_SUFFIXES})
    await _hogar.start()

    mq.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    mq.loop_start()

    await asyncio.sleep(2)  # let the first Hogar poll land before the first state publish
    await _state_publish_loop()


if __name__ == "__main__":
    asyncio.run(main())
