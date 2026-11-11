import sys,asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright
sys.path.insert(0,'/home/djpi/sunday')
from central_ac_inching import HA
ROOT=Path('/home/djpi/sunday-verifier-notifications')
async def main():
 async with HA() as h:
  cfg=await h.call('lovelace/config',url_path='sunday-inputs');view=next((v.get('path',str(i)) for i,v in enumerate(cfg['views']) if 'custom:sunday-wake-alias-card' in json.dumps(v)));token=h.token
 async with async_playwright() as p:
  b=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);ctx=await b.new_context(viewport={'width':430,'height':950});await ctx.add_init_script("if(location.origin==='http://127.0.0.1:8123')localStorage.setItem('hassTokens',JSON.stringify({hassUrl:location.origin,access_token:"+json.dumps(token)+",token_type:'Bearer',expires:Date.now()+3600000,expires_in:3600,clientId:location.origin+'/'}));");page=await ctx.new_page()
  await page.goto('http://127.0.0.1:8123/sunday-inputs/'+view,wait_until='domcontentloaded');card=page.locator('sunday-wake-alias-card');await card.locator('.tag').first.wait_for(timeout=45000)
  current=json.loads(Path('/home/djpi/sunday/wake_verifier.json').read_text())['aliases'];assert await card.locator('.tag').all_text_contents()==current
  await card.get_by_role('textbox',name='New wake alias').fill('SUNDAY!');await card.get_by_role('button',name='Add alias').click();await card.get_by_text('Already in the alias list.',exact=True).wait_for(timeout=20000)
  assert await card.get_by_role('spinbutton',name='Whisper audio context').input_value()=='128'
  await card.get_by_role('button',name='Save context').click();await card.get_by_text('Audio context is already set to 128.',exact=True).wait_for(timeout=15000)
  dims=await card.evaluate('(e)=>({w:e.clientWidth,scroll:e.shadowRoot.querySelector("ha-card").scrollWidth})');assert dims['scroll']<=dims['w']+1,dims
  await card.screenshot(path=str(ROOT/'context-dashboard-mobile.png'));await b.close();print('Live context value, no-change save and mobile layout passed.')
if __name__=='__main__':asyncio.run(main())
