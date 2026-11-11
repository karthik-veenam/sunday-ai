"""On-demand read-only projection of the last three existing interaction records."""
import ast,hashlib,json,re,sqlite3,subprocess,time
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path('/home/djpi/sunday2-rebuild/state')
SECRET=re.compile(r'(?i)^(?:password|secret|api_key|access_token|authorization|telegram_bot_token|token)$')
def redact(x):
 if isinstance(x,dict):return {k:('[redacted]' if SECRET.match(str(k)) else redact(v)) for k,v in x.items()}
 if isinstance(x,list):return [redact(v) for v in x[:50]]
 if isinstance(x,str):
  x=re.sub(r'https://api\.telegram\.org/bot[^/\s]+','https://api.telegram.org/bot[redacted]',x)
  x=re.sub(r'\b\d{6,}:[A-Za-z0-9_-]{20,}\b','[redacted]',x)
  x=re.sub(r'(?i)Bearer\s+[A-Za-z0-9_.-]+','Bearer [redacted]',x)
  x=re.sub(r'\b(?:sk[-_]|gsk[-_])[A-Za-z0-9_-]{16,}','[redacted]',x)
  return x[:16000]+(' … [truncated]' if len(x)>16000 else '')
 return x

def db(path):
 c=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=.025);c.row_factory=sqlite3.Row;c.execute('pragma query_only=ON');return c

def journal(start,end):
 try:
  p=subprocess.run(['nice','-n','19','journalctl','-u','sunday.service','-u','sunday2.service','--since','@'+str(start),'--until','@'+str(end),'-o','json','--output-fields=__REALTIME_TIMESTAMP,_SYSTEMD_UNIT,MESSAGE','--no-pager','-n','1200'],capture_output=True,timeout=4)
  if p.returncode:return []
  out=[]
  for line in p.stdout.splitlines():
   try:
    x=json.loads(line);m=x.get('MESSAGE','')
    if not isinstance(m,str) or len(m)>100000:continue
    if any(k in m for k in ['[STT','[Latency]','[⏱] STT','[⏱] Agent total','[Wake verifier]','[Sunday/OAI] Tool','[Sunday/OAI] Error','[Voice]']):out.append({'at':int(x['__REALTIME_TIMESTAMP'])/1e6,'unit':x.get('_SYSTEMD_UNIT'),'message':m})
   except (ValueError,KeyError,TypeError):pass
  return out
 except (OSError,subprocess.TimeoutExpired):return []

def number(rows,pattern):
 for r in rows:
  m=re.search(pattern,r['message'])
  if m:return float(m[1])
 return None

def timings(turn,fast,rows,overlap):
 start=turn['started'];duration=fast.get('turn_latency_ms');end=start+(duration or 0)/1000
 result={'brain_ms':duration,'first_text_ms':None,'recording_and_endpoint_ms':None,'stt_cleanup_ms':None,'stt_api_ms':None,'stt_total_ms':None,'voice_response_through_playback_ms':None,'tts_synthesis_ms':None,'tts_playback_ms':None,'audio_duration_ms':None,'wake_verifier_request_ms':None}
 if not overlap:
  b=[r for r in rows if r['unit']=='sunday2.service' and start-.05<=r['at']<=end+1]
  for record in b:
   v=number([record],r'first assistant text: ([0-9.]+)s')
   if v is not None and abs((record['at']-start)-v)<.35:result['first_text_ms']=v*1000;break
 # Match the actual transcript, not merely the closest unrelated STT request.
 transcript=None
 for r in rows:
  if r['unit']!='sunday.service' or abs(r['at']-start)>3 or '[STT] HA transcript:' not in r['message']:continue
  try:text=ast.literal_eval(r['message'].split('[STT] HA transcript:',1)[1].strip())
  except (ValueError,SyntaxError):continue
  if text==fast.get('input'):transcript=r
 if transcript:
  starts=[r for r in rows if r['unit']=='sunday.service' and '[STT] Listening' in r['message'] and r['at']<=transcript['at']]
  if starts:
   listening=starts[-1]['at']
   wake=[r for r in rows if r['unit']=='sunday.service' and listening-2<=r['at']<=listening and '[Wake verifier]' in r['message'] and ': ACCEPT' in r['message']]
   result['wake_verifier_request_ms']=number(wake,r' ([0-9.]+)ms')
   mic=[r for r in rows if r['unit']=='sunday.service' and listening<=r['at']<=transcript['at']+.25]
   cleanup=next((r for r in mic if '[STT cleanup]' in r['message']),None)
   if cleanup:
    try:
     meta=json.loads(cleanup['message'].split('[STT cleanup]',1)[1]);result['stt_cleanup_ms']=meta.get('elapsed_ms');result['audio_duration_ms']=meta.get('input_samples',0)/16
    except (ValueError,TypeError):pass
   stt=number(mic,r'STT request \([^)]*\): ([0-9.]+)s');result['stt_api_ms']=stt*1000 if stt is not None else None
   if result['stt_api_ms'] is not None:result['recording_and_endpoint_ms']=max(0,(transcript['at']-listening)*1000-result['stt_api_ms']-(result['stt_cleanup_ms'] or 0))
   total=number(mic,r'\[⏱\] STT: ([0-9.]+)s');result['stt_total_ms']=total*1000 if total is not None else None
  tail=[r for r in rows if r['unit']=='sunday.service' and start<=r['at']<=end+45]
  for r in tail:
   if '[STT] Listening' in r['message']:break
   v=number([r],r'Agent total: ([0-9.]+)s')
   if v is not None:result['voice_response_through_playback_ms']=v*1000;break
 return {k:round(v,1) if isinstance(v,(float,int)) else None for k,v in result.items()}

