import asyncio,copy,datetime,json
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi'); STAMP=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
class HA:
 async def __aenter__(self):
  self.s=aiohttp.ClientSession();self.token=json.load(open(ROOT/'sunday/config.json'))['ha_token'];self.w=await self.s.ws_connect('ws://127.0.0.1:8123/api/websocket');await self.w.receive_json();await self.w.send_json({'type':'auth','access_token':self.token});assert (await self.w.receive_json())['type']=='auth_ok';self.i=0;return self
 async def __aexit__(self,*a):await self.w.close();await self.s.close()
 async def call(self,t,**kw):
  self.i+=1;await self.w.send_json(dict(id=self.i,type=t,**kw))
  while True:
   r=await self.w.receive_json()
   if r.get('id')==self.i:
    assert r.get('success'),r.get('error');return r.get('result')
 async def service(self,d,a,data):
  async with self.s.post(f'http://127.0.0.1:8123/api/services/{d}/{a}',headers={'Authorization':'Bearer '+self.token},json=data) as r:assert r.status==200,(r.status,await r.text())
async def helpers():
 async with HA() as h:
  specs=[('input_boolean','central_ac_inching',{'name':'Central AC Inching','icon':'mdi:timer-outline'}),('input_number','central_ac_inching_minutes',{'name':'Central AC Inching Minutes','min':1,'max':1439,'step':1,'mode':'box','unit_of_measurement':'min','icon':'mdi:timer-cog-outline'}),('input_boolean','central_ac_inching_armed',{'name':'Central AC Inching Armed','icon':'mdi:timer-sand'}),('input_datetime','central_ac_inching_deadline',{'name':'Central AC Inching Deadline','has_date':True,'has_time':True,'icon':'mdi:clock-end'})]
  for domain,slug,fields in specs:
   items=await h.call(domain+'/list')
   if not any(x['id']==slug for x in items):
    result=await h.call(domain+'/create',**fields);assert result['id']==slug,result
    if domain=='input_number':await h.service(domain,'set_value',{'entity_id':domain+'.'+slug,'value':60})
    if domain=='input_datetime':await h.service(domain,'set_datetime',{'entity_id':domain+'.'+slug,'datetime':'2000-01-01 00:00:00'})
  print('Four persistent inching helpers ready; default disabled, 60 minutes.')
if __name__=='__main__':asyncio.run(helpers())
