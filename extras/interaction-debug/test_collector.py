import unittest,tempfile,json,sqlite3,hashlib
from pathlib import Path
from collector import Collector,timings,redact
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);receipts={}
  c=sqlite3.connect(self.root/'costs.db');c.executescript('create table turns(id text,started real,category text,status text,path text,model_calls int);create table calls(request_ref text,started real,model text,latency_ms real);')
  f=sqlite3.connect(self.root/'fast_mode.db');f.execute('create table attempts(id text primary key,data text)')
  a=sqlite3.connect(self.root/'world.db');a.execute('create table actions(request text,observed real,data text)')
  for i in range(4):
   rid='request'+str(i);cid=hashlib.sha256(rid.encode()).hexdigest()[:32];c.execute('insert into turns values(?,?,?,?,?,?)',(cid,100+i*100,'voice_replies','completed','fast',0));f.execute('insert into attempts values(?,?)',(rid,json.dumps({'id':rid,'input':'Turn off AC '+str(i),'decision':'EXECUTE','reply':'Done','actual_tools':[],'stage_ms':{},'turn_latency_ms':1000})));receipts[rid]={'result':{'text':'Reply '+str(i)}}
  for x in [c,f,a]:x.commit();x.close()
  (self.root/'receipts.json').write_text(json.dumps(receipts))
 def tearDown(self):self.tmp.cleanup()
 def test_latest_three_exact_correlation_and_no_db_writes(self):
  before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.glob('*.db')};result=Collector(self.root,lambda *args:[]).snapshot();self.assertEqual(len(result['interactions']),3);self.assertEqual([r['reply'] for r in result['interactions']],['Reply 3','Reply 2','Reply 1']);self.assertTrue(result['read_only']);self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.glob('*.db')})
 def test_repeat_request_uses_cache_not_journal(self):
  calls=[];c=Collector(self.root,lambda *args:calls.append(args) or []);a=c.snapshot();b=c.snapshot();self.assertIs(a,b);self.assertEqual(len(calls),3)
 def test_no_databases_returns_unavailable_without_creating_any(self):
  with tempfile.TemporaryDirectory() as tmp:
   self.assertFalse(Collector(tmp).snapshot()['interactions']);self.assertEqual(list(Path(tmp).iterdir()),[])
 def test_missing_timings_stay_unknown_not_zero(self):
  m=timings({'started':100},{'turn_latency_ms':500},[],False);self.assertEqual(m['brain_ms'],500);self.assertIsNone(m['stt_api_ms']);self.assertIsNone(m['tts_playback_ms'])
 def test_exact_transcript_timing_and_overlap_guard(self):
  rows=[{'unit':'sunday.service','at':97,'message':'[STT] Listening via Home Assistant'},{'unit':'sunday.service','at':98,'message':'[STT cleanup] {"elapsed_ms": 100, "input_samples": 16000}'},{'unit':'sunday.service','at':99,'message':'[Latency] STT request (elevenlabs): 0.8s'},{'unit':'sunday.service','at':99.9,'message':"[STT] HA transcript: 'AC off'"},{'unit':'sunday2.service','at':100.5,'message':'[Latency] first assistant text: 0.5s'},{'unit':'sunday.service','at':102,'message':'[⏱] Agent total: 2.0s'}]
  m=timings({'started':100},{'input':'AC off','turn_latency_ms':1000},rows,False);self.assertEqual(m['stt_api_ms'],800);self.assertEqual(m['first_text_ms'],500);self.assertEqual(m['recording_and_endpoint_ms'],2000);self.assertEqual(m['voice_response_through_playback_ms'],2000)
  self.assertIsNone(timings({'started':100},{'input':'different','turn_latency_ms':1000},rows,True)['first_text_ms']);self.assertIsNone(timings({'started':100},{'input':'different','turn_latency_ms':1000},rows,False)['stt_api_ms'])
 def test_credentials_redacted_but_token_counts_retained(self):
  result=redact({'api_key':'private','input_tokens':42,'nested':'Bearer abcdefghijklmnop','url':'https://api.telegram.org/bot123456789:abcdefghijklmnopqrstuv/sendMessage'});self.assertEqual(result['api_key'],'[redacted]');self.assertEqual(result['input_tokens'],42);self.assertNotIn('abcdefghijklmnop',json.dumps(result))
if __name__=='__main__':unittest.main()
