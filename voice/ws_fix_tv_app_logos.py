"""Use real image cards for app artwork with restrained rounded corners."""
import copy,json
from pathlib import Path
import ws_fix_hall_remote_position as runner
APPS={a['name']:a for a in json.loads(Path('/home/djpi/ha-assets/remote-apps/apps.json').read_text())}

def fix(config):
 cards=config['views'][0]['cards'][0]['cards']
 tv=next(c for c in cards if c.get('type')=='custom:mod-card' and any(x.get('entity')=='media_player.bravia_kd_65x75k' for x in c.get('card',{}).get('cards',[])))
 content=tv['card']['cards'][1]['chips'][0]['tap_action']['browser_mod']['data']['content']['card']['cards']
 row=content[-1]
 assert [c.get('name') for c in row['cards']]==['YouTube','JioHotstar']
 for i,old in enumerate(row['cards']):
  app=APPS[old['name']]
  row['cards'][i]={'type':'picture','image':app['image'],'alt_text':app['name'],'tap_action':copy.deepcopy(old['tap_action']),'hold_action':{'action':'none'},'card_mod':{'style':'''ha-card {
    height: 64px !important; box-sizing: border-box;
    display: flex; align-items: center; justify-content: center;
    background: #ffffff; border: 1px solid #d6e1ed;
    border-radius: 8px !important; overflow: hidden;
    box-shadow: 0 1px 4px rgba(43,65,91,.04);
   }
   img, #image {
    display: block !important; width: 42px !important; height: 42px !important;
    max-width: 100%; object-fit: contain !important;
    border-radius: 4px; margin: auto;
   }
  '''}}
 return config
runner.fix=fix
if __name__=='__main__':runner.asyncio.run(runner.main())
