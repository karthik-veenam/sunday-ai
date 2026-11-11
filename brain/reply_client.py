"""Native Claude Messages bridge to the existing trusted chat/tool orchestrator."""
import copy,json,time
from types import SimpleNamespace as NS
import httpx
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion,ChatCompletionChunk

def body_from_chat(args):
 systems=[];messages=[]
 for m in args['messages']:
  content=m.get('content') or ''
  text=content if isinstance(content,str) else '\n'.join(p.get('text','') for p in content)
  role=m['role']
  if role in ('system','developer'):systems.append({'type':'text','text':text});continue
  blocks=[]
  if role=='tool':role='user';blocks=[{'type':'tool_result','tool_use_id':m['tool_call_id'],'content':text}]
  else:
   if text:blocks.append({'type':'text','text':text})
   for tc in m.get('tool_calls') or []:
    blocks.append({'type':'tool_use','id':tc['id'],'name':tc['function']['name'],'input':json.loads(tc['function']['arguments'] or '{}')})
  if not blocks:continue
  if messages and messages[-1]['role']==role:messages[-1]['content'].extend(blocks)
  else:messages.append({'role':role,'content':blocks})
 if systems:systems[0]['cache_control']={'type':'ephemeral'}
 body={'model':args['model'],'system':systems,'messages':messages,'max_tokens':args.get('max_completion_tokens',4096),'thinking':{'type':'disabled'},'stream':args.get('stream',False)}
 if args.get('tools'):body['tools']=[{'name':t['function']['name'],'description':t['function'].get('description',''),'input_schema':copy.deepcopy(t['function']['parameters'])} for t in args['tools']]
 choice=args.get('tool_choice')
 if isinstance(choice,dict):body['tool_choice']={'type':'tool','name':choice['function']['name']}
 elif choice=='required':body['tool_choice']={'type':'any'}
 elif choice in ('auto','none'):body['tool_choice']={'type':choice}
 return body

def usage(u):
 cached=u.get('cache_read_input_tokens',0);written=u.get('cache_creation_input_tokens',0)
 return {'prompt_tokens':u.get('input_tokens',0)+cached+written,'completion_tokens':u.get('output_tokens',0),'total_tokens':u.get('input_tokens',0)+cached+written+u.get('output_tokens',0),'prompt_tokens_details':{'cached_tokens':cached,'cache_write_tokens':written}}
def stop(value):return {'tool_use':'tool_calls','max_tokens':'length'}.get(value,'stop')
class ClaudeStream:
 def __init__(self,response):self.response=response
 async def close(self):await self.response.aclose()
 def __aiter__(self):return self.iterate()
 async def iterate(self):
  rid='';model='';u={};finished=False;created=int(time.time())
  def chunk(delta=None,finish=None,usage_value=None):
   return ChatCompletionChunk.model_validate({'id':rid,'object':'chat.completion.chunk','created':created,'model':model,'service_tier':'default','choices':[] if usage_value is not None else [{'index':0,'delta':delta or {},'finish_reason':finish}],**({'usage':usage_value} if usage_value is not None else {})})
  try:
   async for line in self.response.aiter_lines():
    if not line.startswith('data:'):continue
    obj=json.loads(line[5:].strip());typ=obj.get('type')
    if typ=='message_start':
     msg=obj['message'];rid=msg['id'];model=msg['model'];u.update(msg.get('usage',{}))
    elif typ=='content_block_start':
     block=obj['content_block'];idx=obj['index']
     if block['type']=='tool_use':yield chunk({'tool_calls':[{'index':idx,'id':block['id'],'type':'function','function':{'name':block['name'],'arguments':''}}]})
     elif block['type']=='text' and block.get('text'):yield chunk({'content':block['text']})
    elif typ=='content_block_delta':
     delta=obj['delta']
     if delta['type']=='text_delta':yield chunk({'content':delta['text']})
     elif delta['type']=='input_json_delta':yield chunk({'tool_calls':[{'index':obj['index'],'function':{'arguments':delta['partial_json']}}]})
    elif typ=='message_delta':
     u.update(obj.get('usage',{}));yield chunk(finish=stop(obj.get('delta',{}).get('stop_reason')))
    elif typ=='message_stop':finished=True;yield chunk(usage_value=usage(u))
    elif typ=='error':raise RuntimeError('Anthropic stream failed: '+str(obj.get('error',{}).get('type','provider_error')))
   if not finished:raise RuntimeError('Anthropic stream ended before completion')
  finally:await self.close()
class ReplyClient:
 def __init__(self,openai_key,anthropic_key=''):
  self.openai=AsyncOpenAI(api_key=openai_key);self.anthropic_key=anthropic_key;self.http=None;self.chat=NS(completions=NS(create=self.create))
 async def create(self,**args):
  if not args.get('model','').startswith('claude-'):return await self.openai.chat.completions.create(**args)
  if not self.anthropic_key:raise RuntimeError('Anthropic credentials are not configured')
  if self.http is None:self.http=httpx.AsyncClient(timeout=httpx.Timeout(60,connect=10))
  body=body_from_chat(args);request=self.http.build_request('POST','https://api.anthropic.com/v1/messages',json=body,headers={'x-api-key':self.anthropic_key,'anthropic-version':'2023-06-01'})
  response=await self.http.send(request,stream=args.get('stream',False))
  if response.status_code!=200:
   await response.aclose();raise RuntimeError('Anthropic request failed: HTTP '+str(response.status_code))
  if args.get('stream'):return ClaudeStream(response)
  try:d=response.json()
  finally:await response.aclose()
  content=''.join(b.get('text','') for b in d['content'] if b['type']=='text');tools=[{'id':b['id'],'type':'function','function':{'name':b['name'],'arguments':json.dumps(b['input'])}} for b in d['content'] if b['type']=='tool_use']
  return ChatCompletion.model_validate({'id':d['id'],'model':d['model'],'object':'chat.completion','created':int(time.time()),'service_tier':'default','choices':[{'index':0,'finish_reason':stop(d['stop_reason']),'message':{'role':'assistant','content':content,'tool_calls':tools or None}}],'usage':usage(d['usage'])})
 async def close(self):
  await self.openai.close()
  if self.http:await self.http.aclose()
