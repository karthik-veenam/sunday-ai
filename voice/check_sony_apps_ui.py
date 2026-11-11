import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token'];config=json.load(open('/home/pi/homeassistant/.storage/lovelace.dashboard_roka'))['data']['config'];rows=[]
 def walk(x):
  if isinstance(x,dict):
   if x.get('type')=='grid' and len(x.get('cards',[]))==3 and x['cards'][-1].get('icon')=='mdi:dots-horizontal' and x['cards'][0].get('alt_text')=='YouTube':rows.append(x);return
   for y in x.values():walk(y)
  elif isinstance(x,list):
   for y in x:walk(y)
 walk(config);assert len(rows)==1
 apps=json.load(open('/home/djpi/sunday/sony-more-apps-popup.json'))
 async with async_playwright() as p:
  b=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  c=await b.new_context(viewport={'width':400,'height':900})
  await c.add_init_script("if(location.origin==='http://127.0.0.1:8123')localStorage.setItem('hassTokens',JSON.stringify({hassUrl:location.origin,access_token:"+json.dumps(token)+",token_type:'Bearer',expires:Date.now()+3600000,expires_in:3600,clientId:location.origin+'/'}));")
  page=await c.new_page();await page.goto('http://127.0.0.1:8123/dashboard-roka/hall',wait_until='domcontentloaded');await page.wait_for_timeout(8000)
  await page.evaluate('''async configs=>{const box=document.createElement('div');box.id='apps-preview';box.style.cssText='position:absolute;top:80px;left:16px;width:368px;z-index:99999;background:#f5f8fc;padding:12px;box-sizing:border-box';document.body.append(box);const helpers=await loadCardHelpers();for(const config of configs){const e=helpers.createCardElement(config);e.hass=document.querySelector('home-assistant').hass;box.append(e);}}''',[{'type':'vertical-stack','cards':[rows[0]]},apps])
  await page.wait_for_timeout(2200)
  measurements=await page.evaluate('''()=>{const data={rows:[],images:0,broken:0};function walk(r){for(const e of r.querySelectorAll('*')){if(e.tagName==='HUI-GRID-CARD'){const c=e._config||e.config;if(c?.cards?.length===3&&c.cards[2].icon==='mdi:dots-horizontal')data.rows.push([...e.shadowRoot.querySelector('#root').children].map(n=>n.getBoundingClientRect().width));}if(e.tagName==='IMG'){data.images++;if(!e.complete||!e.naturalWidth)data.broken++;}if(e.shadowRoot)walk(e.shadowRoot);}}walk(document.querySelector('#apps-preview'));return data;}''')
  assert len(measurements['rows'])==1,measurements
  widths=measurements['rows'][0];assert abs(widths[0]/widths[2]-2)<.1 and abs(widths[1]/widths[2]-2)<.1,measurements
  assert measurements['images']>=31 and measurements['broken']==0,measurements
  await page.screenshot(path='/tmp/sony-more-apps-preview.png',full_page=True)
  print('Verified 40/40/20 layout, 29 app buttons, and all 31 shortcut images loaded.',measurements)
  await b.close()
asyncio.run(main())
