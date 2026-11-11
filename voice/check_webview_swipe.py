import asyncio,json
from playwright.async_api import async_playwright
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with async_playwright() as p:
  b=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  ctx=await b.new_context(viewport={'width':400,'height':850},is_mobile=True,has_touch=True,user_agent='Mozilla/5.0 (Linux; Android 14; Pixel 7 Build/AP1A; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/130.0.0.0 Mobile Safari/537.36')
  await ctx.add_init_script("if(location.origin==='http://127.0.0.1:8123')localStorage.setItem('hassTokens',JSON.stringify({hassUrl:location.origin,access_token:"+json.dumps(token)+",token_type:'Bearer',expires:Date.now()+3600000,expires_in:3600,clientId:location.origin+'/'}));")
  page=await ctx.new_page()
  async def swipe(dx,dy=0):
   await page.evaluate('''([dx,dy])=>{let target;function walk(r){for(const e of r.querySelectorAll('*')){if(e.tagName==='HUI-VIEW')target=e;if(!target&&e.shadowRoot)walk(e.shadowRoot);if(target)return;}}walk(document);if(!target)throw Error('View not ready');const touch=(x,y)=>new Touch({identifier:1,target,clientX:x,clientY:y,screenX:x,screenY:y});const fire=(type,touches)=>target.dispatchEvent(new TouchEvent(type,{touches,targetTouches:touches,changedTouches:touches,bubbles:true,composed:true,cancelable:true}));fire('touchstart',[touch(200,200)]);fire('touchmove',[touch(200+dx,200+dy)]);fire('touchend',[]);}''',[dx,dy])
   await page.wait_for_timeout(500)
  for dash,first in [('roka','sunday'),('rani','rani'),('sian','sian')]:
   await page.goto(f'http://127.0.0.1:8123/dashboard-{dash}/{first}',wait_until='domcontentloaded')
   await page.wait_for_timeout(7500)
   await swipe(-130)
   assert page.url.endswith('/hall'),page.url
   await swipe(130)
   assert page.url.endswith('/'+first),page.url
   await swipe(5,150)
   assert page.url.endswith('/'+first),page.url
   print('Passed touch swipes:',dash,'left/right switch views; vertical stays put.',flush=True)
  await b.close()
asyncio.run(main())
