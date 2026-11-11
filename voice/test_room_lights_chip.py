import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
async def main():
 async with async_playwright() as p:
  b=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  page=await b.new_page(viewport={'width':360,'height':700})
  await page.set_content('<body></body>')
  await page.add_script_tag(content=Path('/home/pi/homeassistant/www/room-lights-chip.js').read_text())
  await page.evaluate('''() => {window.calls=[];window.chip=document.createElement('mushroom-room-lights-chip');chip.setConfig({entities:['light.a','light.b','light.unavailable'],room:'Test'});chip.hass={states:{'light.a':{state:'on'},'light.b':{state:'off'},'light.unavailable':{state:'unavailable'}},callService:async(...args)=>calls.push(args)};document.body.append(chip);}''')
  assert await page.locator('.number').inner_text()=='1'
  await page.get_by_role('button',name='Turn on all lights in Test',exact=True).click()
  await page.get_by_role('button',name='Turn off all lights in Test',exact=True).click()
  calls=await page.evaluate('calls')
  assert calls==[['light','turn_on',{'entity_id':['light.a','light.b']}],['light','turn_off',{'entity_id':['light.a','light.b']}]],calls
  await page.evaluate("chip.hass={...chip.hass,states:{'light.a':{state:'off'},'light.b':{state:'off'},'light.unavailable':{state:'unavailable'}}}")
  assert await page.locator('.number').inner_text()=='0'
  await page.evaluate("() => { chip._hass.callService=async()=>{throw Error('test')}; }")
  await page.get_by_role('button',name='Turn on all lights in Test',exact=True).click()
  assert await page.locator('.group').evaluate("e=>e.classList.contains('error')")
  assert await page.get_by_role('button',name='Turn on all lights in Test',exact=True).is_enabled()
  print('Passed: live count, on/off service targeting, unavailable exclusion, error feedback and recovery. No real devices controlled.')
  await b.close()
asyncio.run(main())
