"""Use full-width official brand artwork as the app shortcut buttons."""
import ws_fix_hall_remote_position as runner

def fix(config):
 cards=config['views'][0]['cards'][0]['cards']
 tv=next(c for c in cards if c.get('type')=='custom:mod-card' and any(x.get('entity')=='media_player.bravia_kd_65x75k' for x in c.get('card',{}).get('cards',[])))
 row=tv['card']['cards'][1]['chips'][0]['tap_action']['browser_mod']['data']['content']['card']['cards'][-1]
 assert len(row['cards'])==2 and all(c.get('type')=='picture' for c in row['cards'])
 for card in row['cards']:
  name=card['alt_text']
  card['image']='/local/remote-apps/'+('youtube-wordmark.jpg' if name=='YouTube' else 'jiohotstar-wordmark.png')
  card['card_mod']={'style':'''ha-card {
    width: 100%; aspect-ratio: 16 / 9; box-sizing: border-box;
    background: #fff; border: 1px solid #d6e1ed;
    border-radius: 8px !important; overflow: hidden;
    box-shadow: 0 2px 5px rgba(43,65,91,.06);
   }
   img, #image {
    display: block !important; width: 100% !important; height: 100% !important;
    object-fit: contain !important; margin: 0 !important; border-radius: 0 !important;
   }
  '''}
 return config
runner.fix=fix
if __name__=='__main__':runner.asyncio.run(runner.main())
