import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright
ROOT=Path('/home/djpi/sunday2-rebuild')
async def main():
 token=json.loads(Path('/home/djpi/sunday/config.json').read_text())['ha_token']
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);ctx=await browser.new_context(viewport={'width':400,'height':1000},is_mobile=True,has_touch=True)
  await ctx.add_init_script("if(location.origin==='http://127.0.0.1:8123')localStorage.setItem('hassTokens',JSON.stringify({hassUrl:location.origin,access_token:"+json.dumps(token)+",token_type:'Bearer',expires:Date.now()+3600000,expires_in:3600,clientId:location.origin+'/'}));")
  page=await ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));await page.goto('http://127.0.0.1:8123/device-automations/notifications',wait_until='domcontentloaded')
  card=page.locator('sunday-notification-card');await card.locator('.rule').first.wait_for(timeout=40000);await card.get_by_role('button',name='New notification',exact=True).click();search=card.locator('#entity-search');await search.fill('Fan');await card.locator('.entity-option').first.wait_for();rows=await card.locator('.entity-option').all_text_contents();assert any('fan.roka_s_room_roka_fan' in x for x in rows),rows;print('Fan search results:',rows)
  await search.scroll_into_view_if_needed();await page.screenshot(path=str(ROOT/'state_notifications/fan-search-mobile.png'),full_page=True)
  result=card.locator('.entity-option').filter(has_text='fan.roka_s_room_roka_fan');await result.click();assert await card.locator('.chip').count()==1;assert await search.input_value()=='';assert await card.locator('#rule-name').input_value();assert await card.locator('.entity-option').count()==0
  await search.fill('definitely-not-a-real-device');assert await card.locator('.search-note').inner_text()=='No matching unselected devices. Try another name or room.'
  await card.get_by_role('button',name='Cancel',exact=True).click();assert not errors,errors;print('Partial/case-insensitive fan search, visible mobile results, tap-to-select, auto name and no-match feedback passed. No rule saved.');await browser.close()
asyncio.run(main())
