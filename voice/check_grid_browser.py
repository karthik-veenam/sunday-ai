import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright
async def main():
 token=json.loads(Path('/home/djpi/sunday/config.json').read_text())['ha_token']
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  page=await browser.new_page(viewport={'width':400,'height':900},device_scale_factor=1)
  await page.goto('http://127.0.0.1:8123/')
  await page.evaluate('''token => localStorage.setItem('hassTokens', JSON.stringify({hassUrl:location.origin, access_token:token, token_type:'Bearer', expires:Date.now()+3600000, expires_in:3600, clientId:location.origin+'/'}))''',token)
  await page.goto('http://127.0.0.1:8123/dashboard-roka/sunday')
  await page.wait_for_timeout(10000)
  await page.screenshot(path='/tmp/roka-grid-check.png',full_page=True)
  print(await page.evaluate('''() => {let found=[]; function walk(root){for(const e of root.querySelectorAll('*')){if(['HUI-THERMOSTAT-CARD','HUI-TILE-CARD'].includes(e.tagName)){const r=e.getBoundingClientRect();found.push({tag:e.tagName,entity:e.config?.entity||e._config?.entity,x:r.x,y:r.y,height:r.height,bottom:r.bottom});}if(e.shadowRoot)walk(e.shadowRoot);}}walk(document);return found;}'''))
  await browser.close()
asyncio.run(main())
