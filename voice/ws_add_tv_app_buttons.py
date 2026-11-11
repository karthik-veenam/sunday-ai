"""Add official TV-provided app icons and direct launch actions to the Hall remote."""
import json
import ws_fix_hall_remote_position as runner
from pathlib import Path

APPS=json.loads(Path('/home/djpi/ha-assets/remote-apps/apps.json').read_text())
def add_buttons(config):
 cards=config['views'][0]['cards'][0]['cards']
 tv=next(c for c in cards if c.get('type')=='custom:mod-card' and any(x.get('entity')=='media_player.bravia_kd_65x75k' for x in c.get('card',{}).get('cards',[])))
 popup=tv['card']['cards'][1]['chips'][0]['tap_action']['browser_mod']['data']
 content=popup['content']['card']['cards']
 assert len(content)==5, 'Unexpected remote structure or app shortcuts already present'
 original=json.dumps(content,sort_keys=True)
 appcards=[]
 for app in sorted(APPS,key=lambda a:0 if a['name']=='YouTube' else 1):
  appcards.append({'type':'button','name':app['name'],'show_name':True,'show_icon':False,'show_state':False,'show_entity_picture':True,'entity_picture':app['image'],'tap_action':{'action':'perform-action','perform_action':'media_player.play_media','target':{'entity_id':'media_player.bravia_kd_65x75k'},'data':{'media_content_type':'app','media_content_id':app['id']}},'hold_action':{'action':'none'},'card_mod':{'style':'''ha-card {
    height: 78px; background: #ffffff; border: 1px solid #d6e1ed;
    border-radius: 16px; box-shadow: 0 2px 6px rgba(43,65,91,.05);
    --primary-text-color: #233247;
   }
   img { width: 34px !important; height: 34px !important; border-radius: 8px !important; object-fit: contain !important; }
   #name, .name { font-size: 12px !important; font-weight: 650 !important; line-height: 18px !important; }
  '''}})
 content.append({'type':'grid','columns':2,'square':False,'cards':appcards})
 assert json.dumps(content[:-1],sort_keys=True)==original, 'Existing remote controls changed'
 return config

async def main():
 import aiohttp
 async with aiohttp.ClientSession() as session:
  for app in APPS:
   async with session.get('http://localhost:8123'+app['image']) as r:
    assert r.status==200 and r.content_type.startswith('image/'), 'App artwork not served'
    await r.read()
 await runner.main()

runner.fix=add_buttons
if __name__=='__main__':runner.asyncio.run(main())
