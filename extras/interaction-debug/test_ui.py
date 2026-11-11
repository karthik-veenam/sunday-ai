import asyncio,sys,json
from pathlib import Path
from playwright.async_api import async_playwright
sys.path.insert(0,'/home/djpi/sunday')
from central_ac_inching import HA
ROOT=Path(__file__).parent
async def main():
 async with HA() as h:token=h.token
 async with async_playwright() as p:
  b=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);ctx=await b.new_context(viewport={'width':430,'height':950});await ctx.add_init_script("if(location.origin==='http://127.0.0.1:8123')localStorage.setItem('hassTokens',JSON.stringify({hassUrl:location.origin,access_token:"+json.dumps(token)+",token_type:'Bearer',expires:Date.now()+3600000,expires_in:3600,clientId:location.origin+'/'}));");page=await ctx.new_page()
  await page.goto('http://127.0.0.1:8123/sunday-inputs/debug',wait_until='domcontentloaded');card=page.locator('sunday-interaction-debug-card');await card.locator('.record').nth(2).wait_for(timeout=45000);assert await card.locator('.record').count()==3
  await card.get_by_role('button',name='Refresh').click();await card.get_by_role('button',name='Refresh').wait_for();await page.wait_for_function("()=>{function find(r){for(const e of r.querySelectorAll('*')){if(e.tagName==='SUNDAY-INTERACTION-DEBUG-CARD')return e;if(e.shadowRoot){const x=find(e.shadowRoot);if(x)return x}}}return !find(document)?.busy}")
  dims=await card.evaluate('(e)=>({w:e.clientWidth,scroll:e.shadowRoot.querySelector(".wrap").scrollWidth})');assert dims['scroll']<=dims['w']+1,dims
  await page.screenshot(path=str(ROOT/'debug-mobile.png'),full_page=True)
  # Isolated frontend verifies repeated HA state pushes do not cause polling.
  test=await ctx.new_page();await test.set_content('<sunday-interaction-debug-card></sunday-interaction-debug-card>');await test.add_script_tag(content=(ROOT/'card.js').read_text());data=json.loads((ROOT/'live-sample.json').read_text())
  await test.evaluate('''data=>{window.calls=0;const c=document.querySelector('sunday-interaction-debug-card');c.setConfig({});window.h={callWS:async()=>{window.calls++;return {response:{status:200,content:data}}}};c.hass=window.h;for(let i=0;i<10;i++)c.hass=window.h;}''',data)
  await test.wait_for_timeout(500);assert await test.evaluate('window.calls')==1
  await test.locator('sunday-interaction-debug-card').get_by_role('button',name='Refresh').click();await test.wait_for_timeout(100);assert await test.evaluate('window.calls')==2
  await b.close();print('Live last-three cards, mobile layout and Refresh passed. Repeated HA state pushes cause no extra reads; no polling timer.')
if __name__=='__main__':asyncio.run(main())
