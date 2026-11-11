import json,tempfile,unittest
from pathlib import Path
from alias_usage import AliasUsage
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'verdicts';self.usage=AliasUsage(self.path)
 def write(self,rows):self.path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
 def row(self,alias='sunday',event='one',at='2026-10-27T03:00:00+0530',**extra):return dict(t=at,mode='enforce',accepted=True,matched=alias,event_id=event,**extra)
 def test_actual_matches_only_deduplicated(self):
  a=self.row();shadow=self.row(event='two');shadow['mode']='shadow';rejected=self.row(event='three');rejected['accepted']=False
  self.write([a,a,shadow,rejected,self.row(alias=None,event='four'),self.row('some day','five','2026-10-27T04:00:00+0530')]);r=self.usage.snapshot();self.assertEqual(r['counts'],{'sunday':1,'some day':1});self.assertEqual(r['latest']['alias'],'some day')
 def test_latest_by_timestamp_not_order(self):
  self.write([self.row('somebody','two','2026-10-27T04:00:00+0530'),self.row()]);self.assertEqual(self.usage.snapshot()['latest']['alias'],'somebody')
 def test_append_cache_rotation_and_partial_line(self):
  self.write([self.row()]);self.assertEqual(self.usage.snapshot()['counts'],{'sunday':1})
  with self.path.open('a') as f:f.write(json.dumps(self.row(event='two'))+'\n'+ '{incomplete')
  self.assertEqual(self.usage.snapshot()['counts'],{'sunday':2});self.path.unlink();self.write([self.row('some may')]);self.assertEqual(self.usage.snapshot()['counts'],{'some may':1})
 def test_missing_is_unavailable_not_zero(self):self.assertFalse(self.usage.snapshot()['available'])
 def test_empty_and_corrupt(self):
  self.path.write_text('invalid\n[]\n{}\n');r=self.usage.snapshot();self.assertTrue(r['available']);self.assertEqual(r['counts'],{});self.assertIsNone(r['latest'])
if __name__=='__main__':unittest.main()
