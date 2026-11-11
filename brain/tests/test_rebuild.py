import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
from home_model import HomeModel
from home_tools import action
from output import TurnOutput,current_output
from telegram_transport import TelegramTransport,Rejected
from agent import _OpenAIBackend,SYSTEM_PROMPT
import tools

class HA:
    def __init__(self): self.actions_enabled=True; self.calls=[]; self.on=False
    async def get_states(self):
        return [dict(entity_id=e,state='on' if self.on else 'off',attributes={'friendly_name':n,'supported_color_modes':['onoff']}) for e,n in [('fan.primary','Ceiling Fan'),('light.moon','Moon'),('light.private','Private Moon')]]
    async def get_state(self,eid): return next(e for e in await self.get_states() if e['entity_id']==eid)
    async def ws_command(self,t,**kw):
        if 'area_registry' in t: return [{'area_id':'bedroom','name':'Bedroom'},{'area_id':'private','name':'Private Room'}]
        if 'device_registry' in t: return []
        return [{'entity_id':e,'area_id':a,'aliases':[]} for e,a in [('fan.primary','bedroom'),('light.moon','bedroom'),('light.private','private')]]
    async def request(self,*args): return [{'domain':'light','services':{'turn_on':{'fields':{'brightness_pct':{}}}}}]
    async def call_service(self,*args): self.calls.append(args); self.on=True; return 'ok'

class FakeRuntime:
    def __init__(self): self.config=SimpleNamespace(telegram_bot_token='',telegram_users={'1':'Owner'}); self.calls=[]
    async def turn(self,request,emit): self.calls.append(request); await emit({'type':'final','text':'Hello.'})
class API:
    def __init__(self): self.calls=[]; self.fail=False
    async def call(self,method,**data):
        self.calls.append((method,data))
        if self.fail: raise TimeoutError()
        return {'message_id':123}
    async def close(self): pass

def update(n=1,chat=1): return {'update_id':n,'message':{'chat':{'id':chat,'type':'private'},'from':{'id':chat},'text':'hi'}}

