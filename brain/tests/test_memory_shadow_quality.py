import json,tempfile,unittest
from pathlib import Path
from memory_v1.store import Store,key
from memory_v1.service import MemoryService
from config import Config

class ShadowQuality(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'memory.db';self.s=Store(self.path)
  ev=self.s.evidence('user_statement','test',{'quote':'Alex visited family for the festival.'})
  cid=self.s.submit({'type':'EPISODE','subject':'Alex family trip','statement':'Alex visited family for the festival.','confidence':.99,'people':['Alex'],'tags':['festival','travel']},'USER_EXPLICIT',[ev],'trip')
  self.mid=self.s.reconcile(cid,{'operation':'ADD','reason':'Explicit statement'},[])['affected_memory_ids'][0]
 def tearDown(self):self.s.close();self.temp.cleanup()
 def test_ambient_person_does_not_select_unrelated_episode(self):
  for i,q in enumerate(('How is my room looking?','Turn all devices off','Hey Sunday','What is the capital of Japan?')):
   self.assertEqual(self.s.shadow_retrieve(q,str(i),people=['Alex']),[])
   trace=self.s.inspect('retrievals')[0]['data']['trace'];self.assertEqual(trace['suppressed_person_only'],1)
 def test_relevant_query_still_selects_with_provenance(self):
  result=self.s.shadow_retrieve('family festival','relevant',people=['Alex'])
  self.assertEqual([m['memory_id'] for m in result],[self.mid])
  entry=self.s.inspect('retrievals')[0];trace=entry['data']['trace']['selected'][0]
  self.assertEqual(entry['shadow'],1);self.assertFalse(entry['data']['injection_enabled'])
  self.assertEqual(trace['content_sha256'],key(result[0]));self.assertIsInstance(trace['version_id'],int)
  self.assertEqual(trace['lexical_overlap'],2);self.assertEqual(trace['evidence_refs'],result[0]['evidence_refs'])
 def test_explicit_person_query_and_structural_consolidation_preserved(self):
  self.assertEqual(len(self.s.shadow_retrieve('What about Alex?','named',people=['Alex'])),1)
  self.assertEqual(len(self.s.related({'statement':'paraphrase','people':['Alex']})),1)
 def test_low_confidence_remains_excluded(self):
  m=self.s.get(self.mid);m['confidence']=.2
  with self.s.db:self.s._put(m,'TEST')
  self.assertEqual(self.s.shadow_retrieve('family festival','weak',people=['Alex']),[])
 def test_trace_remains_immutable_after_later_memory_update(self):
  self.s.shadow_retrieve('family festival','before',people=['Alex']);before=self.s.inspect('retrievals')[0]['data']['trace']['selected'][0]
  m=self.s.get(self.mid);m['statement']='Alex returned home.'
  with self.s.db:self.s._put(m,'TEST')
  self.assertEqual(self.s.inspect('retrievals')[0]['data']['trace']['selected'][0],before)
  self.assertNotEqual(before['content_sha256'],key(self.s.get(self.mid)))
 def test_global_read_gate_still_empty(self):
  self.s.close();service=MemoryService(self.path,Config(memory_write_enabled=True,memory_read_enabled=False))
  self.assertEqual(service.turn_context('family festival','normal',person='Alex'),'')
  self.assertEqual(service.store.inspect('retrievals')[0]['shadow'],1);service.close();self.s=Store(self.path)
