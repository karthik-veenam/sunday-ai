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
  for view in ['sunday']:
   await page.goto('http://127.0.0.1:8123/dashboard-roka/'+view,wait_until='domcontentloaded')
   await page.wait_for_timeout(15000)
   print('card-mod defined:', await page.evaluate("Boolean(customElements.get('card-mod'))"),flush=True)
   await page.get_by_role('tab',name='Hall',exact=True).click()
   await page.wait_for_timeout(1000)
   await page.get_by_role('tab',name='Sunday',exact=True).click()
   await page.wait_for_timeout(4000)
   await page.screenshot(path='/tmp/design-review-'+view+'.png',full_page=True)
   print('Captured',view,flush=True)
  await browser.close()
asyncio.run(main())
