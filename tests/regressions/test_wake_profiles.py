import unittest,sys,tempfile,json
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'voice'))
from wake_profile import WakeProfile,CURRENT,PREVIOUS,PROFILES
class Profiles(unittest.IsolatedAsyncioTestCase):
 async def test_switches_and_enables_verifier(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'verifier.json';p.write_text(json.dumps({'enabled':False,'provider':'sherpa','aliases':['sunday']}))
   w=SimpleNamespace(model_path=PROFILES[CURRENT][0],model='current',key='c',threshold=.99,verifier=SimpleNamespace(CONFIG=p),_build_model=lambda path:('previous','p'))
   c=WakeProfile('http://unused','unused',w);c.apply(PREVIOUS);self.assertTrue(await c.apply_pending());self.assertEqual(w.model,'previous');self.assertTrue(json.loads(p.read_text())['enabled']);self.assertFalse(await c.apply_pending());c.apply(None);self.assertEqual(c.requested,PREVIOUS)
 async def test_failed_load_retains_model(self):
  def fail(path):raise RuntimeError('bad artifact')
  w=SimpleNamespace(model_path=PROFILES[CURRENT][0],model='working',key='c',threshold=.99,_build_model=fail)
  c=WakeProfile('http://unused','unused',w);c.apply(PREVIOUS)
  with self.assertRaises(RuntimeError):await c.apply_pending()
  self.assertEqual(w.model,'working');self.assertEqual(w.model_path,PROFILES[CURRENT][0])
if __name__=='__main__':unittest.main()
