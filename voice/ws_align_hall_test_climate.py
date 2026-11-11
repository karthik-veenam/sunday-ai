"""Align Hall Test climate cards; preserve entities, actions, and other cards."""
import asyncio
import copy
import datetime
import json
from pathlib import Path
import aiohttp

URL_PATH = 'dashboard-hall-test'

def align(config):
    cards = config['views'][0]['cards'][0]['cards']
    row = next(c for c in cards if c.get('type') == 'horizontal-stack' and any(x.get('type') == 'thermostat' for x in c.get('cards', [])))
    ac, fans = row['cards']
    assert ac['type'] == 'thermostat' and fans['type'] == 'vertical-stack'
    assert len(fans['cards']) == 5
    ac['card_mod'] = {'style': '''ha-card {
      border: none;
      box-shadow: none;
      background: transparent;
    }'''}
    # Size the dial through the thermostat's available width, retaining its
    # native responsive layout and all temperature/mode interactions.
    row['cards'][0] = {
        'type': 'custom:mod-card',
        'card_mod': {'style': '''ha-card {
          height: 100%;
          min-height: 260px;
          box-sizing: border-box;
          display: flex;
          align-items: center;
          justify-content: center;
          background: var(--ha-card-background, var(--card-background-color));
          border: var(--ha-card-border-width, 1px) solid var(--ha-card-border-color, var(--divider-color));
          border-radius: var(--ha-card-border-radius, 12px);
        }
        ha-card > hui-thermostat-card {
          display: block;
          width: calc(100% - 20px);
          max-width: 260px;
        }'''},
        'card': ac,
    }
    fans['card_mod'] = {'style': ''':host {
      display: flex;
      height: 100%;
    }
    #root {
      width: 100%;
      min-height: 100%;
      justify-content: space-between;
    }'''}
    row['cards'][1] = {
        'type': 'custom:mod-card',
        'card_mod': {'style': '''ha-card {
          height: 100%;
          min-height: 260px;
          box-sizing: border-box;
          padding: 12px 8px 16px;
          display: flex;
          flex-direction: column;
          background: var(--ha-card-background, var(--card-background-color));
          border: var(--ha-card-border-width, 1px) solid var(--ha-card-border-color, var(--divider-color));
          border-radius: var(--ha-card-border-radius, 12px);
        }
        ha-card > hui-vertical-stack-card {
          display: block;
          flex: 1;
          min-height: 0;
        }'''},
        'card': fans,
    }
    return config

async def main():
    token = json.load(open('/home/djpi/sunday/config.json'))['ha_token']
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect('ws://localhost:8123/api/websocket') as ws:
            await ws.receive_json()
            await ws.send_json({'type': 'auth', 'access_token': token})
            assert (await ws.receive_json())['type'] == 'auth_ok'
            seq = 0
            async def call(payload):
                nonlocal seq
                seq += 1
                await ws.send_json(dict(payload, id=seq))
                while True:
                    resp = await ws.receive_json()
                    if resp.get('id') == seq:
                        assert resp.get('success'), resp.get('error')
                        return resp.get('result')
            original = await call({'type': 'lovelace/config', 'url_path': URL_PATH})
            stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
            backup = Path('/home/djpi/ha-backups') / f'hall-test-before-climate-alignment-{stamp}.json'
            backup.write_text(json.dumps(original, indent=2) + '\n')
            updated = align(copy.deepcopy(original))
            before = original['views'][0]['cards'][0]['cards']
            after = updated['views'][0]['cards'][0]['cards']
            assert len(before) == len(after)
            for old, new in zip(before, after):
                if old.get('type') != 'horizontal-stack':
                    assert old == new, 'Unrelated card changed'
            await call({'type': 'lovelace/config/save', 'url_path': URL_PATH, 'config': updated})
            saved = await call({'type': 'lovelace/config', 'url_path': URL_PATH})
            assert saved == updated, 'Saved configuration differs'
            print('Saved and verified:', URL_PATH)
            print('Backup:', backup)

if __name__ == '__main__':
    asyncio.run(main())
