import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with async_playwright() as p:
  b=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  c=await b.new_context(viewport={'width':400,'height':700})
  await c.add_init_script("if(location.origin==='http://127.0.0.1:8123')localStorage.setItem('hassTokens',JSON.stringify({hassUrl:location.origin,access_token:"+json.dumps(token)+",token_type:'Bearer',expires:Date.now()+3600000,expires_in:3600,clientId:location.origin+'/'}));")
  page=await c.new_page();await page.goto('http://127.0.0.1:8123/dashboard-roka/hall',wait_until='domcontentloaded')
  await page.wait_for_function("Boolean(customElements.get('hall-tv-seek-card'))",timeout=25000)
  await page.evaluate('''()=>{window.testSeeks=[];const box=document.createElement('div');box.id='seek-check';box.style.cssText='position:fixed;top:80px;left:8px;width:384px;background:#10243b;z-index:99999';const card=document.createElement('hall-tv-seek-card');card.setConfig({entity:'media_player.preview'});card.hass={states:{'media_player.preview':{state:'playing',attributes:{media_position:300,media_duration:600,media_position_updated_at:new Date().toISOString(),supported_features:2,media_title:'Preview'}}},callService:async(...args)=>testSeeks.push(args)};box.append(card);document.body.append(box);}''')
  await page.wait_for_timeout(600)
  await page.locator('#seek-check').get_by_role('button',name='Forward 10 seconds',exact=True).click()
  assert await page.evaluate('testSeeks.length')==1
  seek=await page.evaluate('testSeeks[0][2].seek_position');assert 310<=seek<313,seek
  assert await page.locator('#seek-check input').is_enabled()
  await page.screenshot(path='/tmp/seek-control-integrated.png')
  print('HA-loaded frontend module registers the card; integrated button emits one correctly targeted seek. No real TV control sent.')
  await b.close()
asyncio.run(main())
