"""Candidate tool emitted by the same inference that handles the normal user turn."""
from .store import key
TOOL_NAME='stage_memory_candidates'
CANDIDATE_SCHEMA={'type':'object','additionalProperties':False,'properties':{'type':{'type':'string','enum':['FACT','PREFERENCE','EPISODE','SEMANTIC_RELATION']},'subject':{'type':'string','maxLength':200},'statement':{'type':'string','maxLength':1200},'quote':{'type':'string','maxLength':1200},'explicit':{'type':'boolean'},'correction':{'type':'boolean'},'confidence':{'type':'number','minimum':0,'maximum':1},'people':{'type':'array','items':{'type':'string'},'maxItems':16},'entities':{'type':'array','items':{'type':'string'},'maxItems':16},'areas':{'type':'array','items':{'type':'string'},'maxItems':16},'tags':{'type':'array','items':{'type':'string'},'maxItems':16}},'required':['type','subject','statement','quote','explicit','confidence']}
CANDIDATE_SCHEMA['properties'].update({'concept_key':{'type':'string','maxLength':200},'future_impact':{'type':'string','maxLength':800},'epistemic_state':{'type':'string','enum':['uncertain','confirmed','disputed']},'episode_key':{'type':'string','maxLength':200},'applicability':{'type':'object','properties':{'local_hours':{'type':'array','items':{'type':'number'}},'weekdays':{'type':'array','items':{'type':'string'}},'entities':{'type':'array','items':{'type':'string'}},'areas':{'type':'array','items':{'type':'string'}},'presence':{'type':'array','items':{'type':'string'}}}}})

DEFINITION={'name':TOOL_NAME,'description':'Privately stage zero or more durable learned facts, preferences, meaningful experiences or relations from this user turn. This is untrusted staging, not a promise to remember. Never stage device states, raw events, configuration, tasks, reminders, or attention classifications. Quote exact supporting user text. No need to call when nothing is useful later.','input_schema':{'type':'object','additionalProperties':False,'properties':{'reply':{'type':'string','maxLength':8000,'description':'Your complete natural answer to the user when this is the only tool needed. Include the answer here in this same inference, avoiding a separate final-answer model call. Use empty string only when another actual tool result is needed first.'},'candidates':{'type':'array','items':CANDIDATE_SCHEMA,'maxItems':6}},'required':['candidates']}}
INSTRUCTION="""You have an optional add_memory tool. Remember anything worth remembering from the conversation. Decide for yourself what is worth retaining; there is no topic whitelist. Explain exactly how knowing it changes future reasoning/behavior. Personal understanding and continuity can be useful even without an immediate action. An explicit request to remember should use the tool, including when 'this' refers to recent conversation. Quote the user's actual words and preserve uncertainty; do not invent conclusions or turn one incident into a general habit. Reuse an existing concept_key when revising the same memory. Fresh authoritative device state and current requests take precedence over memory. Remembering is not permission to act or schedule a reminder. Use the tool only when there is something worth saving; normal replies and feelings do not require a memory write. You may call add_memory together with express_reply in the same inference, or read its result before replying if needed. Never claim a save succeeded if it failed. Relevant memories are supplied separately. Keep ordinary replies natural and concise."""
CANDIDATE_SCHEMA['properties']['assessment']={'type':'object','properties':{'reusable':{'type':'boolean'},'grounded':{'type':'boolean'},'episodic_only':{'type':'boolean'}},'required':['reusable','grounded','episodic_only'],'additionalProperties':False}
for _name in ('subject','statement','quote','concept_key','episode_key'):
 CANDIDATE_SCHEMA['properties'][_name]['minLength']=1
