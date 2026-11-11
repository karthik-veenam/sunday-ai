"""Position the remote trigger in the TV header without adding a row."""
import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp

def fix(config):
 cards=config['views'][0]['cards'][0]['cards']
 wrapper=next(c for c in cards if c.get('type')=='custom:mod-card' and any(x.get('entity')=='media_player.bravia_kd_65x75k' for x in c.get('card',{}).get('cards',[])))
 stack=wrapper['card']; tv,remote=stack['cards']
 assert remote['type']=='custom:mushroom-chips-card'
 chip=remote['chips'][0]
 chip.pop('content',None)
 chip['type']='action'
 chip.pop('icon_color',None)
 assert chip['tap_action']['browser_mod']['service']=='browser_mod.popup'
 # Anchor the trigger to the wrapper instead of occupying a second stack row.
 wrapper['card_mod']['style']+='\nha-card { position: relative; }'
 stack['card_mod']={'style': '''#root { position: relative; gap: 0 !important; }
 #root > * { margin: 0 !important; }
 ''' }
 remote['card_mod']={'style': ''':host {
   position: absolute !important;
   top: 12px !important;
   right: 12px !important;
   z-index: 5;
   display: block;
   width: 40px;
   height: 40px;
   margin: 0 !important;
   --chip-background: #e9f3ff;
   --chip-border-color: rgba(255,255,255,0.7);
   --chip-icon-color: #174d83;
   --primary-text-color: #174d83;
   --chip-height: 40px;
   --chip-icon-size: 24px;
   --chip-box-shadow: 0 1px 4px rgba(0,40,90,0.12);
 }
 ha-card { padding: 0 !important; background: transparent; border: none; box-shadow: none; }
 mushroom-action-chip { --ha-card-background: #e9f3ff; --card-background-color: #e9f3ff; --primary-text-color: #174d83; --chip-icon-color: #174d83; }
 '''}
 # Leave space for the remote trigger beside the media card's title.
 tv['card_mod']['style']+='\nmushroom-state-item { padding-right: 52px; }'
 return config

async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   seq=0
   async def call(payload):
    nonlocal seq
    seq+=1;await w.send_json(dict(payload,id=seq))
    while True:
     r=await w.receive_json()
     if r.get('id')==seq:
      assert r.get('success'),r.get('error')
      return r.get('result')
   paths=['dashboard-hall','dashboard-hall-test','dashboard-common-areas']
   originals={p:await call({'type':'lovelace/config','url_path':p}) for p in paths}
   backup=Path('/home/djpi/ha-backups')/('before-remote-position-fix-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
   backup.write_text(json.dumps(originals,indent=2)+'\n')
   updated=copy.deepcopy(originals)
   for p in paths[:2]:fix(updated[p])
   common=updated[paths[2]]
   idx=next(i for i,v in enumerate(common['views']) if v.get('path')=='hall')
   prior=common['views'][idx]
   view=copy.deepcopy(updated[paths[0]]['views'][0]);view.update(path=prior['path'],title=prior['title'])
   common['views'][idx]=view
   assert all(v==common['views'][i] for i,v in enumerate(originals[paths[2]]['views']) if i!=idx)
   for p,c in updated.items():
    await call({'type':'lovelace/config/save','url_path':p,'config':c})
    assert await call({'type':'lovelace/config','url_path':p})==c
    print('Saved and verified:',p)
   print('Backup:',backup)
if __name__=='__main__':asyncio.run(main())
