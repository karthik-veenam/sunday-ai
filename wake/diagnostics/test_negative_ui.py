import asyncio,sys,json,base64,io,wave
from pathlib import Path
sys.path.insert(0,'/home/djpi/sunday')
from central_ac_inching import HA
from playwright.async_api import async_playwright
OUT=Path('/home/djpi/sunday/deployment/negative-dataset-20261027')
async def main():
 async with HA() as h:token=h.token
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);ctx=await browser.new_context(viewport={'width':430,'height':950})
  await ctx.add_init_script("if(location.origin==='http://127.0.0.1:8123')localStorage.setItem('hassTokens',JSON.stringify({hassUrl:location.origin,access_token:"+json.dumps(token)+",token_type:'Bearer',expires:Date.now()+3600000,expires_in:3600,clientId:location.origin+'/'}));")
  page=await ctx.new_page();await page.goto('http://127.0.0.1:8123/sunday-inputs/wake-negatives');card=page.locator('sunday-negative-wake-card');await card.locator('.summary').filter(has_text='saved clips').wait_for(timeout=45000)
  assert await card.get_by_role('combobox',name='Review filter').count()==1
  dims=await card.evaluate('(e)=>({width:e.clientWidth,scroll:e.shadowRoot.querySelector("ha-card").scrollWidth})');assert dims['scroll']<=dims['width']+1,dims
  await card.screenshot(path=str(OUT/'live-empty-mobile.png'))
  # Browser-only synthetic fixtures: never write a fake clip into the real corpus.
  buf=io.BytesIO()
  with wave.open(buf,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\x00\x00'*1600)
  audio=base64.b64encode(buf.getvalue()).decode()
  await card.evaluate('''(el,audio)=>{el.testCalls=[];let exists=true;let review='unreviewed';el.h={callWS:async req=>{const d=req.service_data;el.testCalls.push(d);let b={};if(d.action==='list')b={clips:exists?[{id:'a'.repeat(32),captured:'2026-10-27T05:00:00+05:30',transcript:'Synthetic UI test only',duration:.1,review}]:[],total:exists?1:0,dataset_count:exists?1:0,audio_bytes:3200};if(d.action==='audio')b={audio_base64:audio};if(d.action==='review'){review=d.review;b={review}}if(d.action==='delete'){exists=false;b={deleted:true}}return {response:{status:200,content:b}}}};return el.load()}''',audio)
  await card.get_by_role('button',name='▶ Play').click();await card.locator('audio').wait_for();assert (await card.locator('audio').get_attribute('src')).startswith('blob:')
  await card.locator('.clip select').select_option('wake_word_present');await card.get_by_text('Review saved.',exact=True).wait_for()
  await card.screenshot(path=str(OUT/'synthetic-review-mobile.png'))
  page.once('dialog',lambda d:d.accept());await card.get_by_role('button',name='Delete',exact=True).click();await card.get_by_text('Clip deleted.',exact=True).wait_for();assert await card.locator('.clip').count()==0
  calls=await card.evaluate('(el)=>el.testCalls.map(x=>x.action)');assert all(a in calls for a in ['list','audio','review','delete'])
  await browser.close();print('Live mobile view and browser-only playback/review/delete UI passed. No real corpus clips modified.')
asyncio.run(main())
