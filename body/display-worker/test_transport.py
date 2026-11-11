import ast,asyncio,json,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from display_projection import Projection
from service import Events
class Tests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  tree=ast.parse(Path(__file__).with_name('gateway.py').read_text());node=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='glance_endpoint');self.hub=SimpleNamespace(dev=SimpleNamespace(closed=False,send_json=AsyncMock()));ns={'web':web,'hub':self.hub};exec(compile(ast.Module(body=[node],type_ignores=[]),'endpoint','exec'),ns);app=web.Application();app.router.add_post('/display',ns['glance_endpoint']);self.client=TestClient(TestServer(app));await self.client.start_server()
 async def asyncTearDown(self):await self.client.close()
 async def test_packet_goes_only_to_device_display_message(self):
  p=Projection().packet();r=await self.client.post('/display',json=p);self.assertEqual(r.status,200);self.hub.dev.send_json.assert_awaited_once_with({'type':'glance',**p})
 async def test_bad_payload_not_forwarded(self):
  for p in [{'bogus':'x'},{**Projection().packet(),'line1':'bad"json'},{**Projection().packet(),'timer_seconds':True}]:
   r=await self.client.post('/display',json=p);self.assertEqual(r.status,400)
  self.hub.dev.send_json.assert_not_awaited()
 async def test_disconnected_body_returns_unavailable(self):
  self.hub.dev=None;r=await self.client.post('/display',json=Projection().packet());self.assertEqual(r.status,503)
 async def test_udp_malformed_and_oversize_do_not_change_model(self):
  p=Projection();e=Events(p)
  for data in [b'{',b'[]',b'x'*3000]:e.datagram_received(data,('127.0.0.1',1))
  self.assertIsNone(p.overlay)
 async def test_events_are_display_only(self):
  p=Projection();e=Events(p);e.datagram_received(json.dumps({'type':'wake'}).encode(),None);e.datagram_received(json.dumps({'type':'partial_transcript','text':'AC off'}).encode(),None);self.assertEqual(p.packet()['line1'],'AC off');self.assertEqual(p.states,{})
 async def test_unicode_event_is_bounded_and_valid_json(self):
  import display_event
  with patch.object(display_event,'_sock') as sock:
   display_event.publish('transcript','😀'*220);payload=sock.sendto.call_args.args[0];self.assertLessEqual(len(payload),2048);self.assertEqual(len(json.loads(payload)['text']),220)
 async def test_projection_to_gateway_flow_without_production_sockets(self):
  import service
  client=self.client
  class Response:
   def __init__(self,packet):self.packet=packet
   async def __aenter__(self):self.response=await client.post('/display',json=self.packet);self.status=self.response.status;return self
   async def __aexit__(self,*args):self.response.release()
  class Session:
   def post(self,url,json,timeout):
    if url!='http://127.0.0.1:8773/display':raise AssertionError('Unexpected route')
    return Response(json)
  worker=service.Worker();worker.captions_enabled=True;worker.p.ha_ready=True;worker.p.states['input_boolean.sunday_no_listening']={'state':'off'};events=Events(worker.p);task=asyncio.create_task(worker.output(Session()))
  async def wait_text(text):
   for _ in range(60):
    calls=self.hub.dev.send_json.await_args_list
    if calls and calls[-1].args[0]['line1']==text:return calls[-1].args[0]
    await asyncio.sleep(.05)
   raise AssertionError('No display packet for '+text)
  try:
   await wait_text('');events.datagram_received(json.dumps({'type':'wake'}).encode(),None);self.assertEqual(worker.display_packet()['line1'],'');events.datagram_received(json.dumps({'type':'partial_transcript','text':'Room temperature'}).encode(),None);packet=await wait_text('Room temperature');self.assertEqual(packet['title'],'');self.assertGreater(packet['ttl_ms'],0);self.assertTrue(worker.gateway_ok);self.assertTrue(all(c.args[0]['type']=='glance' for c in self.hub.dev.send_json.await_args_list))
  finally:task.cancel();await asyncio.gather(task,return_exceptions=True)
 async def test_default_keeps_face_but_transmits_manual_transcript(self):
  from service import Worker
  worker=Worker();worker.p.voice({'type':'wake'});worker.p.voice({'type':'partial_transcript','text':'my room temperature'});sent=[]
  class Response:
   status=200
   async def __aenter__(self):return self
   async def __aexit__(self,*args):pass
  class Session:
   def post(self,url,json,timeout):sent.append(json);return Response()
  task=asyncio.create_task(worker.output(Session()))
  try:
   for _ in range(20):
    if sent:break
    await asyncio.sleep(.01)
   self.assertTrue(sent);self.assertEqual(sent[0]['title'],'');self.assertEqual(sent[0]['ttl_ms'],0);self.assertIn('my room temperature',sent[0]['line2'])
  finally:task.cancel();await asyncio.gather(task,return_exceptions=True)
 async def test_strip_contains_newest_words_and_never_status_cards(self):
  from service import Worker
  worker=Worker();worker.captions_enabled=True;now=[1000.];worker.p.clock=lambda:now[0]
  worker.p.voice({'type':'wake'});self.assertEqual(worker.display_packet()['line1'],'')
  text=' '.join('word'+str(i) for i in range(35));worker.p.voice({'type':'partial_transcript','text':text});packet=worker.display_packet();self.assertTrue(packet['line1'].endswith('word34'));self.assertNotIn('word0 ',packet['line1']);self.assertEqual(packet['title'],'');self.assertLessEqual(len(packet['line1']),127)
  now[0]+=31;self.assertEqual(worker.display_packet()['line1'],'')
  worker.p.notify('Notice','hello');self.assertEqual(worker.display_packet()['line1'],'')
 async def test_strip_does_not_replace_unrenderable_words_with_status_labels(self):
  from service import Worker
  worker=Worker();worker.captions_enabled=True;worker.p.voice({'type':'wake'});worker.p.voice({'type':'partial_transcript','text':'ఫ్యాన్ on'});self.assertEqual(worker.display_packet()['line1'],'')
 async def test_no_consumer_does_not_raise_or_wait(self):
  import display_event
  with patch.object(display_event,'_sock') as sock:
   sock.sendto.side_effect=BlockingIOError();display_event.publish('transcript','text');sock.sendto.assert_called_once()
