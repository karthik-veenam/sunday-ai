import json,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock
from config import Config
from memory_v1.service import MemoryService
from output import TurnOutput,current_output
from agent import _capture_definitions,_learning_tool_choice,_exec_tool,_capture_reply
from reply_client import body_from_chat
from alive.engine import fresh,apply,project
from alive.service import Event

class LearningTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'memory.db'
  self.cfg=Config(memory_v2_enabled=True,memory_write_enabled=True,memory_read_enabled=True,conversation_learning_enabled=True)
  self.s=MemoryService(self.path,self.cfg)
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def candidate(self,**kw):
  c=dict(type='PREFERENCE',subject='bedroom lighting choice',statement='When any bedroom light is requested, prefers Light 1 over Panels.',quote='Remember: pick Light 1 rather than Panels when I ask for any light.',explicit=True,confidence=.95,concept_key='bedroom.any_light',epistemic_state='confirmed',future_impact='Choose Light 1 for a discretionary bedroom lighting request.',tags=['light','lighting','bedroom'],assessment=dict(reusable=True,grounded=True,episodic_only=False));c.update(kw);return c
 def save(self,c,text=None,history=(),ref='r'):
  return self.s.capture({'candidates':[c]},request_id=ref,user_text=text or c['quote'],source='voice',person='Karthik',conversation_id='test',context_messages=history)
 def test_explicit_remember_persists_and_retrieves_after_restart(self):
  ids=self.save(self.candidate());self.assertEqual(self.s.capture_status(ids),['ACCEPTED'])
  self.s.close();self.s=MemoryService(self.path,self.cfg)
  ctx=self.s.turn_context('Turn on any bedroom light','q',person='Karthik')
  self.assertIn('prefers Light 1',ctx);self.assertIn('evidence',ctx)
  self.assertEqual(self.s.store.inspect('retrievals')[0]['shadow'],0)
 def test_remember_this_can_reference_user_history_not_assistant_invention(self):
  c=self.candidate();self.save(c,'Remember this.',[{'role':'user','content':c['quote']}])
  with self.assertRaises(ValueError):self.save(self.candidate(quote='My made-up preference'),'Remember this.',[{'role':'assistant','content':'My made-up preference'}],ref='invalid')
 def test_one_episode_recall_does_not_promote_inferred_habit(self):
  c=self.candidate(type='EPISODE',subject='lighting correction on this occasion',statement='On this occasion Karthik rejected Panels and requested Light 1 or Light 2.',quote='Not Panels. Light 1 or Light 2, please.',concept_key='lighting.correction.episode1',assessment=dict(reusable=True,grounded=True,episodic_only=True))
  self.save(c);self.assertIn('On this occasion',self.s.turn_context('bedroom lighting','episode'))
  self.assertIn('not a general preference',self.s.turn_context('bedroom lighting','episode2'))
  self.save(self.candidate(explicit=False,statement='Karthik always wants Light 1.'),ref='inference')
  self.assertEqual(len(self.s.store.inspect('memories')),1)
 def test_optional_empty_and_extra_annotations_do_not_drop_valid_memory(self):
  c=self.candidate(episode_key='',subject_detail=None)
  self.save(c);self.assertEqual(len(self.s.store.inspect('memories')),1)
 def test_no_usefulness_and_read_disable_remain_enforced(self):
  self.save(self.candidate(future_impact='',assessment=dict(reusable=False,grounded=True,episodic_only=False)))
  self.assertFalse(self.s.store.inspect('memories'))
  self.cfg.memory_read_enabled=False;self.assertEqual(self.s.turn_context('lighting','off'),'')
 def test_correction_updates_one_concept_without_duplicate(self):
  self.save(self.candidate());self.save(self.candidate(statement='Prefers Light 2 for discretionary bedroom lighting.',quote='Actually remember Light 2 instead.',correction=True),ref='correction')
  rows=self.s.store.inspect('memories');self.assertEqual(len(rows),1);self.assertIn('Light 2',rows[0]['data']['statement']);self.assertEqual(len(self.s.store.inspect('versions')),2)

