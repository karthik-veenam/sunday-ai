import asyncio,json,sys
from pathlib import Path
from playwright.async_api import async_playwright
sys.path.insert(0,'/home/djpi/sunday')
from central_ac_inching import HA
R=Path(__file__).parent
async def main():
 async with HA() as h:token=h.token
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--disable-gpu','--disable-background-networking'],timeout=20000)
  context=await browser.new_context(viewport={'width':430,'height':1100})
  await context.add_init_script("if(location.origin==='http://127.0.0.1:8123')localStorage.setItem('hassTokens',JSON.stringify({hassUrl:location.origin,access_token:"+json.dumps(token)+",token_type:'Bearer',expires:Date.now()+3600000,expires_in:3600,clientId:location.origin+'/'}));")
  page=await context.new_page();page.set_default_timeout(15000)
  await page.goto('http://127.0.0.1:8123/sunday-feelings/feelings',wait_until='domcontentloaded',timeout=30000)
  card=page.locator('sunday-feelings-card');await card.locator('.meter').nth(3).wait_for(timeout=45000)
  await page.wait_for_function("()=>{const root=document.querySelector('home-assistant');function find(r){for(const e of r.querySelectorAll('*')){if(e.tagName==='SUNDAY-FEELINGS-CARD')return e;if(e.shadowRoot){const found=find(e.shadowRoot);if(found)return found}}}const c=find(root.shadowRoot);return c?._data&&!c._loading}",timeout=20000)
  assert await card.locator('h1').inner_text()=='Sunday Feelings'
  assert await card.locator('.mood').inner_text()!='Loading…'
  assert await card.locator('.error').count()==0
  dimensions=await card.evaluate('(c)=>({width:c.clientWidth,scroll:c.shadowRoot.querySelector(".page").scrollWidth,enabled:c._data.enabled,mood:c._data.snapshot?.mood,meters:c.shadowRoot.querySelectorAll(".meter").length})')
  assert dimensions['enabled'] and dimensions['meters']==4
  assert dimensions['scroll']<=dimensions['width']+1,dimensions
  await page.screenshot(path=str(R/'live-feelings-mobile.png'),full_page=True)
  await card.get_by_role('button',name='7 days').click();assert await card.evaluate('c=>c._range')=='168h'
  await card.get_by_role('button',name='Refresh feelings').click()
  await page.wait_for_timeout(500)
  assert await card.locator('.error').count()==0
  await page.set_viewport_size({'width':1280,'height':1100})
  await page.screenshot(path=str(R/'live-feelings-desktop.png'),full_page=True)
  (R/'live-ui-result.json').write_text(json.dumps({'page':'/sunday-feelings/feelings','real_ha_authenticated':True,'mobile':dimensions,'range_switch':True,'manual_refresh':True,'screenshots':['live-feelings-mobile.png','live-feelings-desktop.png']},indent=2)+'\n')
  await browser.close()
 print('PASS: authenticated live HA dashboard, cached engine data, 4 gauges, mobile fit, range toggle and refresh.')
asyncio.run(main())
