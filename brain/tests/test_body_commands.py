import asyncio, unittest
from types import SimpleNamespace
from unittest import mock
import body_commands as B


def run(c):
    return asyncio.new_event_loop().run_until_complete(c)


class Parse(unittest.TestCase):
    def test_volume_up(self):
        for t in ['Sunday, speak louder', 'speak louder please', 'can you talk a bit louder', 'be louder', 'speak up', 'increase your volume',
                  'turn up your volume a little', 'raise body volume', 'your volume up', 'Sunday increase your speaker volume please',
                  "boost the body's volume"]:
            self.assertEqual(B.parse(t), ('volume_up', 15), t)

    def test_volume_down(self):
        for t in ['speak quieter', 'talk softer', 'could you be quieter please', 'lower your volume', 'turn down your volume',
                  'decrease body volume', 'your volume down', 'reduce your volume a bit']:
            self.assertEqual(B.parse(t), ('volume_down', 15), t)

    def test_volume_set(self):
        cases = {'set your volume to 60': 60, 'set your volume to 60 percent': 60, 'set your volume to 45%': 45, 'your volume to seventy percent': 70,
                 'set your volume to max': 100, 'set your volume to maximum': 100, 'set body volume to full': 100, 'set your volume to half': 50,
                 'change your volume to fifty': 50, 'put your volume at 80': 80, 'set your volume to 0': 0}
        for t, v in cases.items():
            self.assertEqual(B.parse(t), ('volume_set', v), t)

    def test_volume_question(self):
        for t in ["what's your volume", 'what is your volume', 'tell me your volume', 'how loud are you', "what's your volume now"]:
            self.assertEqual(B.parse(t), ('volume_get', None), t)

    def test_battery(self):
        for t in ["what's your battery", 'what is your battery level', "how's your battery", 'your battery', 'tell me your battery percentage',
                  'check your battery', 'how much battery do you have', 'how much battery is left', 'are you charging', 'are you plugged in',
                  'battery level of your body', "what's your body battery", 'Sunday, how is your battery?']:
            self.assertEqual(B.parse(t), ('battery', None), t)

    def test_things_that_could_mean_something_else_are_left_to_the_brain(self):
        for t in ['turn up the volume', 'volume up', 'louder', 'quieter', 'turn down the volume', 'increase the volume', 'set the volume to 60',
                  'set volume to 50 percent', 'increase the tv volume', 'turn up the soundbar', "what's the battery", 'battery', 'battery level',
                  'is the house battery charging', 'how much battery does the phone have', "what's the battery level of the geyser",
                  'are the lights on', 'is the fan charging']:
            self.assertIsNone(B.parse(t), t)

    def test_negation_conditions_and_multiple_requests_go_to_the_brain(self):
        for t in ["don't speak louder", 'never increase your volume', 'speak louder and take a picture', 'increase your volume if it is quiet',
                  'lower your volume when I say so', 'speak quieter or turn off the light', 'what is your battery and the weather',
                  'set your volume to 50 and then to 70']:
            self.assertIsNone(B.parse(t), t)

    def test_out_of_range_and_nonsense(self):
        for t in ['set your volume to 150', 'set your volume to loud', 'set your volume to', 'what is the weather', 'turn on the fan', '', None]:
            self.assertIsNone(B.parse(t), t)


