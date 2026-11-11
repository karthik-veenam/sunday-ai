"""Natural-language configuration uses the same validated rules as the dashboard."""
import json
_service=None
FIELDS={
 'name':{'type':'string','description':'Human rule name.'},'entity_ids':{'type':'array','items':{'type':'string'},'minItems':1,'maxItems':40,'uniqueItems':True},
 'attribute':{'type':'string','description':'Empty for entity state; otherwise a specific attribute, such as current_temperature.'},
 'condition':{'type':'string','enum':['change','equals','above','below'],'description':'Numeric above/below notify on crossing; change notifies on state changes.'},
 'value':{'type':['string','number','boolean'],'description':'HA state value (on/off/open/etc) or numeric threshold.'},
 'from_value':{'type':['string','number','boolean'],'description':'Optional previous state for equals/change rules; empty allows any valid previous state.'},
 'for_seconds':{'type':'integer','minimum':0,'maximum':86400},'cooldown_seconds':{'type':'integer','minimum':0,'maximum':604800},
 'message':{'type':'string','description':'Optional Telegram text with {device}, {entity}, {old}, {new}, {attribute}, {time}. No code/Jinja.'},'enabled':{'type':'boolean'}}
DEFINITIONS=[{'name':'manage_device_notifications','description':'Configure explicit device-state Telegram notifications through Sunday. Separate from learned proactivity and memory enrollment. Use only when the user asks to create/change/disable/remove notifications. List rules first to find IDs and avoid duplicates; resolve entity IDs with home_query. The current fridge/freezer opening notification appears in this list. Does not send an immediate message or operate the appliance. Use create, update, enable, disable, remove, or list; updates change only supplied fields. Ask about ambiguous targets/conditions rather than guessing.','input_schema':{'type':'object','properties':{'operation':{'type':'string','enum':['list','create','update','enable','disable','remove']},'id':{'type':'string','description':'Existing rule ID from list.'},'rule':{'type':'object','properties':FIELDS,'additionalProperties':False}},'required':['operation'],'additionalProperties':False}}]
def configure(service):
 global _service
 _service=service
async def execute(inputs):
 if _service is None:return json.dumps({'error':'Notifications are unavailable'})
 try:
  op=inputs['operation']
  if op=='list':return json.dumps(await _service.snapshot(False),default=str)
  rid=inputs.get('id');rule=inputs.get('rule') or {}
  if op!='create' and not rid:raise ValueError('List notifications and provide a rule ID')
  if op=='create' and rid:raise ValueError('Create does not take an existing ID')
  if op in {'create','update'}:body={'action':'save','rule':dict(rule,**({'id':rid} if rid else {}))}
  elif op in {'enable','disable'}:body={'action':'toggle','id':rid,'enabled':op=='enable'}
  else:body={'action':'remove','id':rid}
  # Each requested patch is applied to fresh HA state under the same service lock.
  result=await _service.manage(body,actor='Sunday conversation',require_revision=False)
  result.pop('entities',None);return json.dumps(result,default=str)
 except (ValueError,RuntimeError) as error:return json.dumps({'error':str(error),'status':'not_confirmed'})
