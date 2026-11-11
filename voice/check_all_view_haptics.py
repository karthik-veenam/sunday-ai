import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
SOURCE=Path('/home/pi/homeassistant/www/dashboard-tap-feedback.js').read_text()
async def main():
 async with async_playwright() as p:
  b=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  page=await b.new_page(has_touch=True)
  await page.route('http://feedback.test/**',lambda r:r.fulfill(status=200,body='<button id="normal">Normal</button><button id="remote">Volume +</button><button id="temp">Temperature +</button><button id="disabled" disabled>Disabled</button><p id="label">Heading</p><div id="area"></div>',content_type='text/html'))
  await page.goto('http://feedback.test/dashboard-roka/sunday')
  await page.evaluate('''() => {window.pulses=0;window.actions=0;window.__vasuNativeHaptics=true;window.addEventListener('haptic',()=>pulses++);document.querySelectorAll('button').forEach(e=>e.addEventListener('click',()=>actions++));const card=document.createElement('hui-button-card');card._config={type:'button',tap_action:{action:'perform-action'}};card.attachShadow({mode:'open'}).innerHTML='<ha-card id="action-only" style="display:block;width:120px;height:50px">Action only</ha-card>';document.querySelector('#area').append(card);}''')
  await page.add_script_tag(content=SOURCE)
  for dash,views in [('roka',['sunday','hall','dining','kitchen']),('rani',['rani','hall','kitchen','dining']),('sian',['sian','hall','dining','kitchen'])]:
   for view in views:
    await page.evaluate("path=>history.pushState({},'',path)",'/dashboard-'+dash+'/'+view)
    for target in ['#normal','#remote','#temp']:
     before=await page.evaluate('pulses')
     await page.tap(target);await page.wait_for_timeout(130)
     assert await page.evaluate('pulses')==before+1,(dash,view,target)
    before=await page.evaluate('pulses')
    await page.locator('#action-only').evaluate("e=>e.dispatchEvent(new CustomEvent('action',{detail:{action:'tap'},bubbles:true,composed:true}))")
    await page.wait_for_timeout(130)
    assert await page.evaluate('pulses')==before+1,(dash,view,'action')
  before=await page.evaluate('pulses');await page.tap('#label');await page.wait_for_timeout(130);assert await page.evaluate('pulses')==before
  await page.evaluate("path=>history.pushState({},'',path)",'/config/devices')
  await page.tap('#normal');await page.wait_for_timeout(130);assert await page.evaluate('pulses')==before
  assert await page.evaluate('actions')==37
  print('Passed all 12 view routes: touch buttons, remote buttons, temperature buttons, action-only HA buttons, one pulse per tap, normal actions preserved. Labels and settings excluded.')
  await b.close()
asyncio.run(main())