class Collector:
 def __init__(self,root=ROOT,reader=journal):self.root=Path(root);self.reader=reader;self.cache=None;self.cache_key=None;self.cache_at=0
 def snapshot(self):
  t=time.monotonic();warnings=[]
  try:
   c=db(self.root/'costs.db')
   try:turns=[dict(r) for r in c.execute("select * from turns where category in ('voice_replies','message_replies') order by started desc limit 4")]
   finally:c.close()
  except sqlite3.Error:return {'interactions':[],'warnings':['Interaction ledger temporarily unavailable.'],'read_only':True}
  key=tuple((r['id'],r['status'],r['model_calls']) for r in turns)
  if self.cache is not None and key==self.cache_key and t-self.cache_at<15:return self.cache
  try:receipts=json.loads((self.root/'receipts.json').read_text())
  except (OSError,ValueError):receipts={};warnings.append('Final reply receipts unavailable.')
  ids={hashlib.sha256(k.encode()).hexdigest()[:32]:k for k in receipts}
  result=[]
  for turn in turns[:3]:
   rid=ids.get(turn['id']);fast={};calls=[];actions=[]
   if rid:
    try:
     c=db(self.root/'fast_mode.db')
     try:
      r=c.execute('select data from attempts where id=?',(rid,)).fetchone();fast=json.loads(r[0]) if r else {}
     finally:c.close()
    except (sqlite3.Error,ValueError):warnings.append('A Fast Mode record is unavailable.')
   try:
    c=db(self.root/'costs.db')
    try:calls=[dict(r) for r in c.execute('select * from calls where started>=? and started<=? and request_ref=? order by started',(turn['started']-1,turn['started']+180,turn['id']))]
    finally:c.close()
   except sqlite3.Error:pass
   if rid:
    try:
     c=db(self.root/'world.db')
     try:actions=[{'observed_at':r['observed'],**json.loads(r['data'])} for r in c.execute('select observed,data from actions where request=? order by observed limit 40',(rid,))]
     finally:c.close()
    except (sqlite3.Error,ValueError):pass
   dur=(fast.get('turn_latency_ms') or 0)/1000
   overlap=any(o['id']!=turn['id'] and turn['started']-1<=o['started']<=turn['started']+dur for o in turns)
   rows=self.reader(turn['started']-40,turn['started']+min(190,dur+50)) if turn['category']=='voice_replies' else []
   metrics=timings(turn,fast,rows,overlap)
   receipt=receipts.get(rid,{})
   tools=fast.get('actual_tools',[])
   normalized=[]
   for tool in tools:
    entry=dict(tool)
    try:entry['result']=json.loads(entry.get('result',''))
    except (TypeError,ValueError):pass
    normalized.append(entry)
   memory_only=not tools and any('Tool stage_memory_candidates' in r['message'] for r in rows if turn['started']<=r['at']<=turn['started']+dur+1)
   stages=fast.get('stage_ms',{})
   result.append({'id':turn['id'],'request_id':rid,'started':turn['started'],'local_time':datetime.fromtimestamp(turn['started']).astimezone().isoformat(),'channel':'Voice' if turn['category']=='voice_replies' else 'Telegram','status':turn['status'],'input':fast.get('input') or 'Transcript not retained in the available record.','reply':receipt.get('result',{}).get('text') or fast.get('reply'),'path':turn['path'],'model_calls':turn['model_calls'],'decision':fast.get('decision'),'reason':fast.get('reason'),'presence':fast.get('presence'),'plan':fast.get('dispatch_plan') or fast.get('plan',[]),'candidates':fast.get('candidates',[]),'excluded':fast.get('exclude',[]),'unavailable':fast.get('unavailable',[]),'timings':metrics,'fast_stages_ms':stages,'models':[{k:r.get(k) for k in ['model','tier','started','finished','latency_ms','status','input_tokens','output_tokens','cached_tokens','cost_usd']} for r in calls],'tools':normalized,'actions':actions,'findings':(['Memory staging only; no device tool was called.'] if memory_only else [])+(['No device action was recorded.'] if not actions else [])+(['Concurrent turns: unattributed backend timing is omitted.'] if overlap else []),'timing_notes':['First text is not the time speech became audible.','Recording/end-of-speech time is inferred from matched mic log timestamps.','Voice response through playback overlaps brain processing; durations must not all be added.','Separate TTS synthesis and playback timings are not logged.']})
  packet=redact({'generated_at':time.time(),'interactions':result,'warnings':list(dict.fromkeys(warnings)),'read_only':True,'refresh':'On demand only; no continuous polling.','source':'Existing receipts, Fast Mode, cost ledger, action outcomes and bounded journal windows. No new production instrumentation.'})
  self.cache=packet;self.cache_key=key;self.cache_at=t;return packet
