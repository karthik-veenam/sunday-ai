import unittest, tempfile, json
from pathlib import Path
from fast_mode import *
from types import SimpleNamespace
from output import TurnOutput,current_output
import home_tools

def entity(eid,name,domain='light',**kw):
    return dict(entity_id=domain+'.'+eid,name=name,aliases=[],domain=domain,area_id='primary',available=True,state='off',attributes={'supported_color_modes':['brightness']},**kw)
class HA:
    actions_enabled=True
    def __init__(self,m):self.m=m;self.calls=[]
    async def close(self):pass
    async def get_state(self,e):return self.m.entities[e]
    async def request(self,*a):return [{'domain':d,'services':{s:{'fields':{k:{} for k in ('temperature','hvac_mode','brightness_pct','percentage','volume_level')}} for s in ss}} for d,ss in home_tools.ALLOWED.items()]
    async def call_service(self,d,s,data):
        self.calls.append((d,s,data));return 'ok'
class Model:
    def __init__(self):
        rows=[entity('a','Moon'),entity('b','Cove'),entity('c','Dashboard'),entity('f','Ceiling Fan','fan'),entity('t','Bedroom AC','climate')]
        rows[-1]['attributes']={'supported_features':1,'min_temp':16,'max_temp':30,'hvac_modes':['off','cool']}
        rows[-2]['attributes']={'supported_features':1,'percentage':40,'percentage_step':20}
        self.devices={};self.entities={e['entity_id']:e for e in rows};self.areas={'primary':{'name':'Bedroom'}};self.household={'primary_area':'primary'};self.ha=HA(self)
    async def refresh(self,*a,**kw):pass
    async def refresh_current_states(self):pass
class ParserTests(unittest.TestCase):
    def test_generated_language(self):
        count=0
        for name in ('Moon','Cove','Bedroom AC','new renamed lamp'):
            for op in ('on','off'):
                for sentence in (f'{name} {op}',f'turn {name} {op}',f'switch {op} the {name}',f'shut the {name} {op}',f'power {name} {op}'):
                    for wrapper in ('{}','can you please {}','hey Sunday, could you {} please','{} for me'):
                        with self.subTest(sentence=wrapper.format(sentence)):
                            ir=parse(wrapper.format(sentence));self.assertEqual(ir.operations[0].operation,op.upper());self.assertEqual(ir.operations[0].targets,[name.lower()]);count+=1
        self.assertEqual(count,160)
    def test_required_examples(self):
        corpus=['fan off','switch the fan off','can you please shut the fan off','kill the fan','Moon on','switch Moon on please','AC 24','set AC to 24','make the AC 24 degrees','Moon 20%','set Moon brightness to 20 percent','turn Moon and Cove on','turn Moon on and Cove off','all lights off','turn off every light here','kill all the lights in our room','all lights except Moon off','turn off every light but Moon','leave Moon alone and kill the other lights','turn all the lights off but leave Moon on','turn everything here off except the AC','increase fan speed','make the fan faster','lower the fan speed','is the fan on?',"what's the room temperature?"]
        for text in corpus:
            with self.subTest(text=text):self.assertTrue(parse(text).operations)
    def test_semantic_adversarial(self):
        for text in ['make the room cozy','set things up for a movie',"it's too bright in here, fix it",'do what you think is best','do not turn Moon on',"don't turn Moon off",'turn Moon on if I am home','turn Moon off then Cove on','Moon on or Cove off','maybe Moon off','why did AC turn off']:
            with self.subTest(text=text):
                with self.assertRaises(Miss):parse(text)
class PlanningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.m=Model();self.tmp=tempfile.TemporaryDirectory();self.f=FastMode(Path(self.tmp.name)/'fast.db');home_tools.configure(self.m)
    async def asyncTearDown(self):self.f.db.close();self.tmp.cleanup()
    async def attempt(self,text,execute=False,conversation='c'):
        async def emit(x):pass
        o=TurnOutput(str(time.time_ns()),emit);token=current_output.set(o)
        try:return await self.f.attempt(self.m,text,conversation,o.request_id,'test',execute)
        finally:current_output.reset(token)
    async def test_zero_model_exec(self):
        r=await self.attempt('Moon on',True);self.assertTrue(r['handled']);self.assertEqual(r['model_calls'],0);self.assertEqual(len(self.m.ha.calls),1)
    async def test_shadow_never_dispatches(self):
        for text in ('Moon on','all lights off','turn Moon on and Cove off','AC 24','Moon 20%'):
            r=await self.attempt(text);self.assertEqual(r['decision'],'EXECUTE',r)
        self.assertFalse(self.m.ha.calls)
    async def test_exclusion_and_exception(self):
        r=await self.attempt('all lights except Moon off');self.assertEqual({x['entity_ids'][0] for x in r['plan']},{'light.b','light.c'})
        r=await self.attempt('all lights off but leave Moon on');self.assertEqual(next(x for x in r['plan'] if x['entity_ids']==['light.a'])['service'],'turn_on')
    async def test_unknown_later_target_atomic(self):
        r=await self.attempt('turn Moon on and imaginary off',True);self.assertEqual(r['decision'],'FALLBACK');self.assertFalse(self.m.ha.calls);self.assertFalse(r.get('handled'))
    async def test_invalid_later_target_atomic(self):
        self.m.entities['light.b']['available']=False;self.m.entities['light.b']['state']='unavailable'
        r=await self.attempt('turn Moon on and Cove off',True);self.assertEqual(r['decision'],'REJECT');self.assertFalse(self.m.ha.calls)
    async def test_brightness_and_ranges(self):
        r=await self.attempt('Moon 20%');self.assertEqual(r['plan'][0]['data'],{'brightness_pct':20})
        for text in ('Moon 120%','AC 90','fan temperature 24'):
            r=await self.attempt(text,True);self.assertNotEqual(r['decision'],'EXECUTE');self.assertFalse(self.m.ha.calls)
    async def test_dynamic_rename(self):
        self.m.entities['light.a']['name']='Aster';self.assertEqual((await self.attempt('Aster on'))['decision'],'EXECUTE');self.assertEqual((await self.attempt('Moon on'))['decision'],'FALLBACK')
    async def test_contextual_ambiguity_delegates_without_stale_pending_or_action(self):
        self.m.entities['fan.f']['name']='Left Fan';e=entity('right','Right Fan','fan');self.m.entities[e['entity_id']]=e
        r=await self.f.attempt(self.m,'fan on','context','test','voice',execute=True,contextual_ambiguity=True)
        self.assertEqual(r['decision'],'FALLBACK');self.assertFalse(r.get('handled'));self.assertFalse(self.m.ha.calls)
        self.assertEqual(self.f.db.execute('select count(*) from pending').fetchone()[0],0)
        saved=json.loads(self.f.db.execute('select data from attempts where id=?',('test',)).fetchone()[0]);self.assertEqual(saved['decision'],'FALLBACK')
    async def test_ambiguity_and_followup(self):
        self.m.entities['fan.f']['name']='Left Fan';e=entity('right','Right Fan','fan');self.m.entities[e['entity_id']]=e
        r=await self.attempt('fan on',True);self.assertEqual(r['decision'],'CLARIFY');self.assertFalse(self.m.ha.calls)
        r=await self.attempt('Right one',True);self.assertTrue(r['handled']);self.assertEqual(self.m.ha.calls[0][2]['entity_id'],['fan.right']);self.assertEqual(r['model_calls'],0)
    async def test_unrelated_followup_does_not_execute(self):
        self.m.entities['fan.f']['name']='Left Fan';e=entity('right','Right Fan','fan');self.m.entities[e['entity_id']]=e
        await self.attempt('fan on',True);r=await self.attempt('right now tell me a joke',True);self.assertEqual(r['decision'],'FALLBACK');self.assertFalse(self.m.ha.calls)
    async def test_other_conversation_does_not_claim(self):
        self.m.entities['fan.f']['name']='Left Fan';e=entity('right','Right Fan','fan');self.m.entities[e['entity_id']]=e
        await self.attempt('fan on',True);r=await self.attempt('right one',True,'different');self.assertFalse(r.get('handled'));self.assertFalse(self.m.ha.calls)
    async def test_expiry(self):
        self.m.entities['fan.f']['name']='Left Fan';e=entity('right','Right Fan','fan');self.m.entities[e['entity_id']]=e
        await self.attempt('fan on',True);self.f.db.execute('update pending set expires=0');self.f.db.commit();r=await self.attempt('right one',True);self.assertFalse(r.get('handled'));self.assertFalse(self.m.ha.calls)
    async def test_readonly(self):
        r=await self.f.attempt(self.m,'Moon on','c','readonly','test',True,True);self.assertFalse(r.get('handled'));self.assertFalse(self.m.ha.calls)
    async def test_group_production_gate(self):
        r=await self.attempt('all lights on',True);self.assertEqual(r['decision'],'FALLBACK');self.assertFalse(self.m.ha.calls)
    async def test_typo_never_best_guess(self):
        r=await self.attempt('Moo on');self.assertEqual(r['decision'],'FALLBACK')
    async def test_relative_plan(self):
        r=await self.attempt('increase fan speed');self.assertEqual(r['plan'][0]['data'],{'percentage':60})
    async def test_assistant_and_service_devices_excluded(self):
        e=entity('speaker','Assistant','media_player');e['device_id']='assistant'
        sat=entity('sat','Assistant','assist_satellite');sat['device_id']='assistant'
        svc=entity('svc','Infrastructure','switch');svc['device_id']='service'
        for x in (e,sat,svc):self.m.entities[x['entity_id']]=x
        self.m.devices={'service':{'entry_type':'service'}}
        r=await self.attempt('everything off');targets=[x['entity_ids'][0] for x in r['plan']]
        self.assertNotIn('media_player.speaker',targets);self.assertNotIn('switch.svc',targets)
    async def test_friendly_device_prefix_dynamic(self):
        self.m.entities['light.b']['name']='Hub Controller Cove';self.m.entities['light.b']['device_id']='hub'
        self.m.devices={'hub':{'name':'Hub Controller'}}
        r=await self.attempt('Cove on');self.assertEqual(r['plan'][0]['entity_ids'],['light.b'])
    async def test_sensitive_cover(self):
        e=entity('gate','Gate','cover');e['attributes']={'device_class':'gate'};self.m.entities[e['entity_id']]=e
        r=await self.attempt('open Gate',True);self.assertFalse(r.get('handled'));self.assertFalse(self.m.ha.calls)
    async def test_already_correct(self):
        self.m.entities['light.a']['state']='on';r=await self.attempt('Moon on',True);self.assertIn('already',r['reply']);self.assertFalse(self.m.ha.calls)
    async def test_redaction(self):
        await self.attempt('my password is abc123');data=self.f.db.execute('select data from attempts').fetchone()[0];self.assertNotIn('abc123',data)
