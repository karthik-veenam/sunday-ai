import asyncio,sys
sys.path.insert(0,'/config')
from custom_components import firetv_lan as m
class Response:
 def __init__(self,status=200,value=None):self.status=status;self.value=value
 async def __aenter__(self):return self
 async def __aexit__(self,*args):return False
 async def text(self):return '{}' if self.value is None else '[]'
 async def json(self,**kwargs):return self.value
class Session:
 def __init__(self):self.calls=[];self.status=200
 def request(self,method,url,**kwargs):
  self.calls.append((method,url,kwargs))
  return Response(self.status,[{'name':'YouTube','appId':'com.amazon.firetv.youtube'}] if method=='GET' else {'description':'OK'})
async def main():
 s=Session();m.async_get_clientsession=lambda *a,**kw:s
 c=m.Client(None,{'host':'192.0.2.1','token':'test-only'})
 await c.send('right');assert s.calls[-1][1].endswith('action=dpad_right') and s.calls[-1][2]['json']=={'keyActionType':'keyDownUp'}
 await c.send('play_pause');assert s.calls[-1][1].endswith('/v1/media?action=play')
 await c.send('rewind');assert s.calls[-1][2]['json']['direction']=='back'
 await c.send('fast_forward');assert s.calls[-1][2]['json']['direction']=='forward'
 await c.send('app:in.startv.hotstar');assert s.calls[-1][1].endswith('/app/in.startv.hotstar')
 await c.refresh();assert c.apps['YouTube']=='com.amazon.firetv.youtube'
 count=len(s.calls);await c.refresh();assert len(s.calls)==count
 try:await c.send('not_a_command')
 except m.HomeAssistantError:pass
 else:raise AssertionError('Unknown key accepted')
 s.status=500
 try:await c.send('left')
 except m.HomeAssistantError:pass
 else:raise AssertionError('Failure treated as success')
 s.status=403
 try:await c.send('left')
 except m.HomeAssistantError:pass
 else:raise AssertionError('Invalid pairing accepted')
 assert not c.available
 # Polling recovers availability after the endpoint returns successfully.
 s.status=200;c._last_refresh=0;await c.refresh();assert c.available
 print('PASS: navigation, playback, seek, app launch, poll caching, unknown keys, HTTP/auth failures and recovery. Mocked transport; no TV commands sent.')
asyncio.run(main())
