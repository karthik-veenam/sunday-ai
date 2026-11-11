import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from presence import PresenceProvider,PresenceSnapshot,contextual_text
from output import TurnOutput,current_output
import test_fast_mode as fixtures

HOUSEHOLD={'primary_person':'Karthik','primary_area':'primary','people':{'karthik':{'ha_person':'person.identity','presence_zones':{'home':'zone.homebase','office':'zone.workplace'}}}}

def state(eid,value,name='',age=0):
    stamp=(datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat()
    return {'entity_id':eid,'state':value,'last_updated':stamp,'last_reported':stamp,'attributes':{'friendly_name':name,'latitude':12.345678,'longitude':76.543219,'gps_accuracy':7,'source':'device_tracker.private_phone'}}

class PresenceHA:
    def __init__(self,value='home'):
        self.calls=[];self.fail=False
        self.rows={'person.identity':state('person.identity',value,'Karthik'),'zone.homebase':state('zone.homebase','1','Home'),'zone.workplace':state('zone.workplace','0','Work')}
    async def request(self,method,path):
        self.calls.append((method,path))
        if self.fail:raise OSError('network down')
        return self.rows[path.rsplit('/',1)[1]]

class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_mapping(self):
        for value,expected in [('home','HOME'),('Home','HOME'),('Work','OFFICE'),('workplace','OFFICE'),('zone.workplace','OFFICE'),('not_home','AWAY'),('Airport','AWAY'),('unavailable','UNKNOWN'),('unknown','UNKNOWN')]:
            with self.subTest(value=value):
                snap=await PresenceProvider(PresenceHA(value),HOUSEHOLD).get_presence()
                self.assertEqual(snap.presence,expected)
                self.assertNotIn('room_presence',snap.diagnostic())
    async def test_unreachable_never_away(self):
        ha=PresenceHA();ha.fail=True;snap=await PresenceProvider(ha,HOUSEHOLD).get_presence();self.assertEqual(snap.presence,'UNKNOWN')
    async def test_missing_identity_never_guess(self):
        ha=PresenceHA();snap=await PresenceProvider(ha,{'primary_person':'Karthik'}).get_presence();self.assertEqual(snap.presence,'UNKNOWN');self.assertFalse(ha.calls)
    async def test_missing_zone(self):
        ha=PresenceHA();ha.rows['zone.workplace']={};snap=await PresenceProvider(ha,HOUSEHOLD).get_presence();self.assertEqual(snap.presence,'UNKNOWN')
    async def test_stale_person(self):
        ha=PresenceHA();ha.rows['person.identity']=state('person.identity','home',age=3601)
        snap=await PresenceProvider(ha,HOUSEHOLD).get_presence();self.assertEqual(snap.presence,'UNKNOWN');self.assertEqual(snap.reason,'evidence_stale')
    async def test_old_last_changed_does_not_mean_stale(self):
        ha=PresenceHA();ha.rows['person.identity']['last_changed']='2020-01-01T00:00:00Z'
        snap=await PresenceProvider(ha,HOUSEHOLD).get_presence();self.assertEqual(snap.presence,'HOME')
    async def test_timestamp_fallback_and_absence(self):
        ha=PresenceHA();del ha.rows['person.identity']['last_reported'];snap=await PresenceProvider(ha,HOUSEHOLD).get_presence();self.assertEqual(snap.presence,'HOME')
        del ha.rows['person.identity']['last_updated'];snap=await PresenceProvider(ha,HOUSEHOLD).get_presence();self.assertEqual(snap.presence,'UNKNOWN')
    async def test_future_evidence_unknown(self):
        ha=PresenceHA();ha.rows['person.identity']=state('person.identity','home',age=-120)
        self.assertEqual((await PresenceProvider(ha,HOUSEHOLD).get_presence()).presence,'UNKNOWN')
    async def test_unavailable_after_cache_expires(self):
        ha=PresenceHA();p=PresenceProvider(ha,HOUSEHOLD);self.assertEqual((await p.get_presence()).presence,'HOME')
        ha.fail=True;snap=await p.get_presence();self.assertEqual(snap.presence,'HOME');self.assertTrue(snap.cached)
        stamp,s=p._cache['Karthik'];p._cache['Karthik']=(stamp-11,s)
        snap=await p.get_presence();self.assertEqual(snap.presence,'UNKNOWN');self.assertEqual(snap.reason,'ha_evidence_unavailable')
    async def test_cache_expires_when_evidence_age_exceeded(self):
        ha=PresenceHA();p=PresenceProvider(ha,HOUSEHOLD,max_age_seconds=1);await p.get_presence();stamp,s=p._cache['Karthik'];p._cache['Karthik']=(stamp-2,s);ha.fail=True
        self.assertEqual((await p.get_presence()).presence,'UNKNOWN')
    async def test_no_precise_data_in_snapshot_or_prompt(self):
        snap=await PresenceProvider(PresenceHA('Work'),HOUSEHOLD).get_presence()
        payload=json.dumps(snap.diagnostic())+snap.prompt_context('temperature here')
        for secret in ('latitude','longitude','12.345678','76.543219','gps_accuracy','private_phone'):self.assertNotIn(secret,payload)
        self.assertEqual(snap.prompt_context(),'Karthik presence: OFFICE')
    async def test_timeout_is_unknown(self):
        ha=PresenceHA()
        async def hang(*a):await asyncio.sleep(1)
        ha.request=hang;snap=await PresenceProvider(ha,HOUSEHOLD,timeout_seconds=.01).get_presence();self.assertEqual(snap.presence,'UNKNOWN')
    async def test_zone_name_collision_unknown(self):
        ha=PresenceHA();ha.rows['zone.workplace']['attributes']['friendly_name']='Home'
        self.assertEqual((await PresenceProvider(ha,HOUSEHOLD).get_presence()).presence,'UNKNOWN')

class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_presence_and_remote_access(self):
        from fast_mode import FastMode
        import home_tools
        model=fixtures.Model();home_tools.configure(model)
        sensor=fixtures.entity('temperature','Room Temperature','sensor');sensor.update(state='28');sensor['attributes']={'device_class':'temperature','unit_of_measurement':'°C'};model.entities[sensor['entity_id']]=sensor
        with tempfile.TemporaryDirectory() as d:
            fast=FastMode(Path(d)/'fast.db')
            try:
                for semantic in ('HOME','OFFICE','AWAY','UNKNOWN'):
                    snap=PresenceSnapshot('Karthik',semantic,'home_assistant','now')
                    async def emit(e):pass
                    out=TurnOutput(semantic,emit);tok=current_output.set(out)
                    try:
                        r=await fast.attempt(model,"what's the temperature here?",'c',semantic,'test',True,False,'all',presence=snap)
                        if semantic=='HOME':self.assertTrue(r.get('handled'));self.assertIn('28',r['reply'])
                        else:self.assertEqual(r['decision'],'FALLBACK');self.assertFalse(r.get('handled'));self.assertFalse(r['plan'])
                        self.assertEqual(r['model_calls'],0)
                        r=await fast.attempt(model,'AC off','c',semantic+'remote','test',True,False,'all',presence=snap)
                        self.assertTrue(r['handled']);self.assertEqual(r['model_calls'],0)
                    finally:current_output.reset(tok)
            finally:fast.db.close()
    async def test_context_qualifiers(self):
        home=PresenceSnapshot('Karthik','HOME','home_assistant','now');away=PresenceSnapshot('Karthik','AWAY','home_assistant','now')
        self.assertEqual(contextual_text("what's the temperature here?",home),"what's the room temperature?")
        self.assertEqual(contextual_text('all lights off at home',away),'all lights off')
        self.assertEqual(contextual_text('all lights in my room off',away),'all lights in my room off')
        for snapshot in (home,away):
            with self.assertRaises(ValueError):contextual_text('all lights off at the office',snapshot)
        with self.assertRaises(ValueError):contextual_text('all lights here off',away)
    async def test_telegram_voice_runtime_context_parity(self):
        import runtime as module
        from runtime import Runtime
        from config import Config
        seen=[]
        class Brain:
            def reset(self):pass
            async def respond_as_text(self,text):seen.append(current_output.get().presence_context);return 'Ready.'
            async def process(self,text,out):seen.append(out.presence_context);await out.speak('Ready.');return False
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'state').mkdir();(root/'household.json').write_text(json.dumps(HOUSEHOLD))
            with patch.object(module,'ROOT',root):
                r=Runtime(Config(),agent_factory=Brain);ha=PresenceHA('Work');r.presence=PresenceProvider(ha,HOUSEHOLD);r.home.context=AsyncMock(return_value='{}')
                async def emit(e):pass
                try:
                    for source in ('telegram','ha'):
                        await r.turn({'request_id':source,'conversation_id':source,'source':source,'text':'How are you?'},emit)
                    self.assertEqual(seen,['Karthik presence: OFFICE']*2);self.assertEqual(len(ha.calls),3)
                finally:await r.close()
    async def test_runtime_fast_uses_the_same_provider_without_agent(self):
        import runtime as module,home_tools
        from runtime import Runtime
        from config import Config
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'state').mkdir();(root/'household.json').write_text(json.dumps(HOUSEHOLD))
            def forbidden():raise AssertionError('model/agent called')
            with patch.object(module,'ROOT',root):
                r=Runtime(Config(fast_mode_enabled=True,fast_mode_execute=True,fast_mode_stage='all'),agent_factory=forbidden)
                model=fixtures.Model();r.home=model;home_tools.configure(model);ha=PresenceHA('not_home');r.presence=PresenceProvider(ha,HOUSEHOLD);events=[]
                async def emit(e):events.append(e)
                try:
                    for source in ('telegram','ha'):
                        await r.turn({'request_id':source,'conversation_id':source,'source':source,'text':'Moon off'},emit)
                        self.assertEqual(events[-1]['model_calls'],0)
                    self.assertEqual(len(ha.calls),3);self.assertEqual(r.presence.last_diagnostic['presence'],'AWAY')
                finally:await r.close()
    async def test_provider_context_in_both_backend_system_prompts(self):
        from agent import _OpenAIBackend,_GeminiBackend
        out=TurnOutput('p',AsyncMock());out.presence_context='Karthik presence: OFFICE';token=current_output.set(out)
        try:
            openai=_OpenAIBackend.__new__(_OpenAIBackend);openai._turn_context='{}'
            for telegram in (True,False):
                msgs=openai._system_messages(telegram);self.assertIn('Karthik presence: OFFICE',msgs[-1]['content']);self.assertNotIn('presence:',msgs[0]['content'])
            gemini=_GeminiBackend.__new__(_GeminiBackend);gemini._turn_context='{}';gemini._insights=[];gemini._tools=[];gemini._tools_telegram=[]
            gemini._types=SimpleNamespace(GenerateContentConfig=lambda **kw:SimpleNamespace(**kw),ThinkingConfig=lambda **kw:kw)
            for telegram in (True,False):self.assertIn('Karthik presence: OFFICE',gemini._make_config(telegram=telegram).system_instruction)
        finally:current_output.reset(token)

    async def test_authenticated_presence_endpoint_is_coarse(self):
        from aiohttp.test_utils import TestClient,TestServer
        from main import create_app
        import runtime as module
        from runtime import Runtime
        from config import Config
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'state').mkdir();(root/'household.json').write_text(json.dumps(HOUSEHOLD))
            with patch.object(module,'ROOT',root):
                r=Runtime(Config(daemon_token='test'),agent_factory=lambda:None);r.presence=PresenceProvider(PresenceHA('home'),HOUSEHOLD)
                r.home.refresh=AsyncMock();r.home.search=lambda *a,**kw:[]
                client=TestClient(TestServer(create_app(r)));await client.start_server()
                try:
                    resp=await client.get('/v1/presence');self.assertEqual(resp.status,401)
                    resp=await client.get('/v1/presence',headers={'Authorization':'Bearer test'});payload=await resp.json();self.assertEqual(payload['presence'],'HOME')
                    for word in ('latitude','longitude','gps_accuracy','device_tracker'):self.assertNotIn(word,json.dumps(payload))
                finally:await client.close()
    async def test_pending_here_rechecks_current_presence(self):
        from fast_mode import FastMode
        import home_tools
        model=fixtures.Model();model.entities['fan.f']['name']='Left Fan';e=fixtures.entity('right','Right Fan','fan');model.entities[e['entity_id']]=e;home_tools.configure(model)
        with tempfile.TemporaryDirectory() as d:
            fast=FastMode(Path(d)/'fast.db')
            async def emit(e):pass
            out=TurnOutput('p',emit);tok=current_output.set(out)
            try:
                home=PresenceSnapshot('Karthik','HOME','home_assistant','now');office=PresenceSnapshot('Karthik','OFFICE','home_assistant','now')
                r=await fast.attempt(model,'fan here on','c','first','test',True,False,'all',presence=home);self.assertEqual(r['decision'],'CLARIFY')
                r=await fast.attempt(model,'right one','c','second','test',True,False,'all',presence=office);self.assertEqual(r['decision'],'FALLBACK');self.assertFalse(model.ha.calls)
            finally:current_output.reset(tok);fast.db.close()
