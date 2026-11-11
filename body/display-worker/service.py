"""Separate, read-only display worker. Latest-value projection; no audio/actions/models."""
import asyncio,json,sqlite3,time
from pathlib import Path
import aiohttp
from aiohttp import web
from display_projection import Projection,validate_packet
ROOT=Path('/home/djpi/sunday2-rebuild');VOICE=Path('/home/djpi/sunday/config.json')
class Events(asyncio.DatagramProtocol):
 def __init__(self,model):self.model=model
 def datagram_received(self,data,addr):
  if len(data)>2048:return
  try:
   e=json.loads(data)
   if isinstance(e,dict) and isinstance(e.get('text',''),str):self.model.voice(e)
  except (ValueError,TypeError):pass
class Worker:
 def __init__(self):self.p=Projection();self.started=time.time();self.action_versions={};self.last_jobs={};self.delivered=0;self.failed=0;self.gateway_ok=False;self.captions_enabled=False
 async def ha(self,session,config):
  while True:
   self.p.ha_ready=False
   try:
    async with session.ws_connect(config.get('ha_url','http://127.0.0.1:8123').rstrip('/')+'/api/websocket',heartbeat=20) as ws:
     await ws.receive_json();await ws.send_json({'type':'auth','access_token':config['ha_token']});assert (await ws.receive_json())['type']=='auth_ok'
     seq=0
     async def command(typ):
      nonlocal seq;seq+=1;await ws.send_json({'id':seq,'type':typ})
      while True:
       response=await ws.receive_json()
       if response.get('id')==seq:
        if not response.get('success'):raise RuntimeError('HA read unavailable')
        return response.get('result')
     devices={x['id']:x for x in await command('config/device_registry/list')};registry=await command('config/entity_registry/list');self.p.registry={x['entity_id']:x for x in registry}
     household=json.loads((ROOT/'household.json').read_text());primary=household['primary_area'];members=set()
     for r in registry:
      device=devices.get(r.get('device_id'),{});area=r.get('area_id') or device.get('area_id') or household.get('device_area_names',{}).get(device.get('name_by_user') or device.get('name')) or household.get('entity_area_overrides',{}).get(r['entity_id'])
      if area==primary and not r.get('disabled_by') and not r.get('hidden_by'):members.add(r['entity_id'])
     binding=json.loads((Path(__file__).parent/'display_config.json').read_text()).get('room_target_entity')
     if isinstance(binding,str) and binding.startswith("input_number."):members.add(binding)
     self.p.members=members
     self.p.states={e['entity_id']:e for e in await command('get_states')};self.p.ha_ready=True
     await ws.send_json({'id':seq+1,'type':'subscribe_events','event_type':'state_changed'})
     await ws.send_json({'id':seq+2,'type':'persistent_notification/subscribe'})
     async for msg in ws:
      if msg.type!=aiohttp.WSMsgType.TEXT:break
      event=msg.json().get('event',{})
      if 'notifications' in event:
       notifications=event['notifications'];values=list(notifications.values()) if isinstance(notifications,dict) else notifications
       if values:
        notice=values[-1]
        if event.get('type') in ('added','updated'):self.p.notify(notice.get('title','Notice'),notice.get('message',''))
       continue
      d=event.get('data',{})
      if d.get('entity_id'):self.p.changed(d['entity_id'],d.get('old_state'),d.get('new_state'))
   except asyncio.CancelledError:raise
   except Exception:pass
   finally:self.p.ha_ready=False
   await asyncio.sleep(3)
 def read_ledgers(self):
  actions=[];jobs=[];recent=[]
  def db(name):
   c=sqlite3.connect('file:'+str(ROOT/'state'/name)+'?mode=ro',uri=True,timeout=.025);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');return c
  try:
   c=db('world.db')
   try:actions=[(r['observed'],r['id'],json.loads(r['data'])) for r in c.execute('select id,observed,data from actions where observed>=? order by observed,id limit 100',(max(self.started,time.time()-120),))]
   finally:c.close()
  except (OSError,ValueError,sqlite3.Error):pass
  try:
   c=db('jobs.db')
   try:recent=[dict(r) for r in c.execute("select id,due,kind,status from jobs where (status='pending' or due>?) and kind in ('timer','alarm') order by due limit 100",(time.time()-120,))];jobs=[r for r in recent if r['status']=='pending']
   finally:c.close()
  except sqlite3.Error:pass
  return actions,jobs,recent
 async def poll(self,session):
  while True:
   try:self.captions_enabled=json.loads((Path(__file__).parent/'display_config.json').read_text()).get('captions_enabled',False) is True
   except (OSError,ValueError):pass
   actions,jobs,recent=await asyncio.to_thread(self.read_ledgers)
   for observed,ident,data in actions:
    version=json.dumps(data,sort_keys=True)
    if self.action_versions.get(ident)!=version and self.p.ha_ready:self.p.action(data,observed)
    self.action_versions[ident]=version
   ids={ident for _,ident,_ in actions};self.action_versions={k:v for k,v in self.action_versions.items() if k in ids}
   for job in recent:
    if self.last_jobs.get(job['id'])=='pending' and job['status']=='delivered':self.p.notify('Timer finished' if job['kind']=='timer' else 'Alarm','Your '+job['kind']+' is done')
   self.last_jobs={r['id']:r['status'] for r in recent};self.p.jobs=jobs
   try:
    async with session.get('http://127.0.0.1:8773/health',timeout=aiohttp.ClientTimeout(total=1)) as r:
     h=await r.json();self.p.sleep=h.get('sleep',{})
   except Exception:self.p.sleep={}
   await asyncio.sleep(2)
 def display_packet(self):
  from display_projection import clean
  packet=self.p.packet();packet['line2']=clean(self.p.transcript,95)
  packet.update(title='',line1='',ttl_ms=0)
  if self.captions_enabled and self.p.caption and self.p.clock()<self.p.caption_until:
   text=clean(self.p.caption,512)
   if len(text)>127:
    text=text[-127:];space=text.find(' ')
    if 0<=space<40:text=text[space+1:]
   packet.update(line1=text,ttl_ms=min(30000,int((self.p.caption_until-self.p.clock())*1000)))
  return validate_packet(packet)
 async def output(self,session):
  last=None;sent=0
  while True:
   packet=self.display_packet();now=time.monotonic()
   if packet!=last or now-sent>=3:
    try:
     async with session.post('http://127.0.0.1:8773/display',json=packet,timeout=aiohttp.ClientTimeout(total=.5)) as r:
      self.gateway_ok=r.status==200
      if self.gateway_ok:last=packet;sent=now;self.delivered+=1
      else:self.failed+=1
    except Exception:self.gateway_ok=False;self.failed+=1
   await asyncio.sleep(.25 if self.gateway_ok else 2)
 async def run(self):
  config=json.loads(VOICE.read_text());transport,_=await asyncio.get_running_loop().create_datagram_endpoint(lambda:Events(self.p),local_addr=('127.0.0.1',8777))
  app=web.Application();app.router.add_get('/health',lambda r:web.json_response({'ha_connected':self.p.ha_ready,'body_transport_ok':self.gateway_ok,'automatic_cards_enabled':False,'caption_strip_enabled':self.captions_enabled,'packets_sent':self.delivered,'failures':self.failed}));app.router.add_get('/snapshot',lambda r:web.json_response(self.display_packet()));runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,'127.0.0.1',8776).start()
  try:
   async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:await asyncio.gather(self.ha(session,config),self.poll(session),self.output(session))
  finally:transport.close();await runner.cleanup()
if __name__=='__main__':asyncio.run(Worker().run())
