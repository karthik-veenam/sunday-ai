import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  context=await browser.new_context(viewport={'width':400,'height':900})
  script='''if(location.origin==='http://127.0.0.1:8123')localStorage.setItem('hassTokens',JSON.stringify({hassUrl:location.origin,access_token:TOKEN,token_type:'Bearer',expires:Date.now()+3600000,expires_in:3600,clientId:location.origin+'/'}));'''.replace('TOKEN',json.dumps(token))
  await context.add_init_script(script)
  page=await context.new_page()
  for view in ['hall']:
   await page.goto('http://127.0.0.1:8123/dashboard-roka/'+view,wait_until='domcontentloaded')
   await page.wait_for_timeout(12000)
   await page.screenshot(path='/tmp/design-review-'+view+'.png',full_page=True)
   print(await page.evaluate("""() => {let found=[];function walk(r){for(let e of r.querySelectorAll('*')){const c=e._config||e.config;if(e.tagName==='MUSHROOM-TEMPLATE-CHIP'&&c?.icon==='mdi:power'&&c?.entity==='media_player.bravia_kd_65x75k'){let b=e.getBoundingClientRect();found.push({x:b.x,y:b.y,width:b.width,height:b.height,action:c.tap_action});}if(e.shadowRoot)walk(e.shadowRoot);}}walk(document);return found;}"""))
   print('Captured',view,flush=True)
  await browser.close()
asyncio.run(main())
