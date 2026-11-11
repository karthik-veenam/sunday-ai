"""Gateway tests for the body's BATTERY and VOLUME commands (loopback /battery and /volume) and the HA battery entities.
Runs against a sandboxed gateway process and a scripted fake body; touches no production service.
    ~/sunday-audio/venv/bin/python tests/test_battery_volume.py
"""
import asyncio
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "gateway"))
from aiohttp import ClientSession, WSMsgType  # noqa: E402
from fake_stackchan import mac_for  # noqa: E402
from test_gateway import Sandbox, KEY  # noqa: E402
import ha_mqtt as H  # noqa: E402

TEL = {"type": "telemetry", "expr": "Idle", "st": "idle", "ov": 0, "disp": "immersive", "pet": 1, "lux": 5.0, "rssi": -50, "up": 100,
       "live": 0, "n": 0, "ns": 1, "nb": 3, "ld": "Auto", "ln": "Off", "pe": 1, "pn": 0}


class ScriptedBody:
    """A fake Stack-chan that behaves like the new firmware: answers `cmd battery`, tracks `cmd volume` and reports telemetry."""
    def __init__(self, url, vol=255, battery=(82, 0, 4010), answers_battery=True):
        self.url, self.vol, self.battery, self.answers_battery = url, vol, battery, answers_battery
        self.cmds, self.connected, self._task, self._ws = [], asyncio.Event(), None, None

    async def _telemetry(self):
        await self._ws.send_json(dict(TEL, vol=self.vol))

    async def run(self):
        async with ClientSession() as s:
            async with s.ws_connect(self.url, heartbeat=5) as ws:
                self._ws = ws
                ch = json.loads((await ws.receive()).data)
                await ws.send_json({"type": "hello", "device": "stackchan", "fw": "scripted", "mac": "aa:bb:cc:00:00:02", "hmac": mac_for(KEY, ch["nonce"])})
                async for m in ws:
                    if m.type != WSMsgType.TEXT:
                        continue
                    j = json.loads(m.data)
                    if j["type"] == "welcome":
                        self.connected.set()
                        await self._telemetry()
                    elif j["type"] == "cmd":
                        self.cmds.append((j["name"], j["value"]))
                        if j["name"] == "volume":
                            self.vol = int(j["value"]); await self._telemetry()
                        elif j["name"] == "battery" and self.answers_battery:
                            pct, chg, mv = self.battery
                            await ws.send_json({"type": "battery", "ok": True, "pct": pct, "chg": chg, "mv": mv})

    def start(self):
        self._task = asyncio.create_task(self.run())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except BaseException:
                pass


async def get(gw, path, method="GET"):
    async with ClientSession() as s:
        async with s.request(method, "http://127.0.0.1:%d%s" % (gw.ports["health"], path)) as r:
            return r.status, await r.json()


