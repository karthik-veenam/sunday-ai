"""Model-authored speech must stream before tool dispatch without corrupting text."""
import asyncio
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from agent import _OpenAIBackend,VOICE_PROMPT
from output import TurnOutput,current_output,speech_text


def chunk(text=None,calls=None):
    return NS(choices=[NS(delta=NS(content=text,tool_calls=calls))],usage=None,service_tier=None)

def call(name,args,ident='call-test',index=0):
    return NS(index=index,id=ident,function=NS(name=name,arguments=args))

def backend(stream):
    b=object.__new__(_OpenAIBackend)
    b.history=[];b._voice_model='test';b._voice_service_tier='default';b._turn_context=''
    b._tools=[];b._tools_telegram=[]
    b._system_messages=lambda:[];b._safe_history_slice=lambda:[]
    b._client=NS(chat=NS(completions=NS(create=AsyncMock(return_value=stream))))
    return b

class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_update_waits_for_sentence_then_reaches_ha_before_tool(self):
        events=[];paused=asyncio.Event();resume=asyncio.Event();sentence_ready=asyncio.Event();finish=asyncio.Event();executed=[]
        async def emit(e):events.append(e)
        o=TurnOutput('test',emit)
        async def stream():
            yield chunk('Let me ')
            paused.set();await resume.wait()
            yield chunk('look at the room. ')
            sentence_ready.set();await finish.wait()
            yield chunk(calls=[call('home_','{"query":')])
            yield chunk(calls=[call('query','"private_test_argument"}',ident=None)])
        b=backend(stream())
        async def execute(name,args,text):
            self.assertEqual(''.join(e['text'] for e in events if e['type']=='assistant_text'),'Let me look at the room. ')
            self.assertEqual(name,'home_query');self.assertEqual(args,{'query':'private_test_argument'})
            executed.append(name)
            return await o.tool(name,args,AsyncMock(return_value='private_test_result'))
        token=current_output.set(o)
        with patch('agent._exec_tool',execute),patch('agent.ui.emit',AsyncMock()):
            task=asyncio.create_task(b._stream_turn(o,'Temperature?'))
            try:
                await asyncio.wait_for(paused.wait(),1)
                self.assertFalse(task.done());self.assertFalse(o.speech_started.is_set())
                self.assertEqual(events,[]);self.assertEqual(executed,[])
                resume.set();await asyncio.wait_for(sentence_ready.wait(),1)
                await asyncio.wait_for(o.speech_started.wait(),1)
                self.assertEqual(events[0]['text'],'Let me look at the room. ')
                self.assertEqual(executed,[]);self.assertFalse(task.done())
                finish.set();await task
                self.assertEqual(executed,['home_query'])
                self.assertNotIn('private_test_', ' '.join(o.spoken))
                kinds=[e['type'] for e in events]
                self.assertLess(kinds.index('assistant_text'),kinds.index('tool_start'))
                self.assertEqual(b.history[-1]['role'],'tool')
                self.assertEqual(b.history[-2]['content'],'Let me look at the room. ')
            finally:
                if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
                await o.close();current_output.reset(token)
    async def test_quick_silent_tool_does_not_force_an_extra_progress_call(self):
        async def stream():yield chunk(calls=[call('home_query','{}')])
        o=TurnOutput('test',AsyncMock());b=backend(stream());token=current_output.set(o)
        try:
            with patch('agent._exec_tool',AsyncMock(return_value='result')),patch('agent.ui.emit',AsyncMock()):
                await b._stream_turn(o,'test')
            self.assertEqual(o.spoken,[])
            self.assertEqual(b._client.chat.completions.create.await_count,1)
        finally:await o.close();current_output.reset(token)
    async def test_only_one_progress_update_but_final_answer_is_preserved(self):
        async def first():
            yield chunk('Let me check. ');yield chunk(calls=[call('home_query','{}')])
        async def second():
            yield chunk('Now checking something else. ');yield chunk(calls=[call('home_query','{}')])
        async def final():yield chunk('Your target is 26.5 degrees. ')
        o=TurnOutput('test',AsyncMock());b=backend(first());token=current_output.set(o)
        b._client.chat.completions.create.side_effect=[first(),second(),final()]
        try:
            with patch('agent._exec_tool',AsyncMock(return_value='result')),patch('agent.ui.emit',AsyncMock()):
                await b._stream_turn(o,'test');self.assertTrue(o.voice_update_sent)
                await b._stream_turn(o,'test');self.assertEqual(o.spoken,['Let me check.'])
                await b._stream_turn(o,'test')
            self.assertEqual(o.spoken,['Let me check.','Your target is 26.5 degrees.'])
            self.assertEqual(b._client.chat.completions.create.await_count,3)
        finally:await o.close();current_output.reset(token)
    async def test_final_answer_with_private_memory_tool_is_not_suppressed(self):
        async def stream():
            yield chunk('The fan is off. ');yield chunk(calls=[call('stage_memory_candidates','{}')])
        o=TurnOutput('test',AsyncMock());o.voice_update_sent=True;b=backend(stream());token=current_output.set(o)
        try:
            with patch('agent._exec_tool',AsyncMock(return_value='result')),patch('agent.ui.emit',AsyncMock()):
                await b._stream_turn(o,'test')
            self.assertEqual(o.spoken,['The fan is off.'])
        finally:await o.close();current_output.reset(token)
    async def test_existing_speech_never_requests_another_progress_repair(self):
        async def stream():yield chunk(calls=[call('home_query','{}')])
        o=TurnOutput('test',AsyncMock());await o.speak('I am checking that.')
        b=backend(stream());token=current_output.set(o)
        try:
            with patch('agent._exec_tool',AsyncMock(return_value='ok')),patch('agent.ui.emit',AsyncMock()):
                await b._stream_turn(o,'test')
            self.assertEqual(b._client.chat.completions.create.await_count,1)
            self.assertEqual(o.spoken,['I am checking that.'])
        finally:await o.close();current_output.reset(token)
    async def test_private_memory_only_does_not_generate_progress(self):
        async def stream():yield chunk(calls=[call('stage_memory_candidates','{}')])
        o=TurnOutput('test',AsyncMock());b=backend(stream());token=current_output.set(o)
        try:
            with patch('agent._exec_tool',AsyncMock(return_value='ok')),patch('agent.ui.emit',AsyncMock()):
                await b._stream_turn(o,'test')
            self.assertEqual(o.spoken,[])
            self.assertEqual(b._client.chat.completions.create.await_count,1)
        finally:await o.close();current_output.reset(token)
    async def test_listening_control_does_not_generate_an_announcement(self):
        async def stream():yield chunk(calls=[call('end_conversation','{}')])
        o=TurnOutput('test',AsyncMock());b=backend(stream());token=current_output.set(o)
        try:
            with patch('agent._exec_tool',AsyncMock()) as execute,patch('agent.ui.emit',AsyncMock()):
                await b._stream_turn(o,'test')
            execute.assert_not_awaited();self.assertEqual(o.spoken,[])
        finally:await o.close();current_output.reset(token)
    async def test_cancellation_never_dispatches_partial_tool_or_flushes_late_audio(self):
        ready=asyncio.Event();blocked=asyncio.Event();events=[]
        async def emit(e):events.append(e)
        async def stream():
            yield chunk('Looking now')
            yield chunk(calls=[call('home_query','{"query":')])
            ready.set();await blocked.wait()
            yield chunk(' late text')
        o=TurnOutput('test',emit);b=backend(stream());token=current_output.set(o)
        with patch('agent._exec_tool',AsyncMock()) as execute,patch('agent.ui.emit',AsyncMock()):
            task=asyncio.create_task(b._stream_turn(o,'test'))
            try:
                await asyncio.wait_for(ready.wait(),1);task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
                await o.close();execute.assert_not_awaited()
                self.assertEqual(events,[])
            finally:await o.close();current_output.reset(token)
    async def test_legacy_audio_adapter_still_receives_whole_sentences(self):
        async def stream():
            yield chunk('Check');yield chunk('ing. ');yield chunk('Done.')
        tts=NS(speak=AsyncMock());b=backend(stream())
        with patch('agent.ui.emit',AsyncMock()):await b._stream_turn(tts,'test')
        self.assertEqual([c.args[0] for c in tts.speak.await_args_list],['Checking.','Done.'])
    async def test_fragmented_numbers_and_stage_directions_are_sanitized_at_sentence_boundary(self):
        async def stream():
            for text in ('[soft', 'ly] It is 26', '.5 degrees', '. '):yield chunk(text)
        events=[]
        async def emit(e):events.append(e)
        out=TurnOutput('test',emit);b=backend(stream());token=current_output.set(out)
        try:
            with patch('agent.ui.emit',AsyncMock()):await b._stream_turn(out,'test')
            self.assertEqual(out.spoken,['It is 26.5 degrees.'])
            self.assertEqual(''.join(e['text'] for e in events),'It is 26.5 degrees. ')
        finally:await out.close();current_output.reset(token)

    async def test_voice_does_not_offer_model_only_listening_control(self):
        async def stream():yield chunk('The room is warm. ')
        b=backend(stream());b._tools=[{'type':'function','function':{'name':name,'parameters':{'type':'object'}}} for name in ('home_query','end_conversation')]
        with patch('agent.ui.emit',AsyncMock()):await b._stream_turn(NS(speak=AsyncMock()),'How is my room?')
        self.assertEqual([d['function']['name'] for d in b._client.chat.completions.create.await_args.kwargs['tools']],['home_query'])

    async def test_voice_guidance_is_not_added_to_telegram(self):
        b=object.__new__(_OpenAIBackend);b._turn_context=''
        self.assertIn(VOICE_PROMPT,b._system_messages()[0]['content'])
        self.assertNotIn(VOICE_PROMPT,b._system_messages(telegram=True)[0]['content'])

class ReadOnlyRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_silent_readonly_diagnostics_do_not_offer_private_memory_writes(self):
        import tempfile
        from pathlib import Path
        from config import Config
        from runtime import Runtime
        import runtime as runtime_module
        class Brain:
            async def process(inner,text,out):
                self.assertFalse(hasattr(out,'memory_capture'))
                await out.speak('Read only.')
                return False
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'state').mkdir();(root/'household.json').write_text('{"primary_area":"bedroom"}')
            with patch.object(runtime_module,'ROOT',root):
                r=Runtime(Config(memory_write_enabled=True),agent_factory=Brain)
                r.home.context=AsyncMock(return_value='{}');r.home.refresh=AsyncMock()
                try:
                    events=[]
                    async def emit(e):events.append(e)
                    await r.turn({'request_id':'read-only','text':'hello','source':'ha','read_only':True},emit)
                    self.assertEqual(events[-1]['text'],'Read only.')
                    self.assertEqual(r.learned_memory.store.candidates(),[])
                finally:await r.close()

class SnapshotPreservationTests(unittest.IsolatedAsyncioTestCase):
    async def check_projection(self,projection=None,error=None):
        import tempfile
        from pathlib import Path
        from config import Config
        from runtime import Runtime
        import runtime as runtime_module
        captured=[]
        class Brain:
            async def process(inner,text,out):
                captured.append(out.home_context);await out.speak('The room is warm.');return False
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'state').mkdir();(root/'household.json').write_text('{"primary_area":"bedroom"}')
            with patch.object(runtime_module,'ROOT',root):
                r=Runtime(Config(world_context_injection_enabled=True),agent_factory=Brain)
                r.home.context=AsyncMock(return_value='{"relevant_devices":[{"name":"Room sensor","temperature":28.5}]}')
                r.home.refresh=AsyncMock();r.world=NS(ingest_presence=lambda snapshot:None,relevant_world_context=AsyncMock(return_value=projection,side_effect=error),close=AsyncMock())
                try:
                    await r.turn({'request_id':'snapshot-test','text':"How's my room looking right now?",'source':'ha','read_only':True},AsyncMock())
                    self.assertIn('28.5',captured[0]);self.assertIn('relevant_devices',captured[0])
                    return captured[0]
                finally:await r.close()
    async def test_empty_world_projection_keeps_room_snapshot(self):await self.check_projection('')
    async def test_world_evidence_appended_without_losing_snapshot(self):self.assertIn('causalproof',await self.check_projection('{"causalproof":"user requested"}'))
    async def test_world_failure_keeps_current_room_facts(self):self.assertIn('causal evidence unavailable',await self.check_projection(error=RuntimeError('world offline')))
