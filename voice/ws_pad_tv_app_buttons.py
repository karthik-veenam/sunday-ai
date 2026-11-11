"""Add breathing room above the TV app shortcut row."""
import ws_fix_hall_remote_position as runner

def fix(config):
 cards=config['views'][0]['cards'][0]['cards']
 tv=next(c for c in cards if c.get('type')=='custom:mod-card' and any(x.get('entity')=='media_player.bravia_kd_65x75k' for x in c.get('card',{}).get('cards',[])))
 row=tv['card']['cards'][1]['chips'][0]['tap_action']['browser_mod']['data']['content']['card']['cards'][-1]
 assert row.get('type')=='grid' and len(row['cards'])==2
 row['card_mod']={'style':':host { display: block; padding-top: 12px; }'}
 return config
runner.fix=fix
if __name__=='__main__':runner.asyncio.run(runner.main())