class Tests(unittest.IsolatedAsyncioTestCase):
    async def model(self):
        h=HA(); m=HomeModel(h,{'primary_area':'bedroom','common_areas':[],'area_names':{'bedroom':['our room']}}); await m.refresh(); return m
    async def test_scope_and_explicit_other_area(self):
        m=await self.model()
        self.assertNotIn('light.private',[e['entity_id'] for e in m.search('devices in our room')])
        self.assertEqual(m.resolve('Moon')['entity_id'],'light.moon')
        self.assertEqual(m.resolve('Private Moon')['entity_id'],'light.private')
        self.assertEqual(m.search('lights',area='Private Room')[0]['entity_id'],'light.private')
        self.assertNotIn('Private Moon',await m.context('hello'))
    async def test_registry_rename_refresh(self):
        m=await self.model(); original=m.ha.get_states
        async def renamed():
            rows=await original();rows[1]['attributes']['friendly_name']='Lunar';return rows
        m.ha.get_states=renamed;await m.refresh(force=True)
        self.assertEqual(m.resolve('Lunar')['entity_id'],'light.moon')
        with self.assertRaises(ValueError): m.resolve('Moon')
    async def test_readonly_blocks_and_verified_result(self):
        m=await self.model(); inp={'domain':'light','service':'turn_on','entity_ids':['light.moon']}
        output=TurnOutput('x',AsyncMock());output.read_only=True;token=current_output.set(output)
        try: self.assertEqual((await action(m,inp))['status'],'blocked_read_only')
        finally: current_output.reset(token)
        self.assertEqual(m.ha.calls,[])
        result=await action(m,inp);self.assertTrue(result['service_completed']);self.assertEqual(result['status'],'observed_expected_state')
        self.assertEqual(len(m.ha.calls),1)
    async def test_capability_validation_before_action(self):
        m=await self.model()
        with self.assertRaises(ValueError): await action(m,{'domain':'light','service':'turn_on','entity_ids':['light.moon'],'data':{'brightness_pct':30}})
        self.assertEqual(m.ha.calls,[])
    async def test_no_filler_and_no_wiki(self):
        events=[]
        async def emit(e): events.append(e)
        o=TurnOutput('x',emit)
        await o.tool('home_query',{},AsyncMock(return_value='ok'))
        self.assertEqual(o.spoken,[])
        b=object.__new__(_OpenAIBackend)
        with patch('wiki_io.context_snapshot',side_effect=AssertionError('Wiki read')):
            await b._refresh_turn_context('hello')
        self.assertEqual(b._turn_context,'')
        self.assertNotIn('light.moon',SYSTEM_PROMPT)
        self.assertFalse({'get_memories','save_memory','ask_fitbot','watch_condition'} & {d['name'] for d in tools.DEFINITIONS})
    async def test_telegram_restart_dedup_and_allowlist(self):
        with tempfile.TemporaryDirectory() as d:
            r=FakeRuntime();api=API();p=Path(d)/'t.db';t=TelegramTransport(r,p,api)
            await t.handle(update());await t.handle(update());await t.handle(update(2,2))
            self.assertEqual(len(r.calls),1);self.assertEqual(len(api.calls),1);self.assertEqual(t.offset,3)
            await t.close();t=TelegramTransport(r,p,api);await t.handle(update());self.assertEqual(len(api.calls),1);await t.close()
    async def test_ambiguous_delivery_never_replayed(self):
        with tempfile.TemporaryDirectory() as d:
            r=FakeRuntime();api=API();api.fail=True;p=Path(d)/'t.db';t=TelegramTransport(r,p,api)
            await t.handle(update());self.assertEqual(t.db.execute('select status from updates').fetchone()[0],'unknown');await t.close()
            t=TelegramTransport(r,p,api);await t.handle(update());self.assertEqual(len(api.calls),1);await t.close()
    async def test_crash_claim_not_reexecuted(self):
        with tempfile.TemporaryDirectory() as d:
            r=FakeRuntime();api=API();p=Path(d)/'t.db';t=TelegramTransport(r,p,api);t.claim(update());await t.close()
            t=TelegramTransport(r,p,api);await t.handle(update());self.assertEqual(r.calls,[]);self.assertEqual(t.db.execute('select status from updates').fetchone()[0],'unknown');await t.close()
    async def test_no_poller_while_legacy_active(self):
        with tempfile.TemporaryDirectory() as d:
            t=TelegramTransport(FakeRuntime(),Path(d)/'t.db',API())
            with patch('telegram_transport.subprocess.run',return_value=SimpleNamespace(stdout='active\n')):
                with self.assertRaises(RuntimeError): t.acquire()
            await t.close()
    async def test_program_refresh_and_related_helpers(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'automations.yaml').write_text('- id: test\n  alias: Night button\n  triggers:\n    - trigger: state\n      entity_id: light.moon\n  actions:\n    - action: fan.turn_off\n      target:\n        entity_id: fan.primary\n');(p/'scripts.yaml').write_text('{}');(p/'scenes.yaml').write_text('[]')
            m=await self.model();m.config_path=p;await m.refresh(force=True)
            self.assertEqual(m.related(['light.moon'])[0]['name'],'Night button')
            before=m.related(['light.moon'])[0]['fingerprint'];f=p/'automations.yaml';f.write_text(f.read_text().replace('Night button','Evening button'));await m.refresh(force=True)
            self.assertNotEqual(before,m.related(['light.moon'])[0]['fingerprint'])

if __name__=='__main__':unittest.main()