class Battery(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_reading_with_charging_state(self):
        async with Sandbox() as gw:
            b = ScriptedBody(gw.url, battery=(64, 1, 3950)); b.start(); await b.connected.wait()
            st, j = await get(gw, "/battery")
            self.assertEqual(st, 200)
            self.assertTrue(j["ok"]); self.assertEqual((j["percent"], j["charging"], j["millivolts"]), (64, 1, 3950))
            self.assertLess(j["age_s"], 3)
            self.assertIn(("battery", "read"), b.cmds)
            await b.stop()

    async def test_body_not_answering_reports_unavailable_instead_of_hanging(self):
        async with Sandbox() as gw:
            b = ScriptedBody(gw.url, answers_battery=False); b.start(); await b.connected.wait()
            t0 = asyncio.get_running_loop().time()
            st, j = await get(gw, "/battery")
            self.assertFalse(j["ok"]); self.assertIn("not available", j["detail"])
            self.assertLess(asyncio.get_running_loop().time() - t0, 5)      # bounded wait
            await b.stop()

    async def test_stale_reading_is_returned_with_its_age_when_the_body_stops_answering(self):
        async with Sandbox() as gw:
            b = ScriptedBody(gw.url); b.start(); await b.connected.wait()
            self.assertTrue((await get(gw, "/battery"))[1]["ok"])
            b.answers_battery = False
            await asyncio.sleep(1.2)
            st, j = await get(gw, "/battery")
            self.assertTrue(j["ok"]); self.assertGreaterEqual(j["age_s"], 1.0)  # old value, honestly aged
            await b.stop()

    async def test_no_body_connected(self):
        async with Sandbox() as gw:
            st, j = await get(gw, "/battery")
            self.assertFalse(j["ok"]); self.assertIn("not connected", j["detail"])


class Volume(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sb = Sandbox(); self.gw = await self.sb.__aenter__()
        self.body = ScriptedBody(self.gw.url, vol=255); self.body.start(); await self.body.connected.wait()
        await asyncio.sleep(0.3)                                             # first telemetry arrives

    async def asyncTearDown(self):
        await self.body.stop(); await self.sb.__aexit__(None, None, None)

    async def test_read_current_percent(self):
        st, j = await get(self.gw, "/volume")
        self.assertEqual((j["ok"], j["percent"], j["raw"]), (True, 100, 255))

    async def test_relative_change_and_percent_to_raw_mapping(self):
        st, j = await get(self.gw, "/volume?delta=-15", "POST")
        self.assertEqual((j["percent"], j["previous"], j["raw"]), (85, 100, 217))
        await asyncio.sleep(0.2)
        self.assertEqual(self.body.cmds[-1], ("volume", "217")); self.assertEqual(self.body.vol, 217)

    async def test_back_to_back_changes_build_on_each_other(self):
        await get(self.gw, "/volume?delta=-15", "POST"); st, j = await get(self.gw, "/volume?delta=-15", "POST")
        self.assertEqual((j["previous"], j["percent"]), (85, 70))

    async def test_absolute_set(self):
        st, j = await get(self.gw, "/volume?set=50", "POST")
        self.assertEqual((j["percent"], j["raw"]), (50, 128))

    async def test_voice_floor_keeps_her_audible(self):
        st, j = await get(self.gw, "/volume?set=0", "POST")
        self.assertEqual((j["percent"], j["limited"]), (10, True)); self.assertEqual(j["raw"], 26)

    async def test_cap_at_100(self):
        await get(self.gw, "/volume?set=90", "POST")
        st, j = await get(self.gw, "/volume?delta=50", "POST")
        self.assertEqual((j["percent"], j["raw"]), (100, 255))

    async def test_bad_input_is_a_400(self):
        st, j = await get(self.gw, "/volume?set=loud", "POST")
        self.assertEqual(st, 400); self.assertFalse(j["ok"])


class HomeAssistant(unittest.TestCase):
    def test_battery_entities(self):
        msgs = dict((p["unique_id"], (t, p)) for t, p in H.battery_discovery_messages("fw"))
        t, p = msgs["sunday_body_battery"]
        self.assertTrue(t.endswith("/sensor/sunday_body/battery/config"))
        self.assertEqual((p["device_class"], p["unit_of_measurement"], p["state_topic"]), ("battery", "%", H.BATTERY))
        self.assertEqual(p["availability_topic"], H.AVAIL); self.assertEqual(p["device"]["identifiers"], ["sunday_stackchan"])
        t, p = msgs["sunday_body_battery_charging"]
        self.assertTrue(t.endswith("/binary_sensor/sunday_body/battery_charging/config")); self.assertEqual(p["device_class"], "battery_charging")

    def test_battery_is_published_retained_and_republished_with_discovery(self):
        b = H.HaBridge.__new__(H.HaBridge)
        b.client = mock.Mock(); b.fw = ""
        b.battery({"percent": 77, "charging": 1, "millivolts": 4000})
        topic, payload = b.client.publish.call_args.args[:2]
        self.assertEqual(topic, H.BATTERY); self.assertEqual(json.loads(payload)["percent"], 77)
        self.assertTrue(b.client.publish.call_args.kwargs["retain"])
        b.client.reset_mock()
        with mock.patch.object(H, "discovery_messages", return_value=[]):
            b._publish_discovery()
        topics = [c.args[0] for c in b.client.publish.call_args_list]
        self.assertEqual(sum(t.endswith("/config") for t in topics), 2)       # the two battery entities
        self.assertIn(H.BATTERY, topics)                                       # last reading re-announced


if __name__ == "__main__":
    unittest.main()