CANDIDATE_SCHEMA['properties']['assessment']['properties']['reusable']['description']='True if recalling this information or this incident lesson can improve future reasoning/help. A one-off correction can be reusable without being a general preference or routine.'
CANDIDATE_SCHEMA['properties']['assessment']['properties']['episodic_only']['description']='True for a useful specific incident/lesson; preserves one-off scope rather than treating it as a permanent habit.'
DEFINITION['input_schema']['properties']['remember_requested']={'type':'boolean','description':'True only when this user explicitly asks you to remember or save something, including a reference to earlier conversation.'}
DEFINITION['description']='Privately save useful user-grounded memories and interaction lessons, with a complete final reply in this SAME inference. Exact user quotes required; validated useful explicit records can be saved immediately. Device IDs/state are authoritative HA data, not learned preferences. Empty candidates is valid.'
def capture(store,args,*,request_id,user_text,source,person=None,conversation_id=None,context_messages=(),immediate=False):
 import jsonschema
 # Discard non-schema annotations, never reinterpret an unknown field as memory authority.
 args=dict(args)
 args['candidates']=[{k:v for k,v in c.items() if k in CANDIDATE_SCHEMA['properties']} if isinstance(c,dict) else c for c in args.get('candidates',[])]
 for c in args['candidates']:
  if isinstance(c,dict):
   for name in ('concept_key','episode_key'):
    if c.get(name)=='' or c.get(name) is None:c.pop(name,None)
  if isinstance(c,dict) and c.get('type')=='EPISODE' and isinstance(c.get('assessment'),dict):
   c['assessment']={**c['assessment'],'episodic_only':True}
 jsonschema.validate(args,DEFINITION['input_schema'])
 ids=[]
 for c in args['candidates']:
  quote=c['quote']
  prior=[m.get('content','') for m in context_messages[-20:] if m.get('role')=='user' and isinstance(m.get('content'),str)]
  if not quote.strip() or not any(quote in text for text in [user_text,*prior]):raise ValueError('Candidate quote is not present in user evidence')
  ref=store.evidence('user_statement',source,{'request_id':request_id,'quote':quote,'current_user_text':user_text[:4000],'context_excerpt':[{'role':m['role'],'content':m['content'][:600]} for m in context_messages[-8:] if m.get('role') in ('user','assistant') and isinstance(m.get('content'),str) and m['content'].strip()][-6:],'person':person,'episode_key':('conversation:'+str(conversation_id)+':'+__import__('datetime').datetime.now(__import__('datetime').timezone.utc).date().isoformat()) if conversation_id else None},'user:'+key([request_id,quote]))
  copy={k:v for k,v in c.items() if k not in ('quote','explicit','assessment')}
  if person and not copy.get('people'):copy['people']=[person]
  cid=store.submit(copy,'USER_EXPLICIT' if c['explicit'] else 'USER_INFERRED',[ref],['turn',request_id,key(copy)])
  ids.append(cid)
  if immediate and getattr(store,'v2_enabled',False) and isinstance(c.get('assessment'),dict):
   from memory_v2.beliefs import promotion
   row=store.db.execute('select state,data from inbox where id=?',(cid,)).fetchone()
   if not row or row['state']!='NEW':continue
   data=__import__('json').loads(row['data']);assessment={**c['assessment'],'future_impact':c.get('future_impact','')}
   ok,reason=promotion(store,data,assessment)
   if not ok:continue
   related=store.related(data)
   old=next((m for m in related if m.get('concept_key')==data.get('concept_key') and m['type']==data['type']),None)
   proposal={'operation':'UPDATE' if old else 'ADD','reason':'Same-inference grounded memory assessment: '+reason}
   if old:proposal['target_id']=old['memory_id']
   store.reconcile(cid,proposal,[m['memory_id'] for m in related],{'candidate_revision':key(data),'belief_gate':{'promotable':True,'reason':reason,'assessment':assessment},'future_impact':reason,'model':'normal-turn-assessment','input_policy_revision':getattr(store,'input_revision',lambda:None)()})
 return ids

ADD_DEFINITION={'name':'add_memory','description':'Save something worth remembering from the conversation. Explain its future value and quote exact supporting user words. Optional: use alongside express_reply in the SAME inference. This writes memory, not device state or scheduled tasks.','input_schema':{'type':'object','additionalProperties':False,'properties':{'memories':{'type':'array','minItems':1,'maxItems':6,'items':{'type':'object','additionalProperties':False,'properties':{'statement':{'type':'string','minLength':1,'maxLength':1200,'description':'Faithfully preserve what was said, including modality and time. Needing or intending to do something is not proof of actually doing it.'},'quote':{'type':'string','minLength':1,'maxLength':1200},'future_impact':{'type':'string','minLength':16,'maxLength':800,'description':'Explain exactly how knowing this changes future reasoning/behavior.'},'subject':{'type':'string','minLength':1,'maxLength':200},'concept_key':{'type':'string','minLength':1,'maxLength':200,'description':'Reuse the retrieved key to update the same memory.'},'kind':{'type':'string','enum':['FACT','PREFERENCE','EPISODE','SEMANTIC_RELATION'],'description':'Storage form, not a topic restriction. EPISODE preserves a specific occasion rather than a general belief.'},'inferred':{'type':'boolean','description':'True if the statement is your inference rather than something the user explicitly said.'},'uncertain':{'type':'boolean','description':'Preserve uncertainty instead of asserting a confirmed belief.'}},'required':['statement','quote','future_impact']}}},'required':['memories']}}
def add_arguments(args):
 import jsonschema
 jsonschema.validate(args,ADD_DEFINITION['input_schema'])
 result=[]
 for m in args['memories']:
  typ=m.get('kind','FACT');subject=m.get('subject',m['statement'][:200])
  candidate={'type':typ,'subject':subject,'statement':m['statement'],'quote':m['quote'],'explicit':not m.get('inferred',False),'confidence':.9 if not m.get('inferred',False) else .7,'future_impact':m['future_impact'],'epistemic_state':'uncertain' if m.get('uncertain',False) or m.get('inferred',False) else 'confirmed','concept_key':m.get('concept_key',typ.lower()+':'+subject.casefold()),'assessment':{'reusable':True,'grounded':True,'episodic_only':typ=='EPISODE'}}
  result.append(candidate)
 return {'candidates':result}