class Replies(unittest.TestCase):
    def r(self, kind, result, value=None):
        return B.reply_for(kind, value, result)

    def test_volume(self):
        self.assertEqual(self.r('volume_up', {'ok': True, 'percent': 100, 'previous': 85}), 'Volume is now 100 percent.')
        self.assertEqual(self.r('volume_down', {'ok': True, 'percent': 70, 'previous': 85}), 'Volume is now 70 percent.')
        self.assertEqual(self.r('volume_up', {'ok': True, 'percent': 100, 'previous': 100}), "I'm already at full volume.")
        self.assertEqual(self.r('volume_down', {'ok': True, 'percent': 10, 'previous': 25, 'limited': True}), "That's the lowest I'll go by voice, 10 percent.")
        self.assertEqual(self.r('volume_set', {'ok': True, 'percent': 10, 'previous': 50, 'limited': True}), "That's the lowest I'll go by voice, 10 percent.")
        self.assertEqual(self.r('volume_set', {'ok': True, 'percent': 60, 'previous': 60}), 'My volume is already 60 percent.')
        self.assertEqual(self.r('volume_get', {'ok': True, 'percent': 85}), 'My volume is at 85 percent.')

    def test_battery(self):
        self.assertEqual(self.r('battery', {'ok': True, 'percent': 82, 'charging': 0, 'age_s': 0.4}), 'My battery is at 82 percent.')
        self.assertEqual(self.r('battery', {'ok': True, 'percent': 64, 'charging': 1, 'age_s': 1}), "I'm at 64 percent and charging.")
        self.assertEqual(self.r('battery', {'ok': True, 'percent': 12, 'charging': 0, 'age_s': 1}), 'My battery is low, 12 percent. Please plug me in.')
        self.assertEqual(self.r('battery', {'ok': True, 'percent': 12, 'charging': 1, 'age_s': 1}), "I'm at 12 percent and charging.")
        self.assertEqual(self.r('battery', {'ok': True, 'percent': 40, 'charging': 2, 'age_s': 1}), 'My battery is at 40 percent.')
        self.assertIn('about 3 minutes ago', self.r('battery', {'ok': True, 'percent': 50, 'charging': 0, 'age_s': 180}))

    def test_failures_are_reported_not_faked(self):
        self.assertEqual(self.r('battery', {'ok': False}), "I can't read my battery right now.")
        self.assertEqual(self.r('volume_up', {'ok': False}), "I can't reach my body right now.")


class Attempt(unittest.TestCase):
    def go(self, text, source='voice', read_only=False, result=None):
        out = SimpleNamespace(request_id='r1', read_only=read_only)
        calls = []

        async def fake_call(method, path):
            calls.append((method, path))
            return result or {'ok': True, 'percent': 70, 'previous': 85, 'charging': 0}
        with mock.patch.object(B, '_call', fake_call):
            rec = run(B.attempt({'source': source, 'text': text}, out))
        return rec, calls

    def test_routes_to_the_right_gateway_endpoint(self):
        self.assertEqual(self.go('speak louder')[1], [('POST', '/volume?delta=15')])
        self.assertEqual(self.go('speak quieter')[1], [('POST', '/volume?delta=-15')])
        self.assertEqual(self.go('set your volume to 60 percent')[1], [('POST', '/volume?set=60')])
        self.assertEqual(self.go("what's your volume")[1], [('GET', '/volume')])
        self.assertEqual(self.go("what's your battery", result={'ok': True, 'percent': 82, 'charging': 0, 'age_s': 0.2})[1], [('GET', '/battery')])

    def test_record_shape_matches_the_other_fast_paths(self):
        rec, _ = self.go('speak quieter', result={'ok': True, 'percent': 70, 'previous': 85})
        self.assertTrue(rec['handled']); self.assertEqual(rec['decision'], 'BODY_COMMAND'); self.assertEqual(rec['reply'], 'Volume is now 70 percent.')
        self.assertEqual((rec['id'], rec['request_id'], rec['source']), ('r1', 'r1', 'voice'))
        from fast_mode import compare_shadow
        compare_shadow(rec, [])                      # the runtime does this with every fast record

    def test_only_local_voice_and_never_in_a_read_only_turn(self):
        self.assertIsNone(self.go('speak louder', source='telegram')[0]); self.assertIsNone(self.go('speak louder', source='ha')[0])
        rec, calls = self.go('speak louder', read_only=True); self.assertIsNone(rec); self.assertEqual(calls, [])

    def test_non_matching_text_does_nothing(self):
        rec, calls = self.go('turn on the fan'); self.assertIsNone(rec); self.assertEqual(calls, [])

    def test_gateway_down_is_spoken_honestly(self):
        rec, _ = self.go('speak louder', result={'ok': False, 'detail': 'gateway unreachable (ClientConnectorError)'})
        self.assertEqual(rec['reply'], "I can't reach my body right now.")


if __name__ == '__main__':
    unittest.main()
