import unittest,json
from unittest.mock import AsyncMock
from agent import _exec_tool,_capture_reply,_memory_result_needed
from output import TurnOutput,current_output
class Tests(unittest.IsolatedAsyncioTestCase):
 async def run_case(self,states,fail=False):
  o=TurnOutput('memory-status-test',AsyncMock());o.memory_learning=True
  def capture(args):
   if fail:raise OSError('test disk failure')
   return ['candidate:test']
  o.memory_capture=capture;o.memory_capture_status=lambda ids:states
  token=current_output.set(o)
  try:
   args={'memories':[{'statement':'A useful statement.','quote':'user quote','future_impact':'Use this to improve later answers.'}]}
   result=json.loads(await _exec_tool('add_memory',args,'user quote'))
   calls=[('add_memory',json.dumps(args)),('express_reply',json.dumps({'reply':'A natural reply.'}))]
   answer=_capture_reply(calls);next_answer=_capture_reply([('express_reply',json.dumps({'reply':'Thanks for telling me.'}))])
   return result,o,answer,next_answer
  finally:current_output.reset(token);await o.close()
 async def test_pending_is_not_storage_error_and_reads_result_before_reply(self):
  r,o,a,n=await self.run_case(['NEW']);self.assertEqual(r['status'],'pending_review');self.assertFalse(getattr(o,'memory_capture_error',False));self.assertTrue(o.memory_capture_done);self.assertEqual(a,'');self.assertEqual(n,'Thanks for telling me.')
 async def test_durable_save_preserves_single_round_reply(self):
  r,o,a,n=await self.run_case(['ACCEPTED']);self.assertEqual(r['status'],'saved');self.assertEqual(a,'A natural reply.');self.assertFalse(getattr(o,'memory_capture_pending',False))
 async def test_real_failure_still_prevents_false_save_confirmation(self):
  r,o,a,n=await self.run_case([],True);self.assertEqual(r['status'],'not_staged');self.assertTrue(o.memory_capture_error);self.assertIn("couldn't save",a)
 async def test_uncertain_evidence_wait_is_pending(self):
  r,o,a,n=await self.run_case(['AWAITING_EVIDENCE']);self.assertEqual(r['status'],'pending_review');self.assertEqual(r['saved'],0);self.assertEqual(a,'')
 async def test_pending_followup_never_speaks_premature_save(self):
  from test_voice_live_progress import backend,chunk,call
  from unittest.mock import patch
  async def first():
   yield chunk(calls=[call('add_memory',json.dumps({'memories':[{'statement':'A scoped interpretation.','quote':'user quote','future_impact':'Improve how future replies are phrased.','inferred':True,'uncertain':True}]}))])
   yield chunk(calls=[call('express_reply',json.dumps({'reply':"I've saved that permanently."}),ident='reply',index=1)])
  async def second():yield chunk(calls=[call('express_reply',json.dumps({'reply':'Thanks for explaining.'}),ident='reply2')])
  o=TurnOutput('pending-stream',AsyncMock());o.memory_learning=True;o.memory_capture=lambda args:['candidate:test'];o.memory_capture_status=lambda ids:['NEW'];b=backend(first());b._client.chat.completions.create.side_effect=[first(),second()];t=current_output.set(o)
  try:
   with patch('agent.ui.emit',AsyncMock()):
    result=await b._stream_turn(o,'user quote');self.assertTrue(result[1]);self.assertEqual(o.spoken,[])
    await b._stream_turn(o,'user quote');self.assertEqual(o.spoken,['Thanks for explaining.'])
  finally:current_output.reset(t);await o.close()
