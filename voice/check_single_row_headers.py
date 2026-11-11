import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright
EXPR='''() => { const found=[];function walk(root){for(const e of root.querySelectorAll('*')){if(e.tagName==='MUSHROOM-CHIPS-CARD'){const c=e._config||e.config;if(c?.chips?.some(x=>x.icon==='mdi:lightbulb-group-outline'&&x.tap_action?.action==='none')){const group=e.shadowRoot?.querySelector('.chip-container');if(group){const r=group.getBoundingClientRect();const chips=[...group.children].map(x=>{const b=x.getBoundingClientRect();return {x:b.x,top:b.top,right:b.right,w:b.width}});found.push({chips,left:r.left,right:r.right,nowrap:getComputedStyle(group).flexWrap});}}}if(e.shadowRoot)walk(e.shadowRoot);}}walk(document);return found;}'''
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with async_playwright() as p:
  b=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  c=await b.new_context(viewport={'width':360,'height':900})
  await c.add_init_script("if(location.origin==='http://127.0.0.1:8123')localStorage.setItem('hassTokens',JSON.stringify({hassUrl:location.origin,access_token:"+json.dumps(token)+",token_type:'Bearer',expires:Date.now()+3600000,expires_in:3600,clientId:location.origin+'/'}));")
  page=await c.new_page();results=[]
  for dash,views in [('roka',['sunday','hall','dining','kitchen']),('rani',['rani','hall','kitchen','dining']),('sian',['sian','hall','dining','kitchen'])]:
   for view in views:
    await page.goto(f'http://127.0.0.1:8123/dashboard-{dash}/{view}',wait_until='domcontentloaded')
    await page.wait_for_timeout(6000 if not results else 2000)
    data=await page.evaluate(EXPR)
    if not data:
     await page.wait_for_timeout(4000);data=await page.evaluate(EXPR)
    assert len(data)==1,(dash,view,data)
    chips=data[0]['chips'];tops=[x['top'] for x in chips if x['w']>0]
    assert max(tops)-min(tops)<2,(dash,view,data)
    assert min(x['x'] for x in chips)>=-1 and max(x['right'] for x in chips)<=361,(dash,view,data)
    results.append((dash,view));print('Single row at 360px:',dash,view,flush=True)
    if dash=='roka' and view=='sunday':await page.screenshot(path='/tmp/sunday-single-row.png',full_page=True)
  await b.close()
  print('Passed all',len(results),'views; no wrapped chips or horizontal overflow.')
asyncio.run(main())
