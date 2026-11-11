import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from aiohttp import web
import codex_tools as jobs
import codex_delivery
class Jobs(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'jobs.db'
  self.p=patch.object(jobs,'DB',self.path);self.p.start()
  self.ready=patch.object(codex_delivery,'voice_ready',AsyncMock(return_value=True));self.ready.start()
  self.send=patch.object(codex_delivery,'send_voice',AsyncMock(return_value='ok'));self.sender=self.send.start()
 def tearDown(self):self.p.stop();self.ready.stop();self.send.stop();self.temp.cleanup()
 def add(self,ref='r'):
  return jobs.enqueue({'request':'Investigate','allow_changes':False},ref,'c')['job_id']
 def complete(self,ident):
  with jobs.connect() as db:db.execute("UPDATE jobs SET status='completed',result=?,delivery='pending' WHERE id=?",(json.dumps({'speech':'Codex found the issue.','report':'Evidence'}),ident))
 def runtime(self):return NS(active={},config=NS(outbound_speech_enabled=True,default_satellite='sat'),ha=NS(get_state=AsyncMock(return_value={'state':'idle'}),call_service=AsyncMock(return_value='ok')))
 def test_deduplicate_and_reject_changed_request(self):
  a=self.add();self.assertEqual(a,self.add())
  with self.assertRaises(ValueError):jobs.enqueue({'request':'Different'},'r','c')
 def test_queue_limit(self):
  for i in range(4):self.add(str(i))
  self.assertEqual(jobs.enqueue({},'fifth','c')['status'],'busy')
 async def test_readonly_does_not_enqueue(self):
  token=jobs.current_output.set(NS(read_only=True))
  try:self.assertEqual((await jobs.execute({}))['status'],'disabled')
  finally:jobs.current_output.reset(token)
 async def test_busy_waits_without_dispatch(self):
  i=self.add();self.complete(i);r=self.runtime();r.active['x']='busy'
  with self.assertRaises(web.HTTPConflict):await codex_delivery.deliver(r,i)
  self.sender.assert_not_awaited()
 async def test_delivery_exactly_one_attempt(self):
  i=self.add();self.complete(i);r=self.runtime()
  self.assertEqual((await codex_delivery.deliver(r,i))['status'],'sent')
  await codex_delivery.deliver(r,i);self.sender.assert_awaited_once();self.assertFalse(r.active)
 async def test_ambiguous_delivery_not_retried(self):
  i=self.add();self.complete(i);r=self.runtime();self.sender.side_effect=TimeoutError()
  self.assertEqual((await codex_delivery.deliver(r,i))['status'],'unknown')
  await codex_delivery.deliver(r,i);self.sender.assert_awaited_once()
 async def test_disabled_and_unavailable_wait(self):
  i=self.add();self.complete(i);r=self.runtime();r.config.outbound_speech_enabled=False
  with self.assertRaises(web.HTTPConflict):await codex_delivery.deliver(r,i)
  r.config.outbound_speech_enabled=True;r.ha.get_state.return_value={'state':'unavailable'}
  with self.assertRaises(web.HTTPConflict):await codex_delivery.deliver(r,i)
  self.sender.assert_not_awaited()
 async def test_lookup_reads_completed_output_without_new_job(self):
  i=self.add();self.complete(i)
  token=jobs.current_output.set(NS(read_only=True,codex_conversation='c'))
  try:
   r=await jobs.lookup({'job_id':i})
   self.assertEqual(r['jobs'][0]['result']['speech'],'Codex found the issue.')
   self.assertEqual(r['jobs'][0]['status'],'completed')
   with jobs.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0],1)
  finally:jobs.current_output.reset(token)
 async def test_lookup_does_not_cross_conversations(self):
  i=self.add();self.complete(i)
  token=jobs.current_output.set(NS(codex_conversation='other'))
  try:self.assertEqual((await jobs.lookup({'job_id':i}))['jobs'],[])
  finally:jobs.current_output.reset(token)
 def test_context_preserves_summaries_with_large_reports(self):
  i=self.add();self.complete(i)
  with jobs.connect() as db:db.execute('UPDATE jobs SET result=? WHERE id=?',(json.dumps({'speech':'Distinct saved summary.','report':'long '*10000}),i))
  context=jobs.context('c');self.assertIn('Distinct saved summary.',context)
  data=json.loads(context.split('\n',1)[1]);self.assertTrue(data[0]['result']['report_truncated'])
 def test_all_reply_backends_include_saved_results(self):
  from agent import _OpenAIBackend,_GeminiBackend
  i=self.add();self.complete(i)
  token=jobs.current_output.set(NS(codex_conversation='c',extra_context=''))
  try:
   b=object.__new__(_OpenAIBackend);b._turn_context=''
   self.assertIn('Codex found the issue.',json.dumps(b._system_messages()))
   g=object.__new__(_GeminiBackend);g._turn_context='';g._insights=[]
   g._types=NS(GenerateContentConfig=lambda **k:NS(**k),ThinkingConfig=lambda **k:NS(**k))
   self.assertIn('Codex found the issue.',g._make_config(tools=False).system_instruction)
  finally:jobs.current_output.reset(token)
if __name__=='__main__':unittest.main()
