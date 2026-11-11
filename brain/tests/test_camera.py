import asyncio, unittest
from types import SimpleNamespace
from unittest import mock
import camera_tools, tools
from fast_mode import compare_shadow


def run(c):
    return asyncio.new_event_loop().run_until_complete(c)


class FakeHA:
    """Stands in for the Home Assistant client: tracks every light's state and records every service call."""
    def __init__(self, on=()):
        import ha
        self.state = {ha.ALIASES[name]: ('on' if name in on else 'off') for name in camera_tools.PHOTO_LIGHTS}
        self.other = {eid for eid in ha.ALIASES.values() if eid.startswith('light.') and eid not in self.state}
        self.calls = []

    async def get_state(self, eid):
        return {'entity_id': eid, 'state': self.state.get(eid, 'unavailable'), 'attributes': {}}

    async def call_service(self, domain, service, data=None):
        self.calls.append((domain, service, data['entity_id']))
        self.state[data['entity_id']] = 'on' if service == 'turn_on' else 'off'
        return 'ok'

    def lit(self):
        return sorted(e for e, v in self.state.items() if v == 'on')


def bright():   # room is bright, so the light logic stays out of the way
    return mock.patch.object(camera_tools, 'room_lux', mock.AsyncMock(return_value=500.0))


class CameraFastPath(unittest.TestCase):
    def test_commands_match(self):
        for t in ['Sunday click a picture', 'Sunday, click a picture.', 'take a photo', 'can you take a picture of me', 'snap a selfie',
                  'Click a pic', 'say cheese', 'please take a picture', 'hey sunday take a quick photo', 'could you take a picture please']:
            self.assertTrue(camera_tools.is_camera_command(t), t)

    def test_everything_else_goes_to_the_llm(self):
        for t in ["don't take a picture", 'take a picture and send it to me', "why can't you take a picture", 'take a picture of the tv in an hour',
                  'if I say so take a picture', 'what is a good photo editor', 'turn on the light', 'the picture is crooked', '', 'tell me about pictures']:
            self.assertFalse(camera_tools.is_camera_command(t), t)

    def test_fast_record_only_for_voice_and_when_not_read_only(self):
        out = SimpleNamespace(request_id='r1', read_only=False)
        with bright(), mock.patch.object(camera_tools, 'capture', mock.AsyncMock(return_value=(True, 'requested'))) as cap:
            rec = run(camera_tools.attempt({'source': 'voice', 'text': 'Sunday click a picture'}, out))
            self.assertTrue(rec['handled']); self.assertEqual(rec['reply'], 'Say cheese!'); cap.assert_awaited_once()
            for req, o in [({'source': 'telegram', 'text': 'take a picture'}, out), ({'source': 'ha', 'text': 'take a picture'}, out),
                           ({'source': 'voice', 'text': 'take a picture'}, SimpleNamespace(request_id='r2', read_only=True)),
                           ({'source': 'voice', 'text': 'turn on the fan'}, out)]:
                self.assertIsNone(run(camera_tools.attempt(req, o)))
            self.assertEqual(cap.await_count, 1)

    def test_camera_unavailable_is_reported_not_faked(self):
        out = SimpleNamespace(request_id='r3', read_only=False)
        with bright(), mock.patch.object(camera_tools, 'capture', mock.AsyncMock(return_value=(False, 'device not connected'))):
            rec = run(camera_tools.attempt({'source': 'voice', 'text': 'take a picture'}, out))
            self.assertIn("isn't available", rec['reply'])
            self.assertIn('not available', run(camera_tools.execute({})))

    def test_shadow_compare_accepts_the_record(self):
        rec = {'id': 'x', 'handled': True, 'decision': 'CAMERA', 'plan': [], 'reply': 'Say cheese!'}
        compare_shadow(rec, [])


