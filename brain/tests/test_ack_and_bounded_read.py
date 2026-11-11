"""Instant ack for device actions; post-action state read never holds the reply."""
import asyncio,json,unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from test_voice_live_progress import chunk,call,backend
import home_tools
from output import TurnOutput,current_output


class AckTests(unittest.IsolatedAsyncioTestCase):
    async def run_turn(self,stream_fn):
        o=TurnOutput('test',AsyncMock());b=backend(stream_fn());token=current_output.set(o)
        try:
            with patch('agent._exec_tool',AsyncMock(return_value='result')),patch('agent.ui.emit',AsyncMock()):
                await b._stream_turn(o,'test')
        finally:
            await o.close();current_output.reset(token)
        return o
    async def test_home_action_without_text_speaks_ack_once(self):
        async def stream():yield chunk(calls=[call('home_action','{"domain":"climate","service":"turn_on","entity_ids":["climate.x"]}')])
        o=await self.run_turn(stream)
        self.assertEqual(o.spoken,['On it.']);self.assertTrue(o.voice_update_sent)
    async def test_read_only_tool_gets_no_ack(self):
        async def stream():yield chunk(calls=[call('home_query','{}')])
        o=await self.run_turn(stream);self.assertEqual(o.spoken,[])
    async def test_model_text_before_action_is_not_doubled(self):
        async def stream():yield chunk('Turning it on. ');yield chunk(calls=[call('home_action','{}')])
        o=await self.run_turn(stream);self.assertEqual(o.spoken,['Turning it on.'])
    async def test_memory_tool_alone_gets_no_ack(self):
        async def stream():yield chunk(calls=[call('stage_memory_candidates','{}')])
        o=await self.run_turn(stream);self.assertEqual(o.spoken,[])


class BoundedReadTests(unittest.IsolatedAsyncioTestCase):
    def model(self,slow):
        async def get_state(eid):
            if slow:await asyncio.sleep(5)
            return {'entity_id':eid,'state':'on','attributes':{}}
        ha=NS(actions_enabled=True,call_service=AsyncMock(return_value='ok'),get_state=get_state)
        return NS(ha=ha,household={})
    async def test_slow_state_read_returns_pending_not_failure(self):
        m=self.model(True);before=[{'entity_id':'climate.x','state':'off','attributes':{}}]
        with patch.object(home_tools,'prepare_action',AsyncMock(return_value=before)),patch.object(home_tools,'AFTER_READ_TIMEOUT',0.05):
            r=await home_tools.action(m,{'domain':'climate','service':'turn_on','entity_ids':['climate.x']})
        self.assertTrue(r['service_completed']);self.assertTrue(r['observed_pending']);self.assertFalse(r['physical_success_confirmed'])
        self.assertEqual(m.ha.call_service.await_count,1)
    async def test_fast_state_read_still_reports_observed_state(self):
        m=self.model(False);before=[{'entity_id':'light.x','state':'off','attributes':{}}]
        with patch.object(home_tools,'prepare_action',AsyncMock(return_value=before)):
            r=await home_tools.action(m,{'domain':'light','service':'turn_on','entity_ids':['light.x']})
        self.assertEqual(r['status'],'observed_expected_state');self.assertNotIn('observed_pending',r)


if __name__=='__main__':unittest.main()
