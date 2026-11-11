import copy
import unittest

import automation_tools as t

PLAN = {'name': 'Geyser every morning', 'triggers': [{'trigger': 'time', 'at': '07:30:00'}],
        'actions': [{'action': 'switch.turn_on', 'target': {'entity_id': 'switch.geyser_socket_1'}}]}


class FakeHA:
    actions_enabled = True
    def __init__(self):
        self.saved = None; self.posts = 0; self.enabled = True; self.timeout = False
    async def get_states(self):
        rows = [{'entity_id': 'switch.geyser_socket_1'}]
        if self.saved:
            rows.append({'entity_id': 'automation.test', 'state': 'on' if self.enabled else 'off', 'attributes': {'id': self.aid}})
        return rows
    async def request(self, method, path, data=None):
        if path == '/api/services': return [{'domain': 'switch', 'services': {'turn_on': {}}}]
        if path == '/api/config': return {'time_zone': 'Asia/Calcutta'}
        if method == 'POST':
            self.posts += 1; self.saved = copy.deepcopy(data); self.aid = path.rsplit('/', 1)[-1]
            if self.timeout: raise TimeoutError()
            return {'result': 'ok'}
        if not self.saved: raise RuntimeError('HA GET: HTTP 404')
        return self.saved


class AutomationTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_readback_no_device_execution_and_deduplicate(self):
        ha = FakeHA(); result = await t.create(ha, PLAN)
        self.assertTrue(result['active']); self.assertFalse(result['actions_executed_now'])
        again = await t.create(ha, {**PLAN, 'name': 'Renamed same request'})
        self.assertTrue(again['already_exists']); self.assertEqual(ha.posts, 1)
        self.assertEqual(ha.saved['conditions'], [])

    async def test_timeout_reconciled_without_second_write(self):
        ha = FakeHA(); ha.timeout = True
        self.assertTrue((await t.create(ha, PLAN))['saved']); self.assertEqual(ha.posts, 1)

    async def test_edited_existing_config_not_overwritten(self):
        ha = FakeHA(); await t.create(ha, PLAN); ha.saved['mode'] = 'restart'
        result = await t.create(ha, PLAN)
        self.assertFalse(result['ok']); self.assertEqual(ha.posts, 1)

    async def test_disabled_duplicate_not_implicitly_enabled(self):
        ha = FakeHA(); await t.create(ha, PLAN); ha.enabled = False
        result = await t.create(ha, PLAN)
        self.assertTrue(result['saved']); self.assertFalse(result['active']); self.assertEqual(ha.posts, 1)

    async def test_actions_disabled_blocks_write(self):
        ha = FakeHA(); ha.actions_enabled = False
        self.assertFalse((await t.create(ha, PLAN))['ok']); self.assertEqual(ha.posts, 0)

    async def test_bad_entities_services_templates_and_unbounded_repeat_rejected(self):
        bad = [
            {'actions': [{'action': 'switch.turn_on', 'target': {'entity_id': 'switch.missing'}}]},
            {'actions': [{'action': 'shell_command.run', 'target': {'entity_id': 'switch.geyser_socket_1'}}]},
            {'actions': [{'action': 'switch.turn_on', 'target': {'area_id': 'bedroom'}}]},
            {'actions': [{'repeat': {'while': [], 'sequence': PLAN['actions']}}]},
            {'triggers': [{'trigger': 'template', 'value_template': '{{ true }}'}]},
        ]
        for change in bad:
            ha = FakeHA()
            result = await t.create(ha, {**PLAN, **change})
            self.assertFalse(result['ok']); self.assertEqual(ha.posts, 0)


if __name__ == '__main__': unittest.main()
