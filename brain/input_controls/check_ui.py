import asyncio,json
from pathlib import Path
import requests
from playwright.async_api import async_playwright
ROOT=Path('/home/djpi/sunday2-rebuild')
async def main():
 ha=json.loads(Path('/home/djpi/sunday/config.json').read_text())['ha_token'];c=json.loads((ROOT/'config.json').read_text())
 snapshot=requests.get('http://127.0.0.1:8090/v1/inputs',headers={'Authorization':'Bearer '+c['daemon_token']},timeout=10).json()
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);ctx=await browser.new_context(viewport={'width':400,'height':950})
  await ctx.add_init_script("if(location.origin==='http://127.0.0.1:8123')localStorage.setItem('hassTokens',JSON.stringify({hassUrl:location.origin,access_token:"+json.dumps(ha)+",token_type:'Bearer',expires:Date.now()+3600000,expires_in:3600,clientId:location.origin+'/'}));")
  page=await ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  await page.goto('http://127.0.0.1:8123/sunday-inputs/inputs',wait_until='domcontentloaded')
  card=page.locator('sunday-input-card');await card.locator('.sources input').first.wait_for(timeout=45000)
  await page.wait_for_function("()=>{function find(r){for(const e of r.querySelectorAll('*')){if(e.tagName==='SUNDAY-INPUT-CARD')return e;if(e.shadowRoot){const x=find(e.shadowRoot);if(x)return x;}}}return !!find(document)?._data?.devices?.length}",timeout=20000)
  assert await card.get_by_role('checkbox',name='Your location / zones — proactivity',exact=True).is_checked()
  assert not await card.get_by_role('checkbox',name='Learned memories and expectations — proactivity',exact=True).is_checked()
  dimensions=await card.evaluate('(e)=>({width:e.shadowRoot.querySelector(".wrap").clientWidth,scroll:e.shadowRoot.querySelector(".wrap").scrollWidth})');assert dimensions['scroll']<=dimensions['width']+1,dimensions
  await page.screenshot(path=str(ROOT/'input_controls/dashboard-mobile.png'),full_page=True)
  await card.locator('.search').fill('fan.roka_s_room_roka_fan');await page.wait_for_timeout(300);assert await card.locator('.group').count()==1
  await card.get_by_role('button',name='Choose individual entities').click();assert await card.locator('.children input[type=checkbox]').count()>=2
  await page.set_viewport_size({'width':1250,'height':1000});await card.locator('.search').fill('');await page.wait_for_timeout(300);await page.screenshot(path=str(ROOT/'input_controls/dashboard-desktop.png'),full_page=True)
  assert not errors,errors
  # Exercise saves in an isolated card with a fake HA adapter, never change live enrollment.
  isolated=await ctx.new_page();await isolated.set_content('<sunday-input-card></sunday-input-card>');await isolated.add_script_tag(content=(ROOT/'input_controls/input-card.js').read_text())
  await isolated.evaluate('''data=>{window.data=data;window.saved=[];const card=document.querySelector('sunday-input-card');card.setConfig({});card.hass={callWS:async req=>{if(req.service==='sunday_input_manage'){window.saved.push(req.service_data.payload);for(const c of req.service_data.payload.changes){if(c.kind==='source')data.sources.find(s=>s.id===c.id).selected[c.purpose]=c.enabled;else for(const g of data.devices)for(const e of g.entities)if(e.id===c.id)e.selected[c.purpose]=c.enabled;}data.revision+='x';}return {response:{status:200,content:structuredClone(data)}}}};}''',snapshot)
  fake=isolated.locator('sunday-input-card');cb=fake.get_by_role('checkbox',name='Your location / zones — proactivity',exact=True);await cb.wait_for();await cb.uncheck();await isolated.wait_for_function('window.saved.length===1');await isolated.wait_for_function('!document.querySelector("sunday-input-card")._busy');assert not await cb.is_checked()
  assert await isolated.evaluate("window.saved[0].changes[0].id==='presence' && window.saved[0].changes[0].purpose==='proactivity' && window.saved[0].changes[0].enabled===false")
  await fake.locator('.search').fill('fan.roka_s_room_roka_fan');await isolated.wait_for_timeout(300);await fake.get_by_role('button',name='Choose individual entities').click();entity=fake.locator('.children').get_by_role('checkbox',name=' — proactivity',exact=False).first;await entity.uncheck();await isolated.wait_for_function('window.saved.length===2');assert await isolated.evaluate("window.saved[1].changes[0].id==='fan.roka_s_room_roka_fan' && window.saved[1].changes[0].purpose==='proactivity'")
  print('Live HA mobile/desktop render, initial checks, search, entity expansion, source/device save interactions: passed. Live selections unchanged.');await browser.close()
asyncio.run(main())
