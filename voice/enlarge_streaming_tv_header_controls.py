import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi')
PRE="""{% set streaming = is_state('media_player.bravia_kd_65x75k','on') and states('media_player.vasuden_tv') in ['playing','paused'] %}
"""
STYLE=PRE+""":host {
 position: absolute !important;
 top: 12px !important; right: 12px !important; z-index: 5;
 display: block; width: {{ '116px' if streaming else '88px' }}; height: {{ '52px' if streaming else '40px' }};
 margin: 0 !important;
 --mush-chip-height: {{ '52px' if streaming else '40px' }};
 --chip-height: {{ '52px' if streaming else '40px' }};
 --mush-chip-icon-size: {{ '28px' if streaming else '24px' }};
 --chip-icon-size: {{ '28px' if streaming else '24px' }};
 --mush-chip-border-radius: {{ '26px' if streaming else '20px' }};
 --chip-border-radius: {{ '26px' if streaming else '20px' }};
 --mush-chip-background: {{ '#f2f7ff' if streaming else '#e9f3ff' }};
 --chip-background: {{ '#f2f7ff' if streaming else '#e9f3ff' }};
 --mush-chip-border-color: {{ 'rgba(255,255,255,.95)' if streaming else '#d6e1ed' }};
 --chip-border-color: {{ 'rgba(255,255,255,.95)' if streaming else '#d6e1ed' }};
 --mush-chip-box-shadow: {{ '0 3px 12px rgba(0,15,40,.3)' if streaming else '0 1px 4px rgba(0,40,90,.12)' }};
 --chip-box-shadow: {{ '0 3px 12px rgba(0,15,40,.3)' if streaming else '0 1px 4px rgba(0,40,90,.12)' }};
 --primary-text-color: #174d83;
 --chip-icon-color: #174d83;
}
ha-card { padding: 0 !important; background: transparent; border: none; box-shadow: none; }
.chip-container { flex-wrap: nowrap !important; gap: {{ '12px' if streaming else '8px' }} !important; }
"""
async def main():
 token=json.load(open(ROOT/'sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35)) as s:
  async with s.ws_connect('ws://127.0.0.1:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok';i=0
   async def call(kind,**kw):
    nonlocal i
    i+=1;await w.send_json(dict(id=i,type=kind,**kw))
    while True:
     r=await w.receive_json()
     if r.get('id')==i:
      assert r.get('success'),r.get('error');return r.get('result')
   paths=['dashboard-roka','dashboard-rani','dashboard-sian','dashboard-hall','dashboard-common-areas'];old={p:await call('lovelace/config',url_path=p) for p in paths};new=copy.deepcopy(old)
   for path,config in new.items():
    found=[]
    def walk(x):
     if not isinstance(x,dict):return
     if x.get('type')=='custom:mod-card' and x.get('card',{}).get('cards',[{}])[0].get('type')=='custom:mushroom-template-card' and x['card']['cards'][0].get('entity')=='media_player.bravia_kd_65x75k':
      kids=x['card']['cards'];header=kids[0]
      header['card_mod']['style']+='\n'+PRE+"ha-card { min-height: {{ '76px' if streaming else '64px' }}; padding-right: {{ '128px' if streaming else '100px' }}; box-sizing: border-box; }\n"
      chips=next(c for c in kids if c.get('type')=='custom:mushroom-chips-card' and any(ch.get('icon')=='mdi:remote-tv' for ch in c.get('chips',[])))
      chips['card_mod']['style']=STYLE;found.append(True);return
     for child in x.get('cards',[]):walk(child)
     if isinstance(x.get('card'),dict):walk(x['card'])
    for view in config['views']:
     for card in view.get('cards',[]):walk(card)
    assert len(found)==1,(path,len(found))
   def strip(x):
    if isinstance(x,dict):return {k:strip(v) for k,v in x.items() if k!='card_mod'}
    if isinstance(x,list):return [strip(v) for v in x]
    return x
   assert strip(old)==strip(new)
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');(ROOT/f'ha-backups/before-larger-streaming-controls-{stamp}.json').write_text(json.dumps(old,indent=2))
   for path in paths:
    assert await call('lovelace/config',url_path=path)==old[path]
    await call('lovelace/config/save',url_path=path,config=new[path]);assert await call('lovelace/config',url_path=path)==new[path]
   print('Verified larger high-contrast streaming power/remote controls across five Hall copies; control actions unchanged.')
asyncio.run(main())
