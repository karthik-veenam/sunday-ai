import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi');TV='media_player.bravia_kd_65x75k';CAST='media_player.vasuden_tv'
PRE="""{% set tv_on = states('media_player.bravia_kd_65x75k') not in ['off','standby','unknown','unavailable'] %}
{% set rich = tv_on and states('media_player.vasuden_tv') in ['playing','paused'] and state_attr('media_player.vasuden_tv','media_title') %}
"""
COLORS=PRE+""":host {
 --primary-text-color: {{ '#ffffff' if tv_on else '#233247' }};
 --secondary-text-color: {{ '#e4edf8' if tv_on else '#52657b' }};
 --rgb-primary-text-color: {{ '255,255,255' if tv_on else '35,50,71' }};
 --rgb-secondary-text-color: {{ '228,237,248' if tv_on else '82,101,123' }};
}
"""
BACKGROUND=PRE+"""ha-card {
 {% if rich and state_attr('media_player.vasuden_tv','entity_picture') %}
 background: linear-gradient(180deg, rgba(6,17,34,.35) 0%, rgba(6,17,34,.73) 48%, rgba(6,17,34,.94) 100%), url("{{ state_attr('media_player.vasuden_tv','entity_picture') }}") center 30% / cover no-repeat !important;
 {% elif tv_on %}
 background: linear-gradient(135deg, #56ccf2, #2f80ed) !important;
 {% else %}
 background: #f9fbfe !important;
 {% endif %}
 position: relative; overflow: hidden; border-radius: 14px;
 border: 1px solid {{ 'rgba(255,255,255,.16)' if tv_on else '#d6e0eb' }};
 box-shadow: {{ '0 5px 20px rgba(15,35,65,.14)' if rich else 'none' }};
}
"""+COLORS
TIME="""{% set d = state_attr('media_player.vasuden_tv','media_duration') | float(0) %}
{% set p = state_attr('media_player.vasuden_tv','media_position') | float(0) %}
{% set updated = as_timestamp(state_attr('media_player.vasuden_tv','media_position_updated_at'), 0) %}
{% if is_state('media_player.vasuden_tv','playing') and updated > 0 %}{% set p = p + [0, as_timestamp(now()) - updated] | max %}{% endif %}
{% set p = [[p,0] | max,d] | min %}
"""
RICH_CONDITIONS=[{'entity':TV,'state':'on'},{'condition':'or','conditions':[{'entity':CAST,'state':'playing'},{'entity':CAST,'state':'paused'}]}]
header={'type':'custom:mushroom-template-card','entity':TV,'primary':'TV','secondary':PRE+"""{% if not tv_on %}{{ 'Unavailable' if states('media_player.bravia_kd_65x75k') in ['unknown','unavailable'] else 'Off' }}{% elif rich %}{{ state_attr('media_player.vasuden_tv','app_name') or 'TV' }} · {{ 'Paused' if is_state('media_player.vasuden_tv','paused') else 'Playing' }}{% else %}{{ state_attr('media_player.vasuden_tv','app_name') if states('media_player.vasuden_tv') not in ['off','unknown','unavailable'] and state_attr('media_player.vasuden_tv','app_name') else (state_attr('media_player.bravia_kd_65x75k','source') or 'On') }}{% endif %}""",'icon':'mdi:television','icon_color':"{{ 'light-blue' if is_state('"+TV+"','on') else 'grey' }}",'tap_action':{'action':'more-info'},'hold_action':{'action':'none'},'card_mod':{'style':'ha-card { background: transparent !important; border: none; box-shadow: none; padding-right: 100px; }\n'+COLORS}}
info={'type':'custom:mushroom-template-card','entity':CAST,'primary':"{{ state_attr('"+CAST+"','media_title') or 'Now playing' }}",'secondary':"{% set artist = state_attr('"+CAST+"','media_artist') %}{% set series = state_attr('"+CAST+"','media_series_title') %}{{ artist or series or '' }}",'icon':'','multiline_secondary':True,'tap_action':{'action':'more-info'},'card_mod':{'style':'''ha-card { background: transparent !important; border: none; box-shadow: none; padding: 14px 0 0; }
mushroom-shape-icon, mushroom-shape-avatar { display: none; }
:host { --card-primary-font-size: 20px; --card-primary-line-height: 25px; --card-primary-font-weight: 700; }
''' +COLORS,'mushroom-state-info $':'.primary { white-space: normal !important; overflow: visible !important; line-height: 25px; font-size: 20px; font-weight: 700; }'}}
info['card_mod']={'style':{'.':info['card_mod']['style'],'mushroom-state-info $':info['card_mod']['mushroom-state-info $']}}
# A native markdown label supplies elapsed/duration; the line advances between state reports.
progress={'type':'markdown','content':TIME+"""{% if d > 0 %}{% set pi = p | int %}{% set di = d | int %}{{ '%d:%02d' | format(pi // 60, pi % 60) }} / {{ '%d:%02d' | format(di // 60, di % 60) }}{% endif %}""",'card_mod':{'style':TIME+"""ha-card { position: relative; margin: 0 12px 5px; padding: 7px 0 0; background: transparent !important; border: none; box-shadow: none; color: #dce8f7; font-size: 11px; }
ha-card::before { content: ''; position: absolute; inset: 0 0 auto; height: 3px; background: rgba(255,255,255,.22); border-radius: 2px; }
ha-card::after { content: ''; position: absolute; inset: 0 0 auto; height: 3px; border-radius: 2px; background: #9addff; transform-origin: left; transform: scaleX({{ p / d if d > 0 else 0 }}); {% if d > p and is_state('media_player.vasuden_tv','playing') %}animation: hall-tv-progress {{ d-p }}s linear forwards;{% endif %} }
@keyframes hall-tv-progress { from { transform: scaleX({{ p / d if d > 0 else 0 }}); } to { transform: scaleX(1); } }
"""}}
async def main():
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
     if not isinstance(x,dict):return
     if x.get('type')=='custom:mod-card' and x.get('card',{}).get('type')=='vertical-stack':
      children=x['card'].get('cards',[])
      if children and children[0].get('type')=='custom:mushroom-media-player-card' and children[0].get('entity')==TV:
       player=copy.deepcopy(children[0]);buttons=copy.deepcopy(children[1:])
       player['card_mod']['style']='ha-card { background: transparent !important; border: none; box-shadow: none; }\nmushroom-state-item { display: none !important; }\n:host { --control-button-background-color: rgba(255,255,255,.12); --control-slider-background: rgba(255,255,255,.16); }\n'+COLORS
       player['collapsible_controls']=False;player['show_volume_level']=True
       media={'type':'conditional','conditions':RICH_CONDITIONS,'card':{'type':'vertical-stack','cards':[copy.deepcopy(info),copy.deepcopy(progress)]}}
       controls={'type':'conditional','conditions':[{'entity':TV,'state':'on'}],'card':player}
       x['card']['cards']=[copy.deepcopy(header),media,controls,*buttons]
       x['card_mod']['style']=BACKGROUND
       found.append(True);return
     for child in x.get('cards',[]):walk(child)
     if isinstance(x.get('card'),dict):walk(x['card'])
    for view in config['views']:
     for card in view.get('cards',[]):walk(card)
    assert len(found)==1,(path,len(found))
   stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');(ROOT/f'ha-backups/before-hall-rich-now-playing-{stamp}.json').write_text(json.dumps(old,indent=2))
   for path in paths:
    assert await call('lovelace/config',url_path=path)==old[path]
    await call('lovelace/config/save',url_path=path,config=new[path]);assert await call('lovelace/config',url_path=path)==new[path]
   (ROOT/'sunday/hall-rich-tv-templates.json').write_text(json.dumps({'header':header['secondary'],'title':info['primary'],'progress':progress['content'],'background':BACKGROUND,'progress_style':progress['card_mod']['style']},indent=2))
   print('Saved and verified rich Cast artwork/title/app/progress with Bravia controls and existing remote popup across five Hall copies.')
asyncio.run(main())
