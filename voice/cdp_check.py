import asyncio,json,base64
from pathlib import Path
import aiohttp
async def main():
 async with aiohttp.ClientSession() as s:
  targets=await (await s.get('http://127.0.0.1:9223/json/list')).json()
  target=next(t for t in targets if t['type']=='page')
  async with s.ws_connect(target['webSocketDebuggerUrl']) as w:
   i=0
   async def call(method,**params):
    nonlocal i
    i+=1;await w.send_json({'id':i,'method':method,'params':params})
    while True:
     r=await w.receive_json()
     if r.get('id')==i:return r.get('result',r)
   await call('Emulation.setDeviceMetricsOverride',width=400,height=1000,deviceScaleFactor=1,mobile=False)
   await call('Page.navigate',url='http://127.0.0.1:8123')
   await asyncio.sleep(2)
   token=json.loads(Path('/home/djpi/sunday/config.json').read_text())['ha_token']
   value=json.dumps({'hassUrl':'http://127.0.0.1:8123','access_token':token,'token_type':'Bearer','expires':9999999999999,'expires_in':3600,'clientId':'http://127.0.0.1:8123/'})
   await call('Runtime.evaluate',expression='localStorage.setItem("hassTokens",'+json.dumps(value)+')')
   await call('Page.navigate',url='http://127.0.0.1:8123/dashboard-roka/sunday')
   await asyncio.sleep(7)
   expr="""(()=>{let out=[];function walk(r){for(let e of r.querySelectorAll('*')){if(['HUI-GRID-CARD','HUI-THERMOSTAT-CARD','HUI-TILE-CARD'].includes(e.tagName)){let b=e.getBoundingClientRect();out.push({tag:e.tagName,entity:e._config?.entity||e.config?.entity,x:b.x,y:b.y,w:b.width,h:b.height,bottom:b.bottom,html:e.shadowRoot?.innerHTML.slice(-7000)});}if(e.shadowRoot)walk(e.shadowRoot);}}walk(document);return out})()"""
   r=await call('Runtime.evaluate',expression=expr,returnByValue=True)
   Path('/tmp/ha-grid-dom.json').write_text(json.dumps(r,indent=2))
   shot=await call('Page.captureScreenshot',format='png');Path('/tmp/ha-grid-browser.png').write_bytes(base64.b64decode(shot['data']))
   print('Browser inspection saved',len(r.get('result',{}).get('value',[])))
asyncio.run(main())
