import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
async def main():
 async with async_playwright() as p:
  b=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  page=await b.new_page()
  await page.route('http://feedback.test/**',lambda r:r.fulfill(status=200,body='<button id="test">Test</button><button id="disabled" disabled>Disabled</button><p id="text">Label</p>',content_type='text/html'))
  await page.goto('http://feedback.test/dashboard-roka/sunday')
  await page.evaluate('''() => {window.haptics=0;window.clicks=0; window.addEventListener('haptic',()=>window.haptics++); document.querySelector('#test').addEventListener('click',()=>window.clicks++);}''')
  await page.add_script_tag(content=Path('/home/pi/homeassistant/www/dashboard-tap-feedback.js').read_text())
  await page.click('#test');await page.wait_for_timeout(200)
  assert await page.evaluate('haptics===1 && clicks===1')
  await page.click('#text');await page.wait_for_timeout(150)
  assert await page.evaluate('haptics===1')
  await page.evaluate("history.pushState({},'', '/config/devices')")
  await page.click('#test');await page.wait_for_timeout(150)
  assert await page.evaluate('haptics===1 && clicks===2')
  print('Browser checks passed: tap haptic, original action preserved, labels ignored, settings excluded.')
  await b.close()
asyncio.run(main())
