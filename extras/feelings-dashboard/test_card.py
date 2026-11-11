import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright
R=Path(__file__).parent
async def main():
 data=json.loads((R/'live-status.json').read_text())
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--disable-gpu','--disable-background-networking'],timeout=20000)
  page=await browser.new_page(viewport={'width':430,'height':1100},device_scale_factor=1)
  page.set_default_timeout(12000)
  await page.set_content('<style>body{margin:0;background:#edf3fa;font-family:Arial;--primary-text-color:#243448;--secondary-text-color:#6c7b8d;--ha-card-background:#fff;--divider-color:#e1e8f0;--primary-color:#4481cd}</style><sunday-feelings-card></sunday-feelings-card>')
  await page.add_script_tag(content=(R/'card.js').read_text())
  await page.evaluate('''data=>{window.calls=0;window.hiddenForTest=false;Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.hiddenForTest});window.fixture=data;window.hass={config:{time_zone:'Asia/Kolkata'},callWS:async msg=>{if(msg.domain!=='rest_command'||msg.service!=='sunday_feelings'||!msg.return_response)throw Error('unexpected request');window.calls++;if(window.fail)throw Error('Offline test');return {response:{status:200,content:window.fixture}}}};window.card=document.querySelector('sunday-feelings-card');card.setConfig({auto_refresh:false});card.hass=hass;}''',data)
  await page.wait_for_function('card._data && !card._loading')
  card=page.locator('sunday-feelings-card')
  assert await card.locator('.meter').count()==4
  assert await card.locator('h1').inner_text()=='Sunday Feelings'
  assert await page.evaluate('window.calls')==1
  # A grounded sub-percent change must be visible rather than rounded away.
  await page.evaluate('card._data.snapshot.meters.appreciation=.7245396;card.render()')
  assert '72.5' in await card.locator('.meter').nth(1).inner_text()
  assert '72.5 percent' in await card.locator('.meter').nth(1).locator('.gauge').get_attribute('aria-label')
  await page.evaluate('for(let i=0;i<25;i++)card.hass=hass')
  assert await page.evaluate('window.calls')==1
  assert await card.locator('.badge').inner_text() in ['Engine on','Snapshot is old']
  size=await card.evaluate('(c)=>({width:c.clientWidth,scroll:c.shadowRoot.querySelector(".page").scrollWidth})')
  assert size['scroll']<=size['width']+1,size
  await page.screenshot(path=str(R/'feelings-mobile.png'),full_page=True)
  await card.get_by_role('button',name='7 days').click()
  assert await page.evaluate('card._range')=='168h'
  assert await page.evaluate('window.calls')==1
  await card.get_by_role('button',name='Refresh feelings').click();await page.wait_for_function('!card._loading')
  assert await page.evaluate('window.calls')==2
  await page.set_viewport_size({'width':1280,'height':1100})
  await page.screenshot(path=str(R/'feelings-desktop.png'),full_page=True)
  # Rendering untrusted strings must never create markup.
  await page.evaluate('''()=>{card._data.snapshot.mood='<img src=x onerror="window.attacked=true">';card._data.snapshot.profile.tone='<script>window.attacked=true</script>';card._data.snapshot.recent_causes=[{kind:'affect',actor:'<img src=x onerror="window.attacked=true">',at:Date.now()/1000,inference:true}];card.render();}''')
  assert await card.locator('img').count()==0 and await card.locator('script').count()==0
  assert not await page.evaluate('window.attacked===true')
  # Partial coverage remains visible; a stale fetch retains the last good values.
  await page.evaluate('window.fail=true');await card.get_by_role('button',name='Refresh feelings').click();await page.wait_for_function('!card._loading')
  assert await card.locator('.error').count()==1
  assert await card.locator('.meter').count()==4
  assert await card.locator('.badge').inner_text()=='Showing last update'
  # Missing/disabled data must not become fabricated zero-percent meters.
  await page.evaluate('''()=>{window.fail=false;window.fixture={enabled:false,flags:{},errors:0,dropped:0,queue:0,snapshot:null};}''')
  await card.get_by_role('button',name='Refresh feelings').click();await page.wait_for_function('!card._loading')
  assert await card.locator('.mood').inner_text()=='Paused'
  assert (await card.locator('.score').all_inner_texts())==['—']*4
  # A temporary affect expires in the UI without a request.
  await page.evaluate('''()=>{card._data={enabled:true,flags:{},snapshot:{mood:'comfortable',meters:{},recent_causes:[],affect:{name:'amusement',strength:.4,confidence:.8,expires_at:Date.now()/1000+.25}}};card.render();window.callsBeforeExpiry=window.calls;}''')
  await page.wait_for_timeout(400)
  assert await card.locator('.reaction').inner_text()=='No active reaction'
  assert await page.evaluate('window.calls')==await page.evaluate('window.callsBeforeExpiry')
  # Hidden tabs and disconnected cards issue no read on HA state pushes.
  await page.evaluate('''()=>{window.hiddenForTest=true;document.dispatchEvent(new Event('visibilitychange'));window.callsBeforeHidden=window.calls;for(let i=0;i<10;i++)card.hass=hass;}''')
  assert await page.evaluate('window.calls')==await page.evaluate('window.callsBeforeHidden')
  await page.evaluate('card.remove()')
  assert not await page.evaluate('card._connected')
  await browser.close()
 print('PASS: real-browser mobile/desktop layouts, 4 meters, range selection, refresh, no HA-push polling, XSS escaping, disabled/missing data, stale fallback, affect expiry, hidden/disconnected lifecycle.')
asyncio.run(main())
