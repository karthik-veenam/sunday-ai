"""Replace the pulse with a CSS door that swings out and snaps shut."""
import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
STYLE='''
:host {
 position: absolute; right: 12px; top: 14px; z-index: 3;
 width: 40px; height: 40px;
 --chip-height: 38px; --chip-background: #ffe5e5;
 --chip-border-color: #f1aaaa; --primary-text-color: #c62828;
 --chip-icon-color: transparent;
}
ha-card { padding: 0; background: transparent; border: none; box-shadow: none; }
/* Only draw the animated door while a real door is open. */
{% if is_state('binary_sensor.fridge_refrigerator_door_status','on') or is_state('binary_sensor.fridge_freezer_door_status','on') %}
ha-card::before {
 content: ""; position: absolute; pointer-events: none;
 width: 16px; height: 24px; top: 7px; right: 11px;
 box-sizing: border-box; border: 2px solid #c62828;
 border-radius: 3px; background: #f9caca;
 box-shadow: inset 0 0 0 2px #ffe5e5;
}
ha-card::after {
 content: ""; position: absolute; pointer-events: none;
 width: 14px; height: 22px; top: 8px; right: 12px;
 box-sizing: border-box; border: 2px solid #c62828;
 border-radius: 2px;
 background: radial-gradient(circle at 77% 50%, #c62828 0 1.4px, transparent 1.8px), #ffe5e5;
 transform-origin: left center;
 animation: fridge-door-snap 2.6s infinite;
}
{% endif %}
@keyframes fridge-door-snap {
 0%, 12% { transform: perspective(65px) rotateY(0deg); }
 42%, 66% { transform: perspective(65px) rotateY(-68deg); }
 76% { transform: perspective(65px) rotateY(0deg); }
 80% { transform: perspective(65px) rotateY(-12deg); }
 85%, 100% { transform: perspective(65px) rotateY(0deg); }
}
@media (prefers-reduced-motion: reduce) {
 ha-card::after { animation: none; transform: perspective(65px) rotateY(-40deg); }
}
'''
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
   paths=['dashboard-kitchen','dashboard-common-areas']
   original={p:await call({'type':'lovelace/config','url_path':p}) for p in paths}
   backup=Path('/home/djpi/ha-backups')/('before-fridge-door-snap-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json');backup.write_text(json.dumps(original,indent=2)+'\n')
   updated=copy.deepcopy(original)
   def replace(obj):
    count=0
    if isinstance(obj,dict):
     if obj.get('type')=='custom:mushroom-chips-card' and 'door-alert-pulse' in obj.get('card_mod',{}).get('style',''):
      obj['card_mod']['style']=STYLE
      # Keep the real action for opening fridge details. The visible door is CSS;
      # clearing the stock icon avoids two overlapping door drawings.
      obj['chips'][0]['chip']['icon']='mdi:blank'
      obj['chips'][0]['chip']['icon_color']='transparent'
      return 1
     for value in obj.values():count+=replace(value)
    elif isinstance(obj,list):
     for value in obj:count+=replace(value)
    return count
   for path,config in updated.items():
    view=config['views'][0] if path=='dashboard-kitchen' else next(v for v in config['views'] if v['path']=='kitchen')
    assert replace(view)==1
    await call({'type':'lovelace/config/save','url_path':path,'config':config})
    assert await call({'type':'lovelace/config','url_path':path})==config
    print('Saved and verified:',path)
   print('Backup:',backup)
if __name__=='__main__':asyncio.run(main())
