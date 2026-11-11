"""Update only Sunday's Auto Climate controls and AC state, via HA's API."""
import asyncio
import copy
import datetime
import json
from pathlib import Path

import aiohttp

ROOT = Path('/home/djpi')


def update(config):
    result = copy.deepcopy(config)
    found = [0, 0]

    def visit(node):
        if isinstance(node, dict):
            if node.get('type') == 'thermostat' and node.get('entity') == 'climate.bedroom_auto_target':
                features = node.setdefault('features', [])
                features[:] = [f for f in features if f.get('type') != 'target-temperature']
                features.insert(0, {'type': 'target-temperature', 'style': 'buttons'})
                found[0] += 1
            if node.get('type') == 'tile' and node.get('entity') == 'climate.bedroom_ac':
                node['hide_state'] = False
                node['state_content'] = 'state'
                found[1] += 1
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    for view in result['views']:
        if view.get('path') == 'sunday':
            visit(view)
    if found != [1, 1]:
        raise ValueError(f'Expected one Auto Climate thermostat and one AC tile; found {found}')
    return result


async def main():
    token = json.loads((ROOT / 'sunday/config.json').read_text())['ha_token']
    timeout = aiohttp.ClientTimeout(total=20, sock_connect=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.ws_connect('ws://127.0.0.1:8123/api/websocket') as ws:
            await ws.receive_json()
            await ws.send_json({'type': 'auth', 'access_token': token})
            if (await ws.receive_json()).get('type') != 'auth_ok':
                raise RuntimeError('HA authentication failed')
            request_id = 0

            async def call(kind, **kwargs):
                nonlocal request_id
                request_id += 1
                await ws.send_json({'id': request_id, 'type': kind, **kwargs})
                while True:
                    response = await asyncio.wait_for(ws.receive_json(), 15)
                    if response.get('id') == request_id:
                        if not response.get('success'):
                            raise RuntimeError(response.get('error'))
                        return response.get('result')

            original = await call('lovelace/config', url_path='dashboard-roka')
            changed = update(original)
            stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
            (ROOT / f'ha-backups/roka-sunday-before-climate-controls-{stamp}.json').write_text(json.dumps(original, indent=2))
            # Refuse to overwrite concurrent dashboard edits.
            if await call('lovelace/config', url_path='dashboard-roka') != original:
                raise RuntimeError('Dashboard changed during update; rerun to use the latest version')
            await call('lovelace/config/save', url_path='dashboard-roka', config=changed)
            if await call('lovelace/config', url_path='dashboard-roka') != changed:
                raise RuntimeError('Dashboard readback did not match')
            print('Saved and verified: temperature +/- controls and visible AC state.')


if __name__ == '__main__':
    asyncio.run(main())