if __name__=='__main__':unittest.main()

class SharedRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_receipts_channels_restart_no_provider(self):
        from runtime import Runtime
        from config import Config
        import runtime as module
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'state').mkdir();(root/'household.json').write_text('{"primary_area":"primary"}')
            def forbidden():raise AssertionError('provider/agent construction on fast path')
            with patch.object(module,'ROOT',root):
                config=Config(fast_mode_enabled=True,fast_mode_execute=True,llm_provider='openai')
                r=Runtime(config,agent_factory=forbidden);model=Model();r.home=model;home_tools.configure(model);events=[]
                async def emit(e):events.append(e)
                try:
                    req={'request_id':'telegram-fast','conversation_id':'c','source':'telegram','text':'Moon on'}
                    await r.turn(req,emit);await r.turn(req,emit)
                    self.assertEqual(len(model.ha.calls),1);self.assertTrue(events[-1]['replayed']);self.assertEqual(events[-1]['model_calls'],0)
                    await r.turn({'request_id':'voice-fast','conversation_id':'v','source':'ha','text':'Cove on'},emit)
                    self.assertEqual(events[-1]['model_calls'],0);self.assertEqual(len(model.ha.calls),2)
                finally:await r.close()
                r=Runtime(config,agent_factory=forbidden);r.home=model;home_tools.configure(model)
                try:await r.turn(req,emit);self.assertEqual(len(model.ha.calls),2);self.assertTrue(events[-1]['replayed'])
                finally:await r.close()
    async def test_cancelled_dispatch_not_replayed(self):
        from runtime import Runtime
        from config import Config
        import runtime as module
        from unittest.mock import patch
        import asyncio
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'state').mkdir();(root/'household.json').write_text('{"primary_area":"primary"}')
            with patch.object(module,'ROOT',root):
                r=Runtime(Config(fast_mode_enabled=True,fast_mode_execute=True),agent_factory=lambda:None);model=Model();r.home=model;home_tools.configure(model);sent=asyncio.Event()
                async def dispatch(*args):model.ha.calls.append(args);sent.set();await asyncio.Event().wait()
                model.ha.call_service=dispatch
                async def emit(e):pass
                req={'request_id':'cancel','conversation_id':'c','text':'Moon on'}
                try:
                    task=asyncio.create_task(r.turn(req,emit));await sent.wait();task.cancel()
                    with self.assertRaises(asyncio.CancelledError):await task
                    with self.assertRaisesRegex(ValueError,'outcomes need review'):await r.turn(req,emit)
                    self.assertEqual(len(model.ha.calls),1);self.assertFalse(r.active)
                finally:await r.close()
