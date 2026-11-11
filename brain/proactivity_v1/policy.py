"""Validate structured cognition, then independently gate every proposed effect."""
import math,re
from .store import AUTH,digest,js
OPS={'IGNORE','REMEMBER','WATCH','INVESTIGATE','ACT','TELL_USER','ASK_USER'}
FIELDS={'operation','reason','confidence','message','destination','action','candidate','condition','expires_at','query','already_known','consequence_of_silence','time_sensitive','useful_now','can_resolve_silently','interruption_cost','subject'}
def validate_output(data):
 if not isinstance(data,dict) or set(data)!={'outcomes','reason'} or not isinstance(data['reason'],str) or len(data['reason'])>1000:raise ValueError('Invalid cognition envelope')
 outcomes=data['outcomes']
 if not isinstance(outcomes,list) or not 1<=len(outcomes)<=4:raise ValueError('Invalid outcomes bound')
 ops=[]
 for p in outcomes:
  if not isinstance(p,dict) or set(p)-FIELDS or p.get('operation') not in OPS or not isinstance(p.get('reason'),str) or not p['reason'].strip() or len(p['reason'])>1000:raise ValueError('Invalid cognition outcome')
  op=p['operation'];ops.append(op)
  c=p.get('confidence',0)
  if isinstance(c,bool) or not isinstance(c,(int,float)) or not math.isfinite(c) or not 0<=c<=1:raise ValueError('Invalid confidence')
  if op in {'TELL_USER','ASK_USER'}:
   if not isinstance(p.get('message'),str) or not 1<=len(p['message'].strip())<=1200 or p.get('destination') not in ('telegram','voice','dashboard'):raise ValueError('Invalid communication proposal')
  if op=='ACT' and (not isinstance(p.get('action'),dict) or set(p['action'])-{'domain','service','entity_ids','data'}):raise ValueError('Invalid generic action proposal')
  if op=='REMEMBER' and (not isinstance(p.get('candidate'),dict) or p['candidate'].get('type') not in {'FACT','PREFERENCE','EPISODE','SEMANTIC_RELATION'}):raise ValueError('Invalid Inbox candidate')
  if op=='WATCH' and (not isinstance(p.get('condition'),dict) or not isinstance(p.get('expires_at'),(int,float))):raise ValueError('Invalid WATCH proposal')
  if op=='INVESTIGATE' and (not isinstance(p.get('query'),dict) or p['query'].get('operation') not in {'world','history','presence_history','duration','evidence','action_receipts'}):raise ValueError('Investigation not allowed')
 if len(set(ops))!=len(ops) or 'IGNORE' in ops and len(ops)>1 or {'TELL_USER','ASK_USER'}<=set(ops) or {'ACT','ASK_USER'}<=set(ops):raise ValueError('Unsupported outcome combination')
 return outcomes
