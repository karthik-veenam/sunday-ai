import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token'];config=json.load(open('/home/pi/homeassistant/.storage/lovelace.dashboard_roka'))['data']['config'];cards=[]
 def walk(x):
  if not isinstance(x,dict):return
  if x.get('type')=='custom:mod-card' and x.get('card',{}).get('cards',[{}])[0].get('entity')=='media_player.bravia_kd_65x75k' and x['card']['cards'][0].get('type')=='custom:mushroom-template-card':cards.append(x);return
  for v in x.get('cards',[]):walk(v)
  if isinstance(x.get('card'),dict):walk(x['card'])
 for v in config['views']:
  if v.get('path')=='hall':
   for c in v['cards']:walk(c)
 assert len(cards)==1
 async with async_playwright() as p:
  b=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  c=await b.new_context(viewport={'width':400,'height':750})
  await c.add_init_script("if(location.origin==='http://127.0.0.1:8123')localStorage.setItem('hassTokens',JSON.stringify({hassUrl:location.origin,access_token:"+json.dumps(token)+",token_type:'Bearer',expires:Date.now()+3600000,expires_in:3600,clientId:location.origin+'/'}));")
  page=await c.new_page();await page.goto('http://127.0.0.1:8123/dashboard-roka/hall',wait_until='domcontentloaded');await page.wait_for_timeout(9000)
  await page.evaluate('''async card=>{const box=document.createElement('div');box.id='rich-tv-preview';box.style.cssText='position:fixed;top:80px;left:8px;width:384px;z-index:99999;';document.body.append(box);const helpers=await loadCardHelpers();const element=helpers.createCardElement({type:'vertical-stack',cards:[card]});element.hass=document.querySelector('home-assistant').hass;box.append(element);}''',cards[0]);await page.wait_for_timeout(5000)
  result=await page.evaluate('''()=>{const result={texts:[],background:false,power:0,remote:0};function walk(r){for(const e of r.querySelectorAll('*')){if(e.tagName==='HA-CARD'&&getComputedStyle(e).backgroundImage.includes('url('))result.background=true;const config=e._config||e.config;if(e.tagName==='MUSHROOM-TEMPLATE-CARD'&&config?.entity==='media_player.vasuden_tv')result.texts.push(e.shadowRoot?.textContent||'');if(e.tagName==='MUSHROOM-TEMPLATE-CHIP'&&config?.icon==='mdi:power')result.power++;if(e.tagName==='MUSHROOM-ACTION-CHIP'&&config?.icon==='mdi:remote-tv')result.remote++;if(e.shadowRoot)walk(e.shadowRoot);}}walk(document.querySelector('#rich-tv-preview'));const b=document.querySelector('#rich-tv-preview').getBoundingClientRect();return {...result,width:b.width,height:b.height};}''')
  assert result['background'], 'Artwork backdrop did not render'
  assert result['power']==1 and result['remote']==1,result
  assert result['height']<400,result['height']
  assert result['texts'], 'No now-playing title'
  await page.screenshot(path='/tmp/hall-rich-tv-preview.png')
  print('Verified artwork background, now-playing title, persistent power/remote controls; card height:',round(result['height']))
  await b.close()
asyncio.run(main())
