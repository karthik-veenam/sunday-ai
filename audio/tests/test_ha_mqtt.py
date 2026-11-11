"""Tests for the Home Assistant (MQTT) bridge's translation logic. No broker or hardware needed."""
import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gateway"))
import ha_mqtt as H


class Discovery(unittest.TestCase):
    def setUp(self):
        self.msgs = H.discovery_messages("sunday-face-2.0-ha")

    def test_one_device_many_entities_with_unique_ids(self):
        ids = [p["unique_id"] for _t, p in self.msgs]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 17)
        self.assertTrue(all(i.startswith("sunday_body_") for i in ids))
        devs = {tuple(p["device"]["identifiers"]) for _t, p in self.msgs}
        self.assertEqual(devs, {("sunday_stackchan",)})
        self.assertTrue(all(p["device"]["name"] == "Sunday Body" and p["device"]["sw_version"] == "sunday-face-2.0-ha" for _t, p in self.msgs))

    def test_every_entity_goes_unavailable_when_the_body_does(self):
        self.assertTrue(all(p["availability_topic"] == H.AVAIL for _t, p in self.msgs))

    def test_topics_follow_homeassistant_discovery_layout(self):
        for t, p in self.msgs:
            parts = t.split("/")
            self.assertEqual(parts[0], "homeassistant"); self.assertEqual(parts[-1], "config"); self.assertEqual(parts[2], "sunday_body")
            self.assertEqual(parts[3], p["unique_id"][len("sunday_body_"):])

    def test_commands_live_under_the_cmd_topic_and_each_one_is_understood(self):
        for _t, p in self.msgs:
            if "command_topic" in p:
                name = p["command_topic"][len(H.CMD):]
                self.assertTrue(p["command_topic"].startswith(H.CMD))
                sample = {"expression": "Auto", "state": "idle", "display": "classic", "head_motion": "ON", "volume": "100",
                          "take_picture": "PRESS", "camera_preview": "OFF", "night_mode": "ON", "night_screen": "Sleep",
                          "night_brightness": "3", "led_day": "Auto", "led_night": "Off", "pet_mode": "ON", "pet_night": "OFF"}[name]
                self.assertIsNotNone(H.map_command(name, sample), name)

    def test_expression_options_match_the_face(self):
        sel = next(p for t, p in self.msgs if p["unique_id"] == "sunday_body_expression")
        self.assertEqual(sel["options"][0], "Auto"); self.assertEqual(len(sel["options"]), 13)
        for o in sel["options"][1:]:
            self.assertIsNotNone(H.map_command("expression", o))


class NightMode(unittest.TestCase):
    def test_night_entities_exist_with_the_right_choices(self):
        by = {p["unique_id"]: p for _t, p in H.discovery_messages("x")}
        self.assertEqual(by["sunday_body_night_screen"]["options"], ["Sleep", "Dim face"])
        self.assertEqual(by["sunday_body_led_day"]["options"][:2], ["Auto", "Off"])
        self.assertNotIn("Auto", by["sunday_body_led_night"]["options"], "at night 'Auto' makes no sense")
        for needed in ("Red", "Amber", "Green", "Cyan", "Blue", "Purple", "Pink", "White", "Warm white"):
            self.assertIn(needed, by["sunday_body_led_night"]["options"])
        self.assertEqual((by["sunday_body_night_brightness"]["min"], by["sunday_body_night_brightness"]["max"]), (1, 30))

    def test_night_commands(self):
        m = H.map_command
        self.assertEqual(m("night_mode", "ON"), ("night", "1")); self.assertEqual(m("night_mode", "OFF"), ("night", "0"))
        self.assertEqual(m("night_screen", "Sleep"), ("night_screen", "sleep")); self.assertEqual(m("night_screen", "Dim face"), ("night_screen", "dim"))
        self.assertEqual(m("night_brightness", "0"), ("night_brightness", "1")); self.assertEqual(m("night_brightness", "99"), ("night_brightness", "30"))
        self.assertEqual(m("led_day", "auto"), ("led_day", "Auto")); self.assertEqual(m("led_night", "warm WHITE"), ("led_night", "Warm white"))

    def test_night_commands_reject_nonsense(self):
        m = H.map_command
        self.assertIsNone(m("led_night", "Auto")); self.assertIsNone(m("led_day", "Rainbow")); self.assertIsNone(m("night_screen", "Off"))
        self.assertIsNone(m("night_mode", "dim")); self.assertIsNone(m("night_brightness", "bright"))

    def test_night_status_is_mapped(self):
        s = H.status_from_telemetry(dict(Status.TEL, n=1, ns=0, nb=7, ld="Blue", ln="Amber"))
        self.assertEqual((s["night_mode"], s["night_screen"], s["night_brightness"], s["led_day"], s["led_night"]), (True, "Sleep", 7, "Blue", "Amber"))
        self.assertEqual(H.status_from_telemetry(dict(Status.TEL, ns=1))["night_screen"], "Dim face")
        self.assertIs(H.status_from_telemetry(dict(Status.TEL, n=0))["night_mode"], False)