class Gates:
 def __init__(self,store,config,capabilities=lambda action:False):self.store=store;self.config=config;self.capabilities=capabilities
 def check(self,wake,proposal,context):
  op=proposal['operation'];authority=wake.get('authority');reason='';valid=True
  if authority not in AUTH:valid=False;reason='Unknown authority'
  elif not wake.get('evidence_refs'):valid=False;reason='Missing evidence references'
  elif not context.get('fresh') or not context.get('healthy'):valid=False;reason='Fresh factual/provider evidence unavailable'
  elif proposal.get('confidence',0)<.7:valid=False;reason='Insufficient supported confidence'
  elif op in ('TELL_USER','ASK_USER'):
   if proposal.get('already_known') is True or context.get('already_known'):valid=False;reason='User already knows'
   elif proposal.get('can_resolve_silently') is True:valid=False;reason='Resolve silently before interrupting'
   elif proposal.get('useful_now') is not True or not proposal.get('consequence_of_silence'):valid=False;reason='Usefulness/consequence not established'
   elif proposal.get('interruption_cost') not in ('low','medium','high'):valid=False;reason='Interruption cost not assessed'
   elif proposal.get('interruption_cost')=='high' and not (proposal.get('time_sensitive') is True and authority=='USER_EXPLICIT'):valid=False;reason='Interruption cost exceeds established authority/urgency'
   elif context.get('quiet') and not (authority=='USER_EXPLICIT' and proposal.get('time_sensitive') is True):valid=False;reason='Quiet hours'
  elif op=='ACT':
   action=proposal['action'];ids=action.get('entity_ids')
   if not isinstance(ids,list) or not 1<=len(ids)<=8 or any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids) or not isinstance(action.get('data',{}),dict) or set(action.get('data',{})) & {'entity_id','device_id','area_id','target'} or not self.capabilities(action):valid=False;reason='Target/capability unavailable or ambiguous'
   elif context.get('outcome_unknown'):valid=False;reason='Unknown consequential outcome; never blindly retry'
   elif context.get('action_authorized') is not True:valid=False;reason='No validated target-specific action scope; authority alone is not action permission'
  if valid and op in ('TELL_USER','ASK_USER') and context.get('v2_incident'):
   incident=context['v2_incident'];assessments=incident.get('assessments',[])
   if incident.get('notification_state')=='DELIVERED':valid=False;reason='Ongoing situation already communicated'
   elif not assessments or assessments[-1].get('outcome') not in {'TELL','URGENT'}:valid=False;reason='Situation did not cross V2 attention threshold'
  return valid,reason
 def evaluate(self,wake,proposal,context,decision_ref=None):
  op=proposal['operation'];authority=wake.get('authority');valid,reason=self.check(wake,proposal,context)
  # One underlying incident can produce at most one communication proposal across channels/restarts.
  equivalent=[wake.get('incident_id'), 'communication' if op in ('TELL_USER','ASK_USER') else op]
  previous=self.store.get('proposals','proposals:'+digest(equivalent))
  if previous:return {'result':'SUPPRESSED_DUPLICATE','reason':'Equivalent incident proposal already recorded','proposal_id':previous['id']}
  flag={'TELL_USER':'proactive_tell_enabled','ASK_USER':'proactive_ask_enabled','ACT':'proactive_act_enabled'}[op]
  enabled=bool(getattr(self.config,flag,False));result=('ELIGIBLE_REQUIRES_TRUSTED_ADAPTER' if enabled else 'WOULD_'+{'TELL_USER':'TELL','ASK_USER':'ASK','ACT':'ACT'}[op]) if valid else 'SUPPRESSED'
  data={'wake':wake,'operation':op,'proposal':proposal,'authority':authority,'evidence_refs':wake.get('evidence_refs',[]),'gate':{'result':result,'reason':reason or ('Independent effect flag disabled; shadow only' if not enabled else 'Policy permits consideration; no dispatch adapter installed')},'equivalent':digest(equivalent),'external_effect':False,'context_fresh':context.get('fresh'),'provider_healthy':context.get('healthy')}
  generation=self.store.meta('telegram_release_generation')
  data.update(decision_ref=decision_ref,release_generation=generation if valid and enabled else None,gate_context={k:context[k] for k in ('fresh','healthy','quiet','already_known','world','input_policy_revision') if k in context})
  if valid and enabled and op in ('TELL_USER','ASK_USER') and generation:
   result='GATE_APPROVED';data['gate']={'result':result,'reason':'Communication policy approved; trusted Telegram pre-send revalidation required'}
  identity=self.store.put('proposals',data,dedupe=equivalent,state=result)
  if result=='GATE_APPROVED':
   approval=self.store.put('approvals',{'proposal_id':identity,'incident_id':wake.get('incident_id'),'decision_ref':decision_ref,'decision_hash':digest(self.store.get('decisions',decision_ref)['data']) if decision_ref and self.store.get('decisions',decision_ref) else None,'message_hash':digest(proposal['message']),'proposal_hash':digest(proposal),'wake_hash':digest(wake),'evidence_refs':wake.get('evidence_refs',[]),'release_generation':generation,'expires_at':self.store.clock()+180,'context':data['gate_context']},dedupe=identity,state='APPROVED')
   current=self.store.get('proposals',identity);self.store.change('proposals',identity,data=current['data']|{'gate_decision_id':approval})
  return data['gate']|{'proposal_id':identity}
