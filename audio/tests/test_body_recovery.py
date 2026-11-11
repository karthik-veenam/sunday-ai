import unittest
from unittest.mock import patch

from body_recovery import decision, main


class RecoveryTests(unittest.TestCase):
    def test_connected_clears_outage_not_reset_budget(self):
        state, action = decision({'offline_since': 10, 'resets': [900]}, {'device_connected': True}, 1000, True)
        self.assertEqual(action, 'connected')
        self.assertNotIn('offline_since', state)
        self.assertEqual(state['resets'], [900])

    def test_short_disconnect_never_resets(self):
        state, _ = decision({}, {'device_connected': False}, 1000, True)
        _, action = decision(state, {'device_connected': False}, 1179, True)
        self.assertEqual(action, 'waiting_body')

    def test_long_disconnect_resets_only_known_usb(self):
        self.assertEqual(decision({'offline_since': 1000}, {}, 1300, True)[1], 'waiting_gateway')
        self.assertEqual(decision({'offline_since': 1000}, {'device_connected': False}, 1300, False)[1], 'usb_missing')
        state, action = decision({'offline_since': 1000}, {'device_connected': False}, 1300, True)
        self.assertEqual(action, 'reset_body')
        self.assertEqual(state['resets'], [1300])

    def test_conversation_and_playback_prevent_reset(self):
        for extra in ({'playing': True}, {'face_state': 'thinking'}, {'face_state': 'speaking'}, {'face_state': 'listening'}):
            self.assertEqual(decision({'offline_since': 1000}, {'device_connected': False, **extra}, 1300, True)[1], 'waiting_idle')

    def test_gateway_failure_requires_three_checks_and_cooldown(self):
        s, action = decision({}, None, 1000, True)
        self.assertEqual(action, 'waiting_gateway')
        s, _ = decision(s, None, 1030, True)
        s, action = decision(s, None, 1060, True)
        self.assertEqual(action, 'restart_gateway')
        s['bad_health'] = 3
        self.assertEqual(decision(s, None, 1090, True)[1], 'waiting_gateway')

    def test_reset_cooldown_and_hourly_budget(self):
        h = {'device_connected': False}
        self.assertEqual(decision({'offline_since': 1000, 'resets': [1200]}, h, 1500, True)[1], 'reset_cooldown')
        self.assertEqual(decision({'offline_since': 1000, 'resets': [1200, 1800, 2400]}, h, 3100, True)[1], 'reset_cooldown')
        self.assertEqual(decision({'offline_since': 1000, 'resets': [1200, 1800, 2400]}, h, 5000, True)[1], 'reset_body')

    def test_backwards_clock_restarts_grace(self):
        state, action = decision({'offline_since': 1500}, {'device_connected': False}, 1000, True)
        self.assertEqual((state['offline_since'], action), (1000, 'waiting_body'))

    def test_explicit_airhug_selection_never_starts_gateway(self):
        with patch('body_recovery.ROOT') as root, patch('body_recovery.open', create=True) as op, patch('body_recovery.fcntl.flock'), patch('body_recovery.audio.read_backend', return_value='airhug'), patch('body_recovery.STATE') as state, patch('body_recovery.audio.RealSys') as system:
            state.exists.return_value = False
            main()
            system.assert_not_called()


if __name__ == '__main__':
    unittest.main()
