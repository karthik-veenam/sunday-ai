import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token'];cases=[]
 for dash in ['roka','rani','sian']:
  config=json.load(open('/home/pi/homeassistant/.storage/lovelace.dashboard_'+dash))['data']['config']
  def walk(x):
   if isinstance(x,dict):
    if x.get('type')=='custom:mod-card' and x.get('card',{}).get('type')=='grid' and any(c.get('icon')=='mdi:chevron-up' for c in x['card'].get('cards',[])):
     cases.append((dash,x));return
    for value in x.values():walk(value)
   elif isinstance(x,list):
    for value in x:walk(value)
  walk(config)
 async with async_playwright() as p:
  b=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  c=await b.new_context(viewport={'width':360,'height':850})
  await c.add_init_script("if(location.origin==='http://127.0.0.1:8123')localStorage.setItem('hassTokens',JSON.stringify({hassUrl:location.origin,access_token:"+json.dumps(token)+",token_type:'Bearer',expires:Date.now()+3600000,expires_in:3600,clientId:location.origin+'/'}));")
  page=await c.new_page();await page.goto('http://127.0.0.1:8123/dashboard-roka/hall',wait_until='domcontentloaded');await page.wait_for_timeout(8000)
  for index,(dash,card) in enumerate(cases):
   await page.evaluate('''async card => {document.querySelector('#dpad-size-check')?.remove();const box=document.createElement('div');box.id='dpad-size-check';box.style.cssText='position:fixed;top:100px;left:16px;width:328px;background:#f5f8fc;z-index:99999;padding:10px;box-sizing:border-box';document.body.append(box);const helpers=await window.loadCardHelpers();const element=helpers.createCardElement(card);element.hass=document.querySelector('home-assistant').hass;box.append(element);}''',card)
   await page.wait_for_timeout(1800)
   rects=await page.evaluate('''()=>{const out=[];function walk(r){for(const e of r.querySelectorAll('*')){if(e.tagName==='HUI-BUTTON-CARD'){const c=e._config||e.config;if(['mdi:chevron-up','mdi:chevron-left','mdi:check','mdi:chevron-right','mdi:chevron-down'].includes(c?.icon)){const el=e.shadowRoot?.querySelector('ha-card');const b=el.getBoundingClientRect();out.push({icon:c.icon,x:b.x,y:b.y,w:b.width,h:b.height});}}if(e.shadowRoot)walk(e.shadowRoot);}}walk(document.querySelector('#dpad-size-check'));return out;}''')
   assert len(rects)==5,(dash,rects)
   assert all(r['h']>=63 and r['w']>=63 for r in rects),(dash,rects)
   assert all(r['x']>=0 and r['x']+r['w']<=360 for r in rects),(dash,rects)
   if index==0:await page.screenshot(path='/tmp/remote-dpad-larger.png')
   print('Verified larger tap targets:',dash,index,[round(r['h']) for r in rects],flush=True)
  await b.close()
asyncio.run(main())
