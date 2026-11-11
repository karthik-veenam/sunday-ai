import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from agent import _OpenAIBackend
from test_voice_live_progress import backend,chunk
class BudgetTests(unittest.IsolatedAsyncioTestCase):
 async def test_exhaustion_generates_final_answer_once(self):
  b=object.__new__(_OpenAIBackend);b.history=[];b._voice_model='test';b._timestamp_prefix=lambda:'';b._refresh_turn_context=AsyncMock()
  b._stream_turn=AsyncMock(side_effect=[('',[{'name':'history_read'}],False,[])]*12+[('I cannot establish the cause.',[],True,[])])
  tts=NS(speak=AsyncMock())
  self.assertFalse(await b.process('Why is it on?',tts))
  self.assertEqual(b._stream_turn.await_count,13)
  self.assertTrue(b._stream_turn.call_args.kwargs['finalize'])
 async def test_final_round_has_no_tools_and_speaks_answer(self):
  async def stream():yield chunk('The cause is uncertain. ')
  b=backend(stream());tts=NS(speak=AsyncMock())
  with patch('agent.ui.emit',AsyncMock()):
   text,tools,_,_=await b._stream_turn(tts,'why',finalize=True)
  args=b._client.chat.completions.create.call_args.kwargs
  self.assertEqual(args['tools'],[]);self.assertEqual(args['tool_choice'],'none')
  self.assertIn('budget is exhausted',args['messages'][-1]['content'])
  self.assertEqual(tools,[]);self.assertIn('uncertain',text);tts.speak.assert_awaited()
