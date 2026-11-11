import ast,asyncio,io,json,re,tempfile,time,unittest,wave
from pathlib import Path
from unittest.mock import Mock,AsyncMock
from types import SimpleNamespace
import numpy as np
SOURCE=ast.parse(Path('/home/djpi/sunday/wake.py').read_text())
def load(name,**extra):
 node=next(n for n in SOURCE.body if isinstance(n,ast.ClassDef) and n.name==name)
 ns=dict(Path=Path,asyncio=asyncio,np=np,time=time,json=json,re=re,io=io,wave=wave,enqueue_negative=Mock());ns.update(extra)
 exec(compile(ast.Module(body=[node],type_ignores=[]),'wake-test','exec'),ns);return ns[name],ns
class Tests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.V,self.ns=load('WakeVerifier');self.v=self.V();self.v.CONFIG=Path(self.tmp.name)/'config';self.v.CONFIG.write_text(json.dumps({'mode':'enforce','post_s':0,'aliases':['sunday'],'timeout_s':.1}));self.v.LOG=Path(self.tmp.name)/'log';self.pcm=np.arange(3200,dtype=np.int16);self.v.window=lambda *a:self.pcm;self.v.alert=Mock()
 async def verify(self):return await self.v.verify(None,None,.999,'f'*32,'enforce')
 async def test_timeout_recovers_once_same_audio(self):
  self.v._call=Mock(side_effect=[TimeoutError(),{'text':'Sunday','stop':'alias','latency_ms':1}]);r=await self.verify();self.assertTrue(r['accepted']);self.assertEqual(r['record']['attempts'],2);self.assertIs(self.v._call.call_args_list[0].args[0],self.v._call.call_args_list[1].args[0]);self.assertEqual(self.v._call.call_args_list[1].args[1]['timeout_s'],3);self.v.alert.assert_not_called()
 async def test_repeat_timeout_blocks_no_negative(self):
  self.v._call=Mock(side_effect=TimeoutError());r=await self.verify();self.assertFalse(r['accepted']);self.assertEqual(self.v._call.call_count,2);self.v.alert.assert_called_once();self.ns['enqueue_negative'].assert_not_called()
 async def test_legitimate_rejection_not_retried(self):
  self.v._call=Mock(return_value={'text':'TV chatter','stop':'false','latency_ms':1});r=await self.verify();self.assertFalse(r['accepted']);self.assertEqual(self.v._call.call_count,1);self.ns['enqueue_negative'].assert_called_once()
 async def test_bad_response_retried_then_blocked(self):
  self.v._call=Mock(return_value={'text':None});self.assertFalse((await self.verify())['accepted']);self.assertEqual(self.v._call.call_count,2)
 async def test_configuration_error_blocks(self):
  self.v.CONFIG.write_text('{bad');self.v._call=Mock();r=await self.verify();self.assertFalse(r['accepted']);self.v._call.assert_not_called()
 async def test_off_cannot_disable_requirement(self):
  self.v.CONFIG.write_text(json.dumps({'mode':'off','aliases':['sunday']}));self.assertEqual(self.v.config()['mode'],'enforce')
 async def test_cancel_never_accepts(self):
  self.v._call=Mock(side_effect=asyncio.CancelledError())
  with self.assertRaises(asyncio.CancelledError):await self.verify()
 async def test_detector_waits_for_verified_candidate(self):
  ticks=iter(range(0,100,3));D,_=load('WakeWordDetector',time=SimpleNamespace(monotonic=lambda:next(ticks)));d=D.__new__(D);d.model_path='/fake/sunday';d.threshold=.99;d._refractory_until=0;d._flush=lambda:None;d._process_chunk=lambda a:1;d.feedback=None;d.verifier=SimpleNamespace(verify=AsyncMock(side_effect=[{'accepted':False},{'accepted':True}]),config=Mock(return_value={'mode':'off'}));mic=SimpleNamespace(drain=Mock(),read=AsyncMock(side_effect=[self.pcm,self.pcm]));await d.wait_for_wake_word(mic);self.assertEqual(d.verifier.verify.await_count,2);self.assertTrue(all(c.args[-1]=='enforce' for c in d.verifier.verify.await_args_list))
if __name__=='__main__':unittest.main()