class DarkRoom(unittest.TestCase):
    """Under 10 lux every light that is off goes on for the photo and is restored afterwards; lights already on are never touched."""
    def go(self, lux, ha, capture=(True, 'requested')):
        order = []

        async def fake_capture():
            order.append(('capture', len(ha.lit())))
            return capture

        async def scenario():
            with mock.patch.object(camera_tools, 'room_lux', mock.AsyncMock(return_value=lux)), \
                 mock.patch.object(camera_tools, '_ha_client', lambda: ha), \
                 mock.patch.object(camera_tools, 'capture', fake_capture), \
                 mock.patch.object(camera_tools, 'LIGHT_SETTLE_S', 0.01), mock.patch.object(camera_tools, 'LIGHT_HOLD_S', 0.05):
                result = await camera_tools.take_photo()
                await asyncio.sleep(0.25)                     # let the restore finish
                return result
        return order, run(scenario())

    def test_dark_room_all_off_turns_every_light_on_then_restores_them_all(self):
        ha = FakeHA()
        total = len(ha.state)
        order, (ok, _d, lit) = self.go(5.0, ha)
        self.assertTrue(ok and lit)
        self.assertEqual(order, [('capture', total)], 'the photo must be taken with all the lights already on')
        self.assertEqual(ha.lit(), [], 'every light we switched on is switched off again')
        self.assertEqual(sorted(c[1] for c in ha.calls), ['turn_off'] * total + ['turn_on'] * total)
        self.assertEqual(total, 4, 'light 1, light 2, cove and spots')
        self.assertFalse({c[2] for c in ha.calls} & ha.other, 'foot lamp, moon, top light, panels and dashboard are never touched')

    def test_lights_that_were_already_on_are_not_touched_or_switched_off(self):
        ha = FakeHA(on=('cove', 'spots'))
        order, (ok, _d, lit) = self.go(5.0, ha)
        self.assertTrue(ok and lit)
        touched = {c[2] for c in ha.calls}
        self.assertNotIn('light.roka_s_room_roka_cove', touched); self.assertNotIn('light.roka_s_room_roka_spots', touched)
        self.assertEqual(sorted(ha.lit()), ['light.roka_s_room_roka_cove', 'light.roka_s_room_roka_spots'], 'they stay on afterwards')

    def test_dark_but_everything_already_on_does_nothing(self):
        import ha as ha_mod
        ha = FakeHA(on=camera_tools.PHOTO_LIGHTS)
        _o, (ok, _d, lit) = self.go(2.0, ha)
        self.assertTrue(ok); self.assertFalse(lit); self.assertEqual(ha.calls, [])

    def test_bright_room_touches_nothing(self):
        ha = FakeHA()
        _o, (ok, _d, lit) = self.go(300.0, ha)
        self.assertTrue(ok); self.assertFalse(lit); self.assertEqual(ha.calls, [])

    def test_ten_lux_is_the_threshold(self):
        ha = FakeHA(); _o, (_k, _d, lit) = self.go(9.9, ha); self.assertTrue(lit)
        ha = FakeHA(); _o, (_k, _d, lit) = self.go(10.0, ha); self.assertFalse(lit)

    def test_unreadable_sensor_still_takes_the_photo_without_touching_lights(self):
        ha = FakeHA()
        _o, (ok, _d, lit) = self.go(None, ha)
        self.assertTrue(ok); self.assertFalse(lit); self.assertEqual(ha.calls, [])

    def test_lights_are_restored_even_if_the_photo_fails(self):
        ha = FakeHA()
        _o, (ok, _d, lit) = self.go(5.0, ha, capture=(False, 'device not connected'))
        self.assertFalse(ok); self.assertTrue(lit); self.assertEqual(ha.lit(), [])

    def test_voice_reply_mentions_the_lights(self):
        out = SimpleNamespace(request_id='r4', read_only=False)
        ha = FakeHA()

        async def scenario():
            with mock.patch.object(camera_tools, 'room_lux', mock.AsyncMock(return_value=2.0)), \
                 mock.patch.object(camera_tools, '_ha_client', lambda: ha), \
                 mock.patch.object(camera_tools, 'capture', mock.AsyncMock(return_value=(True, 'requested'))), \
                 mock.patch.object(camera_tools, 'LIGHT_SETTLE_S', 0.01), mock.patch.object(camera_tools, 'LIGHT_HOLD_S', 0.01):
                rec = await camera_tools.attempt({'source': 'voice', 'text': 'take a picture'}, out)
                await asyncio.sleep(0.2)
                return rec
        self.assertIn('switched on the lights', run(scenario())['reply'])


class CameraTool(unittest.TestCase):
    def test_tool_is_registered_and_dispatches(self):
        self.assertIn('take_picture', [d['name'] for d in tools.DEFINITIONS])
        with bright(), mock.patch.object(camera_tools, 'capture', mock.AsyncMock(return_value=(True, 'requested'))) as cap:
            self.assertIn('photo', run(tools.execute('take_picture', {})))
            cap.assert_awaited_once()
        self.assertIn('Invalid tool arguments', run(tools.execute('take_picture', {'x': 1})))

    def test_prompt_mentions_the_camera(self):
        import agent
        self.assertIn('take_picture', agent.SYSTEM_PROMPT)


if __name__ == '__main__':
    unittest.main()
