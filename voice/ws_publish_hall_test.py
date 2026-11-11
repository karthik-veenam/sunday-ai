"""Promote the reviewed Hall Test dashboard to the main Hall dashboard."""
import asyncio
import datetime
import json
from pathlib import Path
import aiohttp

async def main():
    token = json.load(open('/home/djpi/sunday/config.json'))['ha_token']
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        async with session.ws_connect('ws://localhost:8123/api/websocket') as ws:
            await ws.receive_json()
            await ws.send_json({'type':'auth','access_token':token})
            assert (await ws.receive_json())['type'] == 'auth_ok'
            seq = 0
            async def call(payload):
                nonlocal seq
                seq += 1
                await ws.send_json(dict(payload, id=seq))
                while True:
                    result = await ws.receive_json()
                    if result.get('id') == seq:
                        assert result.get('success'), result.get('error')
                        return result.get('result')
            source = await call({'type':'lovelace/config','url_path':'dashboard-hall-test'})
            current = await call({'type':'lovelace/config','url_path':'dashboard-hall'})
            assert source.get('views'), 'Source dashboard has no views'
            stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
            backup = Path('/home/djpi/ha-backups') / f'hall-production-before-promotion-{stamp}.json'
            backup.write_text(json.dumps(current, indent=2) + '\n')
            await call({'type':'lovelace/config/save','url_path':'dashboard-hall','config':source})
            saved = await call({'type':'lovelace/config','url_path':'dashboard-hall'})
            assert saved == source, 'Production differs from the reviewed test dashboard'
            assert await call({'type':'lovelace/config','url_path':'dashboard-hall-test'}) == source
            print('Published and verified: dashboard-hall matches dashboard-hall-test')
            print('Production backup:', backup)

if __name__ == '__main__':
    asyncio.run(main())
