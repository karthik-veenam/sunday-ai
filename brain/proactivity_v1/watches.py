from .store import AUTH,digest
class Watches:
 def __init__(self,store,scheduler,max_self=20,max_depth=2):self.store=store;self.scheduler=scheduler;self.max_self=max_self;self.max_depth=max_depth
 def create(self,subject,condition,reason,expires,*,authority='SUNDAY_SELF',evidence=None,depth=0,commitment_ref=None):
  if authority not in AUTH or not evidence:raise ValueError('WATCH authority/evidence required')
  if authority=='USER_EXPLICIT' and not commitment_ref:raise ValueError('Explicit WATCH requires user commitment provenance')
  if not isinstance(condition,dict) or set(condition)!={'property','equals'} or condition['property'] not in {'state','presence','availability'} or isinstance(condition['equals'],(dict,list)):raise ValueError('Unsupported deterministic WATCH condition')
  if not self.store.clock()<expires<=self.store.clock()+604800 or depth>self.max_depth:raise ValueError('WATCH expiry/depth outside bound')
  equivalent=digest([subject,condition,authority,commitment_ref]);existing=next((r for r in self.store.rows('watches',('ACTIVE',),1000) if r['data']['equivalent']==equivalent),None)
  if existing:return existing['id']
  active=self.store.rows('watches',('ACTIVE',),1000)
  if authority=='SUNDAY_SELF' and sum(r['data']['authority']=='SUNDAY_SELF' for r in active)>=self.max_self:raise ValueError('Self WATCH limit')
  data={'subject':subject,'trigger_conditions':condition,'reason':str(reason)[:400],'authority':authority,'created_at':self.store.clock(),'expires_at':expires,'timeout_wake_at':expires,'evidence_refs':evidence[:16],'depth':depth,'commitment_ref':commitment_ref,'equivalent':equivalent}
  identity=self.store.put('watches',data,dedupe=[equivalent,self.store.clock()],state='ACTIVE');self.scheduler.add(expires,'WATCH_TIMEOUT',authority,{'watch_id':identity},['watch-timeout',identity]);return identity
 def close(self,identity,reason='satisfied'):
  row=self.store.get('watches',identity)
  if row and row['state']=='ACTIVE':self.store.change('watches',identity,state='CLOSED',data=row['data']|{'closure_reason':reason});return True
  return False
 def event(self,subject,property,value,evidence_ref):
  wakes=[]
  for row in self.store.rows('watches',('ACTIVE',),100):
   p=row['data']
   if self.store.clock()>=p['expires_at']:continue
   condition=p['trigger_conditions']
   if p['subject']!=subject or condition['property']!=property or condition['equals']!=value:continue
   self.close(row['id'],'condition met');authority=p['authority'];wakes.append(self.store.wake('EXTERNAL_CONDITION',authority,subject,[row['id'],evidence_ref], 'watch:'+row['id'],depth=p['depth'],commitment_refs=[p['commitment_ref']] if p['commitment_ref'] else []))
  return wakes
 def timeout(self,row):
  w=self.store.get('watches',row['data']['watch_id'])
  if not w or w['state']!='ACTIVE':return {'state':'SATISFIED'}
  p=w['data'];self.store.change('watches',w['id'],state='EXPIRED');wake=self.store.wake('SELF_RECHECK',p['authority'],p['subject'],[w['id']],'watch-timeout:'+w['id'],depth=p['depth'],commitment_refs=[p['commitment_ref']] if p['commitment_ref'] else []);return {'state':'DONE','wake_id':wake}
