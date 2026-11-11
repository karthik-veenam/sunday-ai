import ast,asyncio,io,json,sys,tempfile,time,unittest,wave,re
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,'/home/djpi/sunday')
from negative_dataset import Dataset
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.ds=Dataset(Path(self.tmp.name)/'dataset');self.record={'mode':'enforce','accepted':False,'event_id':'a'*32,'t':'2026-10-27T05:00:00+0530','text':'Not Sunday','score':.999};self.pcm=b'\x01\x00\xff\xff'*800
 def save(self):return self.ds.save(self.pcm,self.record,{'audio_ctx':250,'aliases':['sunday']})
 def test_original_pcm_and_metadata(self):
  self.assertTrue(self.save());r=self.ds.listing();self.assertEqual(r['total'],1);self.assertEqual(r['clips'][0]['review'],'unreviewed')
  with wave.open(str(self.ds.path('a'*32))) as w:self.assertEqual(w.getframerate(),16000);self.assertEqual(w.getnchannels(),1);self.assertEqual(w.getsampwidth(),2);self.assertEqual(w.readframes(w.getnframes()),self.pcm)
  db=self.ds.connect()
  try:m=json.loads(db.execute('select metadata from clips').fetchone()[0]);self.assertEqual(m['verdict'],self.record)
  finally:db.close()
 def test_duplicate_not_overwritten(self):self.assertTrue(self.save());self.assertFalse(self.save());self.assertEqual(self.ds.listing()['total'],1)
 def test_no_accepts_shadow_or_errors(self):
  for record in [{**self.record,'accepted':True},{**self.record,'mode':'shadow'},{**self.record,'error':'Timeout'}]:self.assertFalse(self.ds.save(self.pcm,record,{}))
  self.assertEqual(self.ds.listing()['total'],0)
 def test_review_filter_delete_and_audio(self):
  import base64
  self.save();self.ds.review('a'*32,'wake_word_present');self.assertEqual(self.ds.listing(review='background')['total'],0);self.assertEqual(self.ds.listing(review='wake_word_present')['total'],1);self.assertTrue(base64.b64decode(self.ds.audio('a'*32)['audio_base64']).startswith(b'RIFF'));self.ds.delete('a'*32);self.assertFalse(self.ds.path('a'*32).exists());self.assertEqual(self.ds.listing()['total'],0)
 def test_invalid_ids_labels_and_missing(self):
  for ident in ['../config','/etc/passwd',None,'g'*32]:
   with self.assertRaises(ValueError):self.ds.audio(ident)
  self.save()
  with self.assertRaises(ValueError):self.ds.review('a'*32,'accepted')
  with self.assertRaises(ValueError):self.ds.delete('b'*32)
 def test_pagination(self):
  for i in range(23):self.ds.save(self.pcm,{**self.record,'event_id':format(i,'032x')},{})
  self.assertEqual(len(self.ds.listing()['clips']),20);self.assertEqual(len(self.ds.listing(offset=20)['clips']),3)
class HookTests(unittest.IsolatedAsyncioTestCase):
 async def test_only_real_rejection_enqueues_exact_pcm(self):
  import numpy as np
  source=ast.parse(Path('/home/djpi/sunday/wake.py').read_text());cls=next(n for n in source.body if isinstance(n,ast.ClassDef) and n.name=='WakeVerifier')
  ns=dict(Path=Path,asyncio=asyncio,np=np,time=time,json=json,re=re,io=io,wave=wave)
  exec(compile(ast.Module(body=[cls],type_ignores=[]),'wake-verifier-test','exec'),ns);Verifier=ns['WakeVerifier']
  with tempfile.TemporaryDirectory() as tmp:
   v=Verifier();v.LOG=Path(tmp)/'verdicts';v.config=lambda:{'post_s':0,'aliases':['sunday']};pcm=np.arange(3200,dtype=np.int16);v.window=lambda *a:pcm
   def call(text):return lambda *a:{'text':text,'stop':'false','latency_ms':.1}
   with patch('negative_dataset.enqueue') as enqueue:
    ns['enqueue_negative']=enqueue
    v._call=call('television chatter');r=await v.verify(None,None,.999,'b'*32,'enforce');self.assertFalse(r['accepted']);self.assertEqual(enqueue.call_args.args[0],pcm.astype('<i2').tobytes());self.assertEqual(enqueue.call_count,1)
    v._call=call('Sunday');await v.verify(None,None,.999,'c'*32,'enforce');self.assertEqual(enqueue.call_count,1)
    v._call=call('television chatter');await v.verify(None,None,.999,'d'*32,'shadow');self.assertEqual(enqueue.call_count,1)
if __name__=='__main__':unittest.main()