class LedgerTests(unittest.TestCase):
 def test_result_updated_in_same_row_is_still_read(self):
  import tempfile,sqlite3,time
  import service
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'state').mkdir();worker=service.Worker();c=sqlite3.connect(root/'state/world.db');c.execute('create table actions(id text,observed real,data text)');created=time.time();c.execute('insert into actions values(?,?,?)',('a',created,json.dumps({'status':'requested'})));c.commit()
   with patch.object(service,'ROOT',root):
    first=worker.read_ledgers()[0];self.assertEqual(first[0][2]['status'],'requested')
    c.execute('update actions set data=? where id=?',(json.dumps({'status':'service_completed','changed_entities':['light.room']}),'a'));c.commit();second=worker.read_ledgers()[0];self.assertEqual(second[0][0],created);self.assertEqual(second[0][2]['status'],'service_completed')
   c.close()
 def test_obsolete_rows_not_replayed_on_startup(self):
  import tempfile,sqlite3,time
  import service
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'state').mkdir();c=sqlite3.connect(root/'state/world.db');c.execute('create table actions(id text,observed real,data text)');c.execute('insert into actions values(?,?,?)',('old',time.time()-20,json.dumps({'status':'service_completed'})));c.commit();c.close();worker=service.Worker()
   with patch.object(service,'ROOT',root):self.assertEqual(worker.read_ledgers()[0],[])
if __name__=='__main__':unittest.main()
