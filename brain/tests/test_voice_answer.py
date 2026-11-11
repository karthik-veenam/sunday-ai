import unittest
from unittest.mock import AsyncMock,patch
from agent import _OpenAIBackend,_END_CONV_DEF
from output import TurnOutput,current_output

class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_control_only_round_recovers_once(self):
        b=object.__new__(_OpenAIBackend);b.history=[];b._voice_model='fake'
        b._refresh_turn_context=AsyncMock();b._stream_turn=AsyncMock(side_effect=[('',[],True,[]),('It is 30 degrees.',[],True,[])])
        tts=TurnOutput('test',AsyncMock())
        await b.process('Temperature?',tts)
        self.assertEqual(b._stream_turn.await_count,2)
        self.assertFalse(b._stream_turn.await_args_list[0].kwargs['repair_empty'])
        self.assertTrue(b._stream_turn.await_args_list[1].kwargs['repair_empty'])
        self.assertNotIn('REQUIRED',_END_CONV_DEF['description'])
    async def test_empty_recovery_is_bounded(self):
        b=object.__new__(_OpenAIBackend);b.history=[];b._voice_model='fake'
        b._refresh_turn_context=AsyncMock();b._stream_turn=AsyncMock(return_value=('',[],True,[]))
        tts=TurnOutput('test',AsyncMock());await b.process('hi',tts)
        self.assertEqual(b._stream_turn.await_count,2)
        self.assertIn('complete answer',tts.spoken[-1])

    async def test_recovery_after_dispatched_tool_cannot_call_action_tools(self):
        from types import SimpleNamespace
        b=object.__new__(_OpenAIBackend);b.history=[];b._voice_model='fake';b._voice_service_tier='default'
        defs=[{'type':'function','function':{'name':n,'parameters':{}}} for n in ('home_action','home_query')]
        b._tools=defs;b._tools_telegram=defs;b._system_messages=lambda:[];b._safe_history_slice=lambda:[]
        async def stream():
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='The command was sent.',tool_calls=None))],usage=None,service_tier=None)
        create=AsyncMock(return_value=stream());b._client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        out=TurnOutput('test',AsyncMock());out.tool_count=1;token=current_output.set(out)
        try:
            await b._stream_turn(out,'Switch off fan',repair_empty=True)
            self.assertEqual([d['function']['name'] for d in create.await_args.kwargs['tools']],['home_query'])
            create.assert_awaited_once()
        finally:await out.close();current_output.reset(token)