class DeliveryTests(unittest.IsolatedAsyncioTestCase):
 async def test_add_memory_only_result_is_visible_to_followup_inference(self):
  from types import SimpleNamespace as NS
  from unittest.mock import patch
  from agent import _OpenAIBackend
  b=_OpenAIBackend('fake-key');b._tools_telegram=[]
  o=TurnOutput('r',AsyncMock());o.memory_learning=True;o.memory_capture=lambda a:['saved'];o.memory_capture_status=lambda ids:['ACCEPTED'];o.home_context=''
  args={'memories':[{'statement':'An ongoing situation matters to the user.','quote':'An ongoing situation matters to me.','future_impact':'Understand future references without asking for the whole explanation again.'}]}
  tc=lambda name,args: NS(id=name,function=NS(name=name,arguments=json.dumps(args)))
  first=NS(usage=None,choices=[NS(message=NS(content=None,tool_calls=[tc('add_memory',args)]))])
  second=NS(usage=None,choices=[NS(message=NS(content=None,tool_calls=[tc('express_reply',{'reply':'Saved.'})]))])
  responses=iter((first,second));seen=[]
  async def create(**kw):seen.append(kw);return next(responses)
  b._client.chat.completions.create=create;token=current_output.set(o)
  try:
   self.assertEqual(await b.respond_as_text('An ongoing situation matters to me.'),'Saved.')
   self.assertIn('"status": "saved"',str(seen[1]['messages']))
   self.assertNotIn('add_memory',str(b.history))
  finally:current_output.reset(token);await o.close();await b._client.close()
 async def test_optional_add_memory_uses_existing_store_and_final_reply_without_forced_write(self):
  from memory_v1.capture import add_arguments
  from agent import _without_capture_history
  args={'memories':[{'statement':'The user is working through a continuing challenge.','quote':'I am working through a continuing challenge.','future_impact':'Understand references to this ongoing situation in future conversations.'}]}
  converted=add_arguments(args);self.assertEqual(converted['candidates'][0]['type'],'FACT')
  self.assertEqual(converted['candidates'][0]['assessment'],dict(reusable=True,grounded=True,episodic_only=False))
  history=[{'role':'assistant','tool_calls':[{'id':'m','function':{'name':'add_memory','arguments':'{}'}}]},{'role':'tool','tool_call_id':'m','content':'{"status":"saved"}'}]
  self.assertEqual(_without_capture_history(history),[])
  self.assertEqual(_without_capture_history(history,keep_memory_tools=True),history)

 async def test_required_same_inference_path_and_claude_translation(self):
  o=TurnOutput('r',AsyncMock());o.memory_capture=lambda a:[];o.memory_learning=True;o.alive_capture_enabled=True;o.alive_hints=True
  token=current_output.set(o)
  try:
   definitions=_capture_definitions([]);self.assertEqual([d['name'] for d in definitions],['add_memory','express_reply'])
   self.assertEqual(definitions[0]['input_schema']['required'],['memories'])
   schema=definitions[1]['input_schema'];self.assertIn('emotion',schema['required']);self.assertIn('reply',schema['required'])
   self.assertEqual(schema['properties']['emotion']['anyOf'][0]['properties']['reason_ref']['enum'],['current_turn'])
   self.assertEqual(_learning_tool_choice(),{'tool_choice':'required'})
   self.assertEqual(body_from_chat({'messages':[],'model':'claude-test','tool_choice':'required'})['tool_choice'],{'type':'any'})
  finally:current_output.reset(token);await o.close()
 async def test_own_mistake_reaches_current_face_voice_and_persistent_day_mood(self):
  events=[];emitted=[]
  async def emit(e):emitted.append(e)
  o=TurnOutput('r',emit);o.alive_hints=True;o.alive_capture_enabled=True;o.audio_tags_enabled=True;o.alive_capture=events.append
  o.capture_performance({'emotion':dict(affect='disappointment',strength=.9,confidence=.95,reason_ref='current_turn',cause='own_mistake'),'performance':dict(expression='Happy'),'voice_delivery':{'tags':['happy']}})
  await o.speak('I made that harder than it should have been.')
  self.assertEqual(emitted[0]['performance']['expression'],'Sad');self.assertIn('[sad]',emitted[0]['tts_text'])
  state=apply(fresh(100),Event('affect:r','affect','r',inference=events[0]),0,100)
  self.assertEqual(project(state,100)['resting_expression'],'Sad');self.assertEqual(state['meters']['connection'],.72)
  self.assertEqual(state['day_mood']['causes'][0]['cause'],'own_mistake');await o.close()
 async def test_moderate_grounded_disappointment_remains_visible_and_explainable(self):
  affect=dict(affect='disappointment',strength=.4,confidence=.8,reason_ref='r',cause='own_mistake',explanation='I made him repeat a simple request.')
  state=apply(fresh(100),Event('affect:r','affect','r',inference=affect),0,100)
  from alive.engine import advance
  state=advance(state,60,160)
  self.assertEqual(project(state,160)['resting_expression'],'Sad')
  self.assertEqual(state['day_mood']['causes'][0]['explanation'],'I made him repeat a simple request.')
 async def test_failed_remember_does_not_claim_success(self):
  o=TurnOutput('r',AsyncMock());o.memory_capture=lambda args:(_ for _ in ()).throw(ValueError('rejected'))
  token=current_output.set(o)
  try:
   args={'remember_requested':True,'reply':"I'll remember that.",'candidates':[]}
   await _exec_tool('stage_memory_candidates',args,'Remember this')
   self.assertIn("couldn't save",_capture_reply([('stage_memory_candidates',args)]))
  finally:current_output.reset(token);await o.close()
if __name__=='__main__':unittest.main()
