import asyncio,json
from pathlib import Path
import requests
from playwright.async_api import async_playwright
ROOT=Path('/home/djpi/sunday2-rebuild')
async def main():
 c=json.loads((ROOT/'config.json').read_text());token=json.loads(Path('/home/djpi/sunday/config.json').read_text())['ha_token'];data=requests.get('http://127.0.0.1:8090/v1/notifications',headers={'Authorization':'Bearer '+c['daemon_token']},timeout=10).json()
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);ctx=await browser.new_context(viewport={'width':400,'height':1000})
  await ctx.add_init_script("if(location.origin==='http://127.0.0.1:8123')localStorage.setItem('hassTokens',JSON.stringify({hassUrl:location.origin,access_token:"+json.dumps(token)+",token_type:'Bearer',expires:Date.now()+3600000,expires_in:3600,clientId:location.origin+'/'}));")
  page=await ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  await page.goto('http://127.0.0.1:8123/device-automations/notifications',wait_until='domcontentloaded');card=page.locator('sunday-notification-card');await card.locator('.rule').first.wait_for(timeout=45000)
  assert await card.get_by_role('checkbox',name='Fridge / freezer door opened enabled').is_checked();assert await card.locator('.rule').count()==1
  await card.get_by_role('button',name='Edit',exact=True).click();assert await card.locator('.chips .chip').count()==2;assert await card.locator('#rule-value').input_value()=='on';assert await card.locator('#from-value').input_value()=='off';await card.get_by_role('button',name='Cancel',exact=True).click()
  dimensions=await card.evaluate('(e)=>({w:e.shadowRoot.querySelector(".wrap").clientWidth,s:e.shadowRoot.querySelector(".wrap").scrollWidth})');assert dimensions['s']<=dimensions['w']+1,dimensions;await page.screenshot(path=str(ROOT/'state_notifications/notifications-mobile.png'),full_page=True)
  await page.set_viewport_size({'width':1250,'height':1000});await page.screenshot(path=str(ROOT/'state_notifications/notifications-desktop.png'),full_page=True)
  # Existing combined views must instantiate their actual cards; no physical controls clicked.
  for view,tag in [('inputs','sunday-input-card'),('memory-inbox','sunday-memory-card'),('memories','sunday-memory-card'),('costs','sunday-cost-card'),('usage','sunday-cost-card'),('wake','sunday-wake-feedback-card')]:
   await page.goto('http://127.0.0.1:8123/sunday-hub/'+view,wait_until='domcontentloaded');await page.locator(tag).first.wait_for(timeout=25000)
  assert not errors,errors
  # Save/toggle interactions run only in an isolated mock adapter.
  fake=await ctx.new_page();await fake.set_content('<sunday-notification-card></sunday-notification-card>');await fake.add_script_tag(content=(ROOT/'state_notifications/notification-card.js').read_text());await fake.evaluate('''data=>{window.saved=[];const c=document.querySelector('sunday-notification-card');c.setConfig({});c.hass={callWS:async req=>{if(req.service==='sunday_notification_manage'){const b=req.service_data.payload;window.saved.push(b);if(b.action==='toggle')data.rules.find(r=>r.rule.id===b.id).rule.enabled=b.enabled;if(b.action==='save')data.rules.push({rule:{...b.rule,id:'fake-new'},revision:'new'});data.revision+='x';data.status='saved';}return {response:{status:200,content:structuredClone(data)}}}};}''',data)
  fc=fake.locator('sunday-notification-card');toggle=fc.get_by_role('checkbox',name='Fridge / freezer door opened enabled');await toggle.wait_for();await toggle.uncheck();await fake.wait_for_function('window.saved.length===1 && !document.querySelector("sunday-notification-card").busy');assert await fake.evaluate('window.saved[0].action==="toggle" && window.saved[0].enabled===false')
  await fc.get_by_role('button',name='New notification',exact=True).click();await fc.locator('#rule-name').fill('Room light off');await fc.locator('#entity-search').fill('fan.roka_s_room_roka_fan');await fc.get_by_role('button',name='Add',exact=True).click();await fc.locator('#rule-value').fill('off');await fc.locator('#for-seconds').fill('120');await fc.locator('#cooldown').fill('300');await fc.get_by_role('button',name='Save notification').click();await fake.wait_for_function('window.saved.length===2 && !document.querySelector("sunday-notification-card").busy');assert await fake.evaluate('window.saved[1].rule.entity_ids[0]==="fan.roka_s_room_roka_fan" && window.saved[1].rule.for_seconds===120 && window.saved[1].rule.cooldown_seconds===300')
  print('Mobile/desktop notification editor, existing fridge checks, unified view cards, and isolated toggle/create saves: passed. No live notification changes from browser checks.');await browser.close()
asyncio.run(main())
