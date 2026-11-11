import asyncio,sys,aiohttp
sys.path.insert(0,'/config')
from custom_components import firetv_lan as m
class Reply:
 def __init__(self,status=200,text='[]',data=None):self.status=status;self.body=text;self.data=data
 async def __aenter__(self):return self
 async def __aexit__(self,*args):return False
 async def text(self):return self.body
 async def json(self,**kwargs):return self.data
class Session:
 def __init__(self):self.running=False;self.online=True;self.auth=True;self.calls=[];self.delivered=0;self.wakes=0
 def request(self,method,url,**kwargs):
  self.calls.append((method,url))
  if ':8009/' in url:
   if not self.online:raise aiohttp.ClientConnectionError('offline')
   if method=='POST':self.wakes+=1;self.running=True;return Reply(201)
   return Reply(text='<name>FireTVRemote</name><state>stopped</state>')
  if not self.running:raise m.RemoteServiceStopped('stopped')
  if not self.auth:return Reply(403)
  if method=='POST':self.delivered+=1
  return Reply(data=[])
async def main():
 session=Session();m.async_get_clientsession=lambda *a,**k:session
 c=m.Client(None,{'host':'192.0.2.1','token':'test-only'})
 await c.refresh();assert c.available and session.wakes==0 and session.delivered==0
 await c.send('right');assert c.available and session.wakes==1 and session.delivered==1
 session.running=False;session.online=False;c._last_refresh=0
 await c.refresh();assert not c.available
 session.online=True;c._last_refresh=0
 await c.refresh();assert c.available and session.wakes==1
 session.running=True;session.auth=False;c._last_refresh=0
 await c.refresh();assert not c.available and c._auth_failed
 # Do not replay an ambiguously delivered command after a timeout / generic failure.
 count=0
 async def fail(*a,**kw):
  nonlocal count
  count+=1;raise m.HomeAssistantError('timeout after sending')
 c.request=fail
 try:await c.send('play_pause')
 except m.HomeAssistantError:pass
 else:raise AssertionError('Failure swallowed')
 assert count==1 and session.wakes==1
 print('PASS: idle service stays available; poll does not wake; first button starts service and delivers once; offline recovery, expired pairing, and no replay on ambiguous failures.')
asyncio.run(main())
