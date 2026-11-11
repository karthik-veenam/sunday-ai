"""Bounded read-only factual evidence. Never an action or investigation loop."""
import json
DEFINITIONS=[{'name':'world_read','description':'Read bounded factual current state, recent normalized events or provenance. Resolve a specific entity/person first. UNKNOWN is not a known cause; temporal relations are not causality. No physical success is inferred from HA state.','input_schema':{'type':'object','properties':{'operation':{'type':'string','enum':['current','events','provenance']},'subject':{'type':'string'},'limit':{'type':'integer','minimum':1,'maximum':40},'hours':{'type':'number','minimum':.01,'maximum':336},'event_type':{'type':'string'}},'required':['operation','subject'],'additionalProperties':False}}]
async def read(world,args):
 try:
  op=args.get('operation','current');subject=args.get('subject','');limit=max(1,min(40,int(args.get('limit',10))));hours=max(.01,min(336,float(args.get('hours',24))))
  if not subject or len(subject)>150:raise ValueError('Specify one exact subject entity_id or person:Name')
  if op=='current':
   if subject in world.home.entities:await world.fresh_entities([subject])
   result={'facts':world.get_current(subject,limit)}
  elif op=='events':result={'events':world.store.events(subject,limit,hours,args.get('event_type'))}
  elif op=='provenance':result=world.explain(subject)
  else:raise ValueError('Unsupported operation')
  text=json.dumps(result,default=str)
  if len(text)>14000:
   result={'note':'Result exceeds evidence budget; narrow subject/count/time','facts':world.get_current(subject,2),'events':world.store.events(subject,1,hours)}
  if len(json.dumps(result,default=str))>14000:
   result={'note':'Evidence budget reached; inspect individual event references','facts':[{'subject':f['subject'],'property':f['property'],'value':f['value'],'availability':f['availability'],'last_event_id':f['last_event_id']} for f in world.get_current(subject,1)]}
  return result
 except (ValueError,TypeError) as e:return {'error':str(e)}
async def execute(world,args):return json.dumps(await read(world,args),default=str)
