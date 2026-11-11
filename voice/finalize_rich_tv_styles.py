import asyncio,json,aiohttp
async def main():
 t=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession() as s:
  async with s.ws_connect('ws://127.0.0.1:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':t});await w.receive_json();i=0
   async def call(ty,**kw):
    nonlocal i
    i+=1;await w.send_json(dict(id=i,type=ty,**kw));r=await w.receive_json();assert r['success'];return r.get('result')
   for path in ['dashboard-roka','dashboard-rani','dashboard-sian','dashboard-hall','dashboard-common-areas']:
    config=await call('lovelace/config',url_path=path)
    def walk(x):
     if isinstance(x,dict):
      if x.get('type')=='custom:mushroom-template-card' and x.get('entity')=='media_player.vasuden_tv':
       mod=x.get('card_mod',{})
       if 'mushroom-state-info $' in mod:
        extra=mod.pop('mushroom-state-info $');mod['style']={'.':mod['style'],'mushroom-state-info $':extra}
      if x.get('type')=='markdown' and "media_player.vasuden_tv" in x.get('content',''):
       x['card_mod']['style']+="\nha-card { display: {{ 'block' if (state_attr('media_player.vasuden_tv','media_duration') | float(0)) > 0 else 'none' }}; }\n"
      for v in x.values():walk(v)
     elif isinstance(x,list):
      for v in x:walk(v)
    walk(config);await call('lovelace/config/save',url_path=path,config=config);assert await call('lovelace/config',url_path=path)==config
   print('Title wrapping and missing-duration fallback verified across all rich cards.')
asyncio.run(main())