class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_exception_uses_explicit_set(self):
        h=HA();m=HomeModel(h,{'primary_area':'bedroom'});await m.refresh()
        # Two eligible primary lights and an excluded Moon; target set is explicit.
        for eid in ('light.cove','light.spots'):m.entities[eid]={'entity_id':eid}
        async def state(e):return {'entity_id':e,'state':'on','attributes':{}}
        h.get_state=state
        async def services(*args):return [{'domain':'light','services':{'turn_off':{'fields':{}}}}]
        h.request=services
        result=await action(m,{'domain':'light','service':'turn_off','entity_ids':['light.cove','light.spots']})
        self.assertEqual(h.calls[0][2]['entity_id'],['light.cove','light.spots']);self.assertNotIn('light.moon',h.calls[0][2]['entity_id'])
        self.assertEqual(result['status'],'service_completed') # HA still reports on; no false success.
    async def test_ac_range_mode_and_failure_semantics(self):
        h=HA();m=HomeModel(h,{'primary_area':'bedroom'});m.entities={'climate.ac':{'entity_id':'climate.ac'}}
        async def state(e):return {'entity_id':e,'state':'cool','attributes':{'min_temp':16,'max_temp':30,'hvac_modes':['off','cool']}}
        async def services(*a):return [{'domain':'climate','services':{'set_temperature':{'fields':{'temperature':{}}},'set_hvac_mode':{'fields':{'hvac_mode':{}}}}}]
        h.get_state=state;h.request=services
        with self.assertRaises(ValueError):await action(m,{'domain':'climate','service':'set_temperature','entity_ids':['climate.ac'],'data':{'temperature':40}})
        with self.assertRaises(ValueError):await action(m,{'domain':'climate','service':'set_hvac_mode','entity_ids':['climate.ac'],'data':{'hvac_mode':'invalid'}})
        self.assertEqual(h.calls,[])
        result=await action(m,{'domain':'climate','service':'set_temperature','entity_ids':['climate.ac'],'data':{'temperature':24}});self.assertTrue(result['service_completed'])
        h.call_service=AsyncMock(return_value='HA action failed or outcome unknown: TimeoutError')
        result=await action(m,{'domain':'climate','service':'set_temperature','entity_ids':['climate.ac'],'data':{'temperature':23}})
        self.assertEqual(result['status'],'unknown_or_failed');self.assertFalse(result['retry_safe']);h.call_service.assert_awaited_once()
    async def test_second_poller_lock_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'t.db';one=TelegramTransport(FakeRuntime(),p,API());two=TelegramTransport(FakeRuntime(),p,API())
            try:
                with patch('telegram_transport.subprocess.run',return_value=SimpleNamespace(stdout='inactive\n')):
                    one.acquire()
                    with self.assertRaises(RuntimeError):two.acquire()
            finally:
                if one.lock:one.lock.close()
                await one.close();await two.close()

class RateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_flood_rejection_retries_without_reexecuting_brain(self):
        with tempfile.TemporaryDirectory() as d:
            r=FakeRuntime();api=API();t=TelegramTransport(r,Path(d)/'t.db',api)
            normal=api.call;api.call=AsyncMock(side_effect=Rejected(120,429))
            await t.handle(update());self.assertEqual(t.db.execute('select status from updates').fetchone()[0],'ready')
            self.assertGreater(t.retry_at,__import__('time').time()+110)
            await t.send(1);self.assertEqual(api.call.await_count,1)
            t.retry_at=0;api.call=normal;await t.send(1)
            self.assertEqual(len(r.calls),1);self.assertEqual(t.db.execute('select status from updates').fetchone()[0],'sent');await t.close()

class LegacyFenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_running_legacy_fence_checks_null_delimited_environment_and_hash(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            r=FakeRuntime();source=b'reviewed legacy source';r.config.legacy_telegram_fence_sha256=hashlib.sha256(source).hexdigest()
            t=TelegramTransport(r,Path(d)/'t.db',API())
            try:
                with patch('telegram_transport.subprocess.run',side_effect=[SimpleNamespace(stdout='active\n'),SimpleNamespace(stdout='123\n')]),patch('telegram_transport.Path.read_bytes',side_effect=[b'OTHER=1\x00SUNDAY_TELEGRAM_ENABLED=0\x00',source]):
                    self.assertTrue(t.legacy_fenced())
                with patch('telegram_transport.subprocess.run',side_effect=[SimpleNamespace(stdout='active\n'),SimpleNamespace(stdout='123\n')]),patch('telegram_transport.Path.read_bytes',side_effect=[b'OTHER=SUNDAY_TELEGRAM_ENABLED=0\x00',source]):
                    self.assertFalse(t.legacy_fenced())
            finally:await t.close()
