import ast,asyncio,io,json,re,tempfile,time,unittest,wave
from pathlib import Path
from unittest.mock import Mock,patch
import numpy as np
SOURCE=ast.parse(Path('/home/djpi/sunday/wake.py').read_text())
def verifier():
 c=next(x for x in SOURCE.body if isinstance(x,ast.ClassDef) and x.name=='WakeVerifier');ns=dict(Path=Path,asyncio=asyncio,io=io,json=json,re=re,time=time,wave=wave,np=np,enqueue_negative=Mock());exec(compile(ast.Module(body=[c],type_ignores=[]),'isolated-verifier','exec'),ns);return ns['WakeVerifier'],ns
class Tests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);V,self.ns=verifier();self.v=V();self.v.LOG=Path(self.tmp.name)/'log';self.cfg={'provider':'sherpa','mode':'enforce','url':'http://127.0.0.1:8184/inference','aliases':['sunday','some day','someday'],'post_s':0};self.v.config=lambda:self.cfg;self.v.window=lambda *a:np.zeros(16000,np.int16);self.v.alert=Mock()
 async def test_keyword_result_used_without_faking_transcript(self):
  self.v._call=Mock(return_value={'provider':'sherpa','model':'test','text':'','accepted':True,'matched_alias':'some day','stop':'keyword','latency_ms':1})
  r=await self.v.verify(None,None,.999,'a'*32,'enforce');self.assertTrue(r['accepted']);self.assertEqual(r['record']['matched'],'some day');self.assertEqual(r['record']['text'],'');self.assertEqual(r['record']['provider'],'sherpa')
 async def test_real_no_match_is_saved_as_sherpa_negative(self):
  self.v._call=Mock(return_value={'provider':'sherpa','model':'test','text':'','accepted':False,'matched_alias':None,'stop':'no_keyword','latency_ms':1});r=await self.v.verify(None,None,.999,'a'*32,'enforce');self.assertFalse(r['accepted']);self.ns['enqueue_negative'].assert_called_once();self.assertEqual(r['record']['keyword_result'],'no_match')
 def test_malformed_or_unconfigured_keyword_is_error(self):
  cases=[{'provider':'sherpa','accepted':True,'matched_alias':'not configured'},{'provider':'sherpa','accepted':'true','matched_alias':'sunday'},{'provider':'whisper','accepted':True,'matched_alias':'sunday'}]
  for result in cases:
   response=Mock(headers={},json=Mock(return_value=result))
   with patch('requests.post',return_value=response):
    with self.assertRaises(ValueError):self.v._call(np.zeros(160,np.int16),self.cfg)
 def test_valid_whisper_still_works(self):
  response=Mock(headers={},json=Mock(return_value={'text':' Sunday'}))
  with patch('requests.post',return_value=response):self.assertEqual(self.v._call(np.zeros(160,np.int16),{'aliases':['sunday']})['text'],' Sunday')
if __name__=='__main__':unittest.main()
