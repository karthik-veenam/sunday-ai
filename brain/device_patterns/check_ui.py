import asyncio,json,sqlite3
from pathlib import Path
from playwright.async_api import async_playwright
ROOT=Path('/home/djpi/sunday2-rebuild');DEST=Path(Path('/tmp/sunday-patterns-dir').read_text())
async def main():
 ha=json.loads(Path('/home/djpi/sunday/config.json').read_text())['ha_token'];errors=[]
 db=sqlite3.connect('file:'+str(ROOT/'state/costs.db')+'?mode=ro',uri=True);before=db.execute("select count(*) from calls where subcategory='device_patterns'").fetchone()[0]
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);ctx=await browser.new_context(viewport={'width':400,'height':950})
  await ctx.add_init_script("if(location.origin==='http://127.0.0.1:8123')localStorage.setItem('hassTokens',JSON.stringify({hassUrl:location.origin,access_token:"+json.dumps(ha)+",token_type:'Bearer',expires:Date.now()+3600000,expires_in:3600,clientId:location.origin+'/'}));")
  page=await ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
  async def visit(view):
   await page.goto('http://127.0.0.1:8123/device-patterns/'+view,wait_until='domcontentloaded');card=page.locator('sunday-device-pattern-card');await card.locator('h1').wait_for(timeout=45000)
   await page.wait_for_function("()=>{function find(r){for(const e of r.querySelectorAll('*')){if(e.tagName==='SUNDAY-DEVICE-PATTERN-CARD')return e;if(e.shadowRoot){const x=find(e.shadowRoot);if(x)return x;}}}return !!find(document)?._data?.enabled}",timeout=45000)
   return card
  card=await visit('live');await card.locator('.device').first.wait_for();size=await card.evaluate('e=>({w:e.shadowRoot.querySelector(".wrap").clientWidth,s:e.shadowRoot.querySelector(".wrap").scrollWidth})');assert size['s']<=size['w']+1,size
  assert await card.locator('.device').count()>=10
  await page.screenshot(path=str(DEST/'live-mobile.png'),full_page=True)
  await card.get_by_role('button',name='Run pattern analysis').click()
  await page.wait_for_function("()=>{function find(r){for(const e of r.querySelectorAll('*')){if(e.tagName==='SUNDAY-DEVICE-PATTERN-CARD')return e;if(e.shadowRoot){const x=find(e.shadowRoot);if(x)return x;}}}return find(document)?._data?.job?.status==='COMPLETED'}",timeout=45000)
  assert 'Last analysis' in await card.locator('.status').inner_text()
  card=await visit('patterns');await card.locator('.pattern').first.wait_for();assert await card.locator('.bars').count()>0
  assert 'Tentative' in await card.locator('.pattern').first.inner_text();await page.screenshot(path=str(DEST/'patterns-mobile.png'),full_page=True)
  card=await visit('history');await card.locator('.tree').first.wait_for();await card.locator('.tree>summary').first.click();await card.locator('.tree .tree>summary').first.click();await card.locator('.tree .tree .tree>summary').first.click();await card.locator('.line').first.wait_for(timeout=20000)
  await page.screenshot(path=str(DEST/'history-mobile.png'),full_page=True)
  await page.set_viewport_size({'width':1250,'height':1000});await page.screenshot(path=str(DEST/'history-desktop.png'),full_page=True)
  assert not errors,errors
  await browser.close()
 after=db.execute("select count(*) from calls where subcategory='device_patterns'").fetchone()[0];assert before==after,'Cold-start analysis made a model call';db.close()
 print('Passed: live HA mobile layout, selected inputs, manual analysis, tentative charts, month/week/day expansion, day timeline, desktop render, no JS errors, no warmup model calls.')
asyncio.run(main())
