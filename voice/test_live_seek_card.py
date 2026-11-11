import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  page=await browser.new_page(viewport={'width':400,'height':250})
  await page.set_content('<body style="background:#10243b;color:white"></body>')
  await page.add_script_tag(content=Path('/home/pi/homeassistant/www/hall-tv-seek-card.js').read_text())
  await page.evaluate('''()=>{window.calls=[];window.chip=document.createElement('hall-tv-seek-card');chip.setConfig({entity:'media_player.test'});window.setPlayer=(state,position,features=2,title='Episode')=>{chip._optimistic=null;chip.hass={states:{'media_player.test':{state,attributes:{media_title:title,media_duration:600,media_position:position,media_position_updated_at:new Date().toISOString(),supported_features:features}}},callService:async(...args)=>{calls.push(args)}}};document.body.append(chip);setPlayer('playing',100);}''')
  await page.wait_for_timeout(1700)
  assert await page.locator('.elapsed').inner_text()=='1:41'
  await page.evaluate("setPlayer('paused',40)");await page.wait_for_timeout(1100)
  assert await page.locator('.elapsed').inner_text()=='0:40'
  await page.get_by_role('button',name='Forward 10 seconds',exact=True).click()
  assert await page.evaluate('calls.at(-1)[2].seek_position')==50
  await page.get_by_role('button',name='Back 10 seconds',exact=True).click()
  assert await page.evaluate('calls.at(-1)[2].seek_position')==40
  before=await page.evaluate('calls.length')
  await page.locator('input').evaluate("e=>{e.value='300';e.dispatchEvent(new Event('input',{bubbles:true}));}")
  assert await page.evaluate('calls.length')==before
  await page.locator('input').evaluate("e=>e.dispatchEvent(new Event('change',{bubbles:true}))")
  assert await page.evaluate('calls.length')==before+1
  assert await page.evaluate('calls.at(-1)[2].seek_position')==300
  await page.evaluate("setPlayer('paused',2)")
  await page.get_by_role('button',name='Back 10 seconds',exact=True).click()
  assert await page.evaluate('calls.at(-1)[2].seek_position')==0
  await page.evaluate("setPlayer('paused',595)")
  await page.get_by_role('button',name='Forward 10 seconds',exact=True).click()
  assert await page.evaluate('calls.at(-1)[2].seek_position')==600
  await page.evaluate("setPlayer('playing',30,0)")
  assert await page.locator('input').is_disabled()
  assert await page.get_by_role('button',name='Forward 10 seconds',exact=True).is_disabled()
  await page.evaluate("()=>{setPlayer('paused',70);chip._hass.callService=async()=>{throw Error('test')};}")
  await page.get_by_role('button',name='Forward 10 seconds',exact=True).click()
  assert await page.locator('.row').evaluate("e=>e.classList.contains('error')")
  assert await page.locator('.elapsed').inner_text()=='1:10'
  assert await page.get_by_role('button',name='Forward 10 seconds',exact=True).is_enabled()
  await page.screenshot(path='/tmp/live-tv-seek-control.png')
  print('Passed: live elapsed clock, pause, relative +/-10, drag preview and single seek on release, bounds, unsupported-state disabling, error recovery. No real TV commands sent.')
  await browser.close()
asyncio.run(main())
