import asyncio,copy,datetime,json,re
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi');ICONS={'mdi:chevron-up','mdi:chevron-down','mdi:chevron-left','mdi:chevron-right','mdi:check'}
BUTTON_CSS='''
/* Slightly larger navigation target and glyph. */
ha-card { height: 64px !important; min-height: 64px; box-sizing: border-box; }
ha-state-icon, ha-icon, #icon { --mdc-icon-size: 28px !important; --ha-icon-size: 28px !important; width: 28px !important; height: 28px !important; }
'''
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
   paths=['dashboard-roka','dashboard-rani','dashboard-sian'];original={p:await call('lovelace/config',url_path=p) for p in paths};changed=copy.deepcopy(original);report={}
   for path,config in changed.items():
    buttons=[];pads=[]
    def walk(x):
     if isinstance(x,dict):
      if x.get('type')=='custom:mod-card' and x.get('card',{}).get('type')=='grid' and any(c.get('icon')=='mdi:chevron-up' for c in x['card'].get('cards',[])):
       kids=x['card']['cards'];navigation=[c for c in kids if c.get('type')=='button' and c.get('icon') in ICONS]
       assert len(navigation)==5,len(navigation)
       style=x['card_mod']['style'];assert '224px' in style
       x['card_mod']['style']=style.replace('224px','248px')
       for c in kids:
        if c.get('type')!='button':continue
        if c in navigation:
         c.setdefault('card_mod',{}).setdefault('style','');c['card_mod']['style']+=BUTTON_CSS;buttons.append(c['icon'])
        elif c.get('show_icon') is False:
         c.setdefault('card_mod',{}).setdefault('style','');c['card_mod']['style']+='\nha-card { height: 64px !important; }\n'
       pads.append(True)
       return
      for value in x.values():walk(value)
     elif isinstance(x,list):
      for value in x:walk(value)
    walk(config)
    assert len(pads)==(2 if path in ['dashboard-roka','dashboard-rani'] else 1),(path,len(pads))
    # Changes are strictly presentational; all commands, entities and layouts remain identical.
    def without_styles(x):
     if isinstance(x,dict):return {k:without_styles(v) for k,v in x.items() if k!='card_mod'}
     if isinstance(x,list):return [without_styles(v) for v in x]
     return x
    assert without_styles(config)==without_styles(original[path])
    report[path]={'remotes':len(pads),'navigation_buttons':len(buttons)}
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');(ROOT/f'ha-backups/before-larger-remote-dpads-{stamp}.json').write_text(json.dumps(original,indent=2))
   for path in paths:
    assert await call('lovelace/config',url_path=path)==original[path]
    await call('lovelace/config/save',url_path=path,config=changed[path]);assert await call('lovelace/config',url_path=path)==changed[path]
   print('Saved and verified:',report)
asyncio.run(main())
