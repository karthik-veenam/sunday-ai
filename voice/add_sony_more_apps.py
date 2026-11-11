import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi');ENTITY='media_player.bravia_kd_65x75k'
STYLE='''ha-card {
 width: 100%; aspect-ratio: 3.5 / 1; box-sizing: border-box;
 display: flex; align-items: center; justify-content: center;
 background: #fff; border: 1px solid #d6e1ed;
 border-radius: 8px !important; overflow: hidden;
 box-shadow: 0 2px 5px rgba(43,65,91,.06);
}
img, #image { display: block !important; width: 100% !important; height: auto !important; object-fit: contain !important; margin: 0 !important; border-radius: 0 !important; }
'''
def launch(title):return {'action':'perform-action','perform_action':'media_player.play_media','target':{'entity_id':ENTITY},'data':{'media_content_type':'app','media_content_id':title}}
async def main():
 apps=json.load(open(ROOT/'sunday/sony-app-buttons.json'))
 popup={'type':'custom:mod-card','card_mod':{'style':'ha-card { background: #f5f8fc; border-radius: 16px; padding: 12px; border: none; box-shadow: none; }'},'card':{'type':'grid','columns':2,'square':False,'cards':[{'type':'picture','image':app['image'],'alt_text':app['title'],'tap_action':launch(app['title']),'hold_action':{'action':'none'},'card_mod':{'style':STYLE}} for app in apps]}}
 more={'type':'button','name':'More apps','icon':'mdi:dots-horizontal','show_name':False,'show_state':False,'tap_action':{'action':'fire-dom-event','browser_mod':{'service':'browser_mod.popup','data':{'title':'TV Apps','content':popup}}},'hold_action':{'action':'none'},'card_mod':{'style':'''ha-card { height: 40px !important; box-sizing: border-box; border-radius: 8px !important; background: #e7eef6; border: 1px solid #d6e1ed; box-shadow: 0 2px 5px rgba(43,65,91,.06); --primary-text-color: #294765; --state-icon-color: #294765; }
ha-state-icon { --mdc-icon-size: 25px; width: 25px; height: 25px; }
'''}}
 token=json.load(open(ROOT/'sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as s:
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
     if isinstance(x,dict):
      if x.get('type')=='grid' and len(x.get('cards',[]))==2 and {z.get('tap_action',{}).get('data',{}).get('media_content_id') for z in x['cards']}=={'YouTube','JioHotstar'} and all(z.get('tap_action',{}).get('target',{}).get('entity_id')==ENTITY for z in x['cards']):
       x['columns']=3
       for z in x['cards']:
        z['card_mod']['style']+='\nha-card { height: 40px !important; aspect-ratio: auto; }\nimg, #image { height: 100% !important; object-fit: contain !important; }\n'
       x['cards'].append(copy.deepcopy(more))
       x.setdefault('card_mod',{}).setdefault('style','')
       x['card_mod']['style']+='''
#root { grid-template-columns: minmax(0, 2fr) minmax(0, 2fr) minmax(40px, 1fr) !important; gap: 8px; align-items: stretch; }
'''
       found.append(True);return
      for value in x.values():walk(value)
     elif isinstance(x,list):
      for value in x:walk(value)
    walk(config);assert len(found)==1,(path,len(found))
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');(ROOT/f'ha-backups/before-sony-more-apps-{stamp}.json').write_text(json.dumps(old,indent=2))
   for path in paths:
    assert await call('lovelace/config',url_path=path)==old[path]
    await call('lovelace/config/save',url_path=path,config=new[path]);assert await call('lovelace/config',url_path=path)==new[path]
   (ROOT/'sunday/sony-more-apps-popup.json').write_text(json.dumps(popup,indent=2))
   print('Verified 40/40/20 shortcut rows and 29-app launch grids in all five Sony remote copies.')
asyncio.run(main())