class PetSettings(unittest.TestCase):
    def test_commands(self):
        m = H.map_command
        self.assertEqual(m("pet_mode", "ON"), ("pet_enable", "1")); self.assertEqual(m("pet_mode", "OFF"), ("pet_enable", "0"))
        self.assertEqual(m("pet_night", "ON"), ("pet_night", "1")); self.assertEqual(m("pet_night", "OFF"), ("pet_night", "0"))
        self.assertIsNone(m("pet_mode", "maybe"))

    def test_status_defaults_for_older_firmware(self):
        tel = {"expr": "Idle", "st": "idle"}                      # firmware that does not report pet settings yet
        s = H.status_from_telemetry(tel)
        self.assertIs(s["pet_mode"], True); self.assertIs(s["pet_night"], False)
        s = H.status_from_telemetry(dict(tel, pe=0, pn=1))
        self.assertIs(s["pet_mode"], False); self.assertIs(s["pet_night"], True)


class Status(unittest.TestCase):
    TEL = {"type": "telemetry", "expr": "Happy", "st": "idle", "ov": 0, "disp": "immersive", "pet": 1, "vol": 255, "lux": 2.8, "rssi": -61, "up": 1234, "live": 0, "n": 1, "ns": 1, "nb": 3, "ld": "Auto", "ln": "Off"}

    def test_automatic_face_reads_auto_and_override_reads_its_name(self):
        self.assertEqual(H.status_from_telemetry(self.TEL)["expression"], "Auto")
        self.assertEqual(H.status_from_telemetry(dict(self.TEL, ov=1))["expression"], "Happy")

    def test_fields_are_mapped(self):
        s = H.status_from_telemetry(self.TEL)
        self.assertEqual((s["state"], s["display"], s["volume"], s["rssi"], s["uptime"], s["lux"]), ("idle", "immersive", 255, -61, 1234, 2.8))
        self.assertIs(s["head_motion"], True); self.assertIs(s["camera_preview"], False)

    def test_unreadable_light_sensor_becomes_none_not_minus_one(self):
        self.assertIsNone(H.status_from_telemetry(dict(self.TEL, lux=-1))["lux"])
        self.assertIsNone(H.status_from_telemetry({k: v for k, v in self.TEL.items() if k != "lux"})["lux"])


class Commands(unittest.TestCase):
    def test_valid_commands(self):
        m = H.map_command
        self.assertEqual(m("expression", "Auto"), ("expression", "auto")); self.assertEqual(m("expression", "Happy"), ("expression", "Happy"))
        self.assertEqual(m("state", "Thinking"), ("state", "thinking")); self.assertEqual(m("display", "Immersive"), ("display", "immersive"))
        self.assertEqual(m("head_motion", "ON"), ("pet_motion", "1")); self.assertEqual(m("head_motion", "OFF"), ("pet_motion", "0"))
        self.assertEqual(m("camera_preview", "ON"), ("live", "1")); self.assertEqual(m("camera_preview", "OFF"), ("live", "0"))
        self.assertEqual(m("take_picture", "PRESS"), ("snap", "1"))

    def test_volume_is_clamped_and_must_be_a_number(self):
        m = H.map_command
        self.assertEqual(m("volume", "120.0"), ("volume", "120")); self.assertEqual(m("volume", "999"), ("volume", "255")); self.assertEqual(m("volume", "-5"), ("volume", "0"))
        self.assertIsNone(m("volume", "loud"))

    def test_garbage_is_rejected(self):
        m = H.map_command
        for name, payload in [("expression", "Furious"), ("state", "dancing"), ("display", "3d"), ("head_motion", "maybe"), ("camera_preview", ""), ("reboot", "1"), ("wifi", "x")]:
            self.assertIsNone(m(name, payload), (name, payload))


class Routing(unittest.TestCase):
    def bridge(self):
        b = H.HaBridge.__new__(H.HaBridge)
        b.log = lambda *a: None; b.loop = object(); b.hub = mock.Mock(); b.online = True; b.fw = ""; b.last_status = None
        b.client = mock.Mock()
        return b

    def msg(self, topic, payload):
        return mock.Mock(topic=topic, payload=payload.encode())

    def test_a_valid_ha_command_reaches_the_hub(self):
        b = self.bridge()
        with mock.patch.object(H.asyncio, "run_coroutine_threadsafe") as rcts:
            b._on_message(b.client, None, self.msg(H.CMD + "expression", "Happy"))
        rcts.assert_called_once(); b.hub.body_cmd.assert_called_once_with("expression", "Happy")

    def test_an_invalid_command_is_ignored(self):
        b = self.bridge()
        with mock.patch.object(H.asyncio, "run_coroutine_threadsafe") as rcts:
            b._on_message(b.client, None, self.msg(H.CMD + "expression", "Furious"))
        rcts.assert_not_called(); b.hub.body_cmd.assert_not_called()

    def test_ha_restart_republishes_everything(self):
        b = self.bridge(); b.last_status = {"state": "idle"}
        b._on_message(b.client, None, self.msg("homeassistant/status", "online"))
        topics = [c.args[0] for c in b.client.publish.call_args_list]
        self.assertEqual(sum(t.endswith("/config") for t in topics), 17)
        self.assertIn(H.AVAIL, topics); self.assertIn(H.STATUS, topics)

    def test_online_offline_and_telemetry_publish_retained(self):
        b = self.bridge()
        b.device_online({"fw": "x"}); b.device_offline(); b.telemetry(Status.TEL)
        pubs = {c.args[0]: c for c in b.client.publish.call_args_list if not c.args[0].endswith("/config")}
        self.assertEqual(pubs[H.AVAIL].args[1], "offline"); self.assertTrue(pubs[H.AVAIL].kwargs["retain"]); self.assertTrue(pubs[H.STATUS].kwargs["retain"])


if __name__ == "__main__":
    unittest.main()
