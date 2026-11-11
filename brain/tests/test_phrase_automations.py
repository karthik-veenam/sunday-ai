import unittest,tempfile,sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,'/home/djpi/sunday2-rebuild')
from phrase_automations import PhraseAutomations,normalize
class HA:
 actions_enabled=True
 def __init__(self):self.sent=[];self.enabled=True;self.fail=False
 async def get_states(self):return [{'entity_id':'automation.test','state':'on' if self.enabled else 'off','attributes':{'id':'one'}}]
 async def request(self,*args):
  self.sent.append(args)
  if self.fail:raise TimeoutError()
class Output:
 read_only=False;request_id='test'
 async def tool(self,n,a,f):return await f()
class Tests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.path=Path(self.tmp.name)/'automations.yaml'
  self.path.write_text("- id: one\n  use_blueprint:\n    path: sunday/when_i_say.yaml\n    input:\n      phrases: |\n        Sleep time\n        Ready for bed\n")
  self.ha=HA();self.router=PhraseAutomations(self.ha,self.tmp.name);self.out=Output()
 async def runphrase(self,text='Sleep time',source='voice'):return await self.router.attempt({'text':text,'source':source},self.out)
 async def test_dispatch(self):
  r=await self.runphrase('Sleep TIME!');self.assertTrue(r['handled']);self.assertEqual(len(self.ha.sent),1)
 async def test_negation(self):self.assertIsNone(await self.runphrase('do not sleep time'));self.assertEqual(self.ha.sent,[])
 async def test_alternative(self):self.assertTrue((await self.runphrase('Ready for bed'))['handled'])
 async def test_telegram_optin(self):self.assertIsNone(await self.runphrase(source='telegram'))
 async def test_no_recursion(self):self.assertIsNone(await self.runphrase(source='ha_compat'))
 async def test_disabled(self):self.ha.enabled=False;self.assertIsNone(await self.runphrase())
 async def test_readonly(self):self.out.read_only=True;self.assertTrue((await self.runphrase())['handled']);self.assertFalse(self.ha.sent)
 async def test_unknown_no_fallback(self):self.ha.fail=True;r=await self.runphrase();self.assertTrue(r['handled']);self.assertEqual(len(self.ha.sent),1);self.assertIn('couldn’t confirm',r['reply'])
 async def test_removed_rules(self):self.path.unlink();self.assertIsNone(await self.runphrase())
 async def test_duplicate(self):
  self.path.write_text(self.path.read_text()+self.path.read_text().replace('id: one','id: two'))
  async def states():return [{'entity_id':'automation.'+i,'state':'on','attributes':{'id':i}} for i in ('one','two')]
  self.ha.get_states=states;r=await self.runphrase();self.assertIn('more than one',r['reply']);self.assertFalse(self.ha.sent)
if __name__=='__main__':unittest.main()
