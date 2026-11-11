import json
import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

from device_patterns.semantics import controller_links, session_stats
from device_patterns.store import Store


class Links(unittest.TestCase):
 def model(self,conditions):
  return NS(entities={'input_boolean.control':{'name':'User control'}},programs={'a':{'entity_id':'automation.random','domain':'automation','source':{'conditions':conditions,'actions':[{'action':'climate.turn_on','target':{'entity_id':'climate.random'}}]}}})
 def test_source_gate_maps_without_room_or_name_rules(self):
  m=self.model([{'condition':'state','entity_id':'input_boolean.control','state':'on'}]);self.assertEqual(controller_links(m,'climate.random')['entity_id'],'input_boolean.control');self.assertIsNone(controller_links(m,'climate.other'))
 def test_optional_or_gate_is_not_presumed_usage(self):
  m=self.model([{'condition':'or','conditions':[{'condition':'state','entity_id':'input_boolean.control','state':'on'}]}]);self.assertIsNone(controller_links(m,'climate.random'))
 def test_written_state_helper_is_not_user_intent(self):
  m=self.model([{'condition':'state','entity_id':'input_boolean.control','state':'on'}]);m.programs['a']['source']['actions'].append({'action':'input_boolean.turn_off','target':{'entity_id':'input_boolean.control'}});self.assertIsNone(controller_links(m,'climate.random'))
 def test_ambiguous_controllers_are_not_guessed(self):
  m=self.model([{'condition':'state','entity_id':e,'state':'on'} for e in ['input_boolean.control','input_boolean.second']]);m.entities['input_boolean.second']={'name':'Other'};self.assertIsNone(controller_links(m,'climate.random'))
 def test_world_dependency_does_not_enroll_unrelated_inputs(self):
  from world import WorldModel
  from config import Config
  w=WorldModel.__new__(WorldModel);w.config=Config();w.active=set();w.pattern_dependencies={'input_boolean.control'}
  w.home=NS(household={'primary_area':'room'},entities={e:{'entity_id':e,'domain':'input_boolean'} for e in ['input_boolean.control','input_boolean.other']},input_policy=NS(entity=lambda *a:False))
  self.assertEqual(w.selected(),{'input_boolean.control'});w.pattern_dependencies=set();self.assertEqual(w.selected(),set())


class Timing(unittest.TestCase):
 def test_different_time_groups_not_one_fifteen_hour_window(self):
  tz=ZoneInfo('Asia/Kolkata');base=datetime(2026,10,1,tzinfo=tz);items=[]
  for day in range(4):
   for hour,duration in [(8,600),(22,28800)]:
    start=(base+timedelta(days=day,hours=hour)).timestamp();items.append({'start':start,'end':start+duration})
  result=session_stats(items,tz,[]);self.assertIsNone(result['all_session_start_window']);self.assertEqual(len(result['start_clusters']),2);self.assertEqual(sorted(x['median_minutes'] for x in result['start_clusters']),[10,480]);self.assertIsNotNone(result['start_window']);self.assertEqual(result['main_daily']['median_start_minutes'],1320)
 def test_longest_daily_session_determines_start_not_many_short_uses(self):
  tz=ZoneInfo('Asia/Kolkata');base=datetime(2026,10,1,tzinfo=tz);items=[]
  for day in range(5):
   for hour,duration in [(8,120),(12,180),(22,28800)]:
    t=(base+timedelta(days=day,hours=hour)).timestamp();items.append({'start':t,'end':t+duration})
  r=session_stats(items,tz,[]);self.assertEqual(r['main_daily']['days'],5);self.assertEqual(r['main_daily']['median_start_minutes'],1320);self.assertEqual(r['main_daily']['median_minutes'],480);self.assertEqual(r['complete_sessions'],15)
 def test_main_start_dominant_group_reports_outlier_days(self):
  tz=ZoneInfo('Asia/Kolkata');base=datetime(2026,10,1,tzinfo=tz);items=[]
  for day,hour in enumerate([22,22,22,22,22,22,8,8]):
   t=(base+timedelta(days=day,hours=hour)).timestamp();items.append({'start':t,'end':t+3600})
  r=session_stats(items,tz,[]);self.assertIsNotNone(r['start_window']);self.assertEqual(r['main_daily']['supporting_days'],6);self.assertEqual(r['main_daily']['days'],8)
 def test_restarts_on_one_day_cannot_establish_common_time(self):
  tz=ZoneInfo('Asia/Kolkata');start=datetime(2026,10,1,22,tzinfo=tz).timestamp();r=session_stats([{'start':start+i,'end':start+i+60} for i in range(40)],tz,[]);self.assertIsNone(r['start_window'])
 def test_narrow_midnight_window_keeps_circular_semantics(self):
  tz=ZoneInfo('Asia/Kolkata');base=datetime(2026,10,1,tzinfo=tz);items=[]
  for day,minute in enumerate([1430,5,1435,10]):
   t=(base+timedelta(days=day,minutes=minute)).timestamp();items.append({'start':t,'end':t+3600})
  r=session_stats(items,tz,[]);self.assertTrue(r['start_window']['crosses_midnight']);self.assertLess(r['start_window']['width_minutes'],30)


class ControllerHistory(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'test.db');self.t=datetime(2026,10,1,20,tzinfo=ZoneInfo('Asia/Kolkata')).timestamp()
  self.raw={'id':'climate.test','name':'Test AC','domain':'climate','usage_subject':'usage:test','usage_control':{'entity_id':'input_boolean.test','name':'Test controller'},'usage_learning_allowed':False}
  self.source={'id':'usage:test','name':'Test controller','domain':'input_boolean','internal':True,'owner':'climate.test'}
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def row(self,state,offset):return {'state':state,'last_changed':datetime.fromtimestamp(self.t+offset,ZoneInfo('UTC')).isoformat()}
 def test_real_usage_boundaries_do_not_merge_or_erase_device_intervals(self):
  self.s.select([self.raw],self.t);self.s.observe('climate.test','off',self.t)
  for a,b in [(3600,7200),(9000,12600),(15000,18600)]:self.s.observe('climate.test','cool',self.t+a,'on',True);self.s.observe('climate.test','off',self.t+b,'off',True)
  before=list(self.s.db.execute("select * from sessions where subject='climate.test'"))
  self.s.select([self.raw,self.source],self.t+40000);self.s.observe('usage:test','off',self.t+40000)
  history=[self.row('off',0),self.row('on',3600),self.row('off',32400)]
  self.s.import_controller_history('usage:test',history,self.t,self.t+40000);self.s.analyze('first',self.t+40000)
  p=json.loads(self.s.db.execute("select data from patterns where subject='climate.test'").fetchone()[0]);self.assertEqual(p['session_basis'],'controller_enabled');self.assertEqual(p['median_minutes'],480);self.assertEqual(p['reported_intervals']['median_minutes'],60);self.assertEqual(p['reported_intervals']['complete_sessions'],3);self.assertFalse(p['learning_eligible'])
  self.assertEqual([tuple(x) for x in before],[tuple(x) for x in self.s.db.execute("select * from sessions where subject='climate.test'")])
  self.s.import_controller_history('usage:test',history,self.t,self.t+40000);self.assertEqual(self.s.db.execute("select count(*) from sessions where subject='usage:test'").fetchone()[0],1)
  self.assertEqual(len(self.s.snapshot(self.t+40000)['selected']),1)
 def test_already_active_history_is_partial_and_unknown_gaps_not_coverage(self):
  self.s.select([self.raw,self.source],self.t+100);self.s.observe('usage:test','off',self.t+100)
  self.s.import_controller_history('usage:test',[self.row('on',0),self.row('unavailable',20),self.row('on',40),self.row('off',60)],self.t,self.t+100)
  self.assertEqual(self.s.db.execute("select count(*) from sessions where subject='usage:test' and quality='complete'").fetchone()[0],0)
  coverage=self.s.db.execute("select sum(end-start) from coverage where subject='usage:test' and end is not null").fetchone()[0];self.assertEqual(coverage,80)
 def test_restart_is_rejoined_only_with_recorder_evidence(self):
  self.s.select([self.raw,self.source],self.t);self.s.observe('usage:test','off',self.t);self.s.observe('usage:test','on',self.t+10,'on',True);self.s.observe('usage:test','on',self.t+20)
  d=self.s.get('usage:test');d['history_imported']=True;self.s.put(d)
  self.s.select([self.raw,self.source],self.t+40,restarting=True);self.s.observe('usage:test','on',self.t+40)
  self.s.import_controller_history('usage:test',[self.row('on',10)],self.t+10,self.t+40);self.s.observe('usage:test','off',self.t+50,'off',True)
  rows=list(self.s.db.execute("select * from sessions where subject='usage:test'"));self.assertEqual(len(rows),1);self.assertEqual(rows[0]['quality'],'complete');self.assertEqual(rows[0]['end']-rows[0]['start'],40)
 def test_restart_history_unavailability_never_becomes_complete(self):
  self.s.select([self.raw,self.source],self.t);self.s.observe('usage:test','off',self.t);self.s.observe('usage:test','on',self.t+10,'on',True);self.s.observe('usage:test','on',self.t+15)
  d=self.s.get('usage:test');d['history_imported']=True;self.s.put(d)
  self.s.select([self.raw,self.source],self.t+40,restarting=True);self.s.observe('usage:test','on',self.t+40)
  self.s.import_controller_history('usage:test',[self.row('on',10),self.row('unavailable',20),self.row('on',30)],self.t+10,self.t+40);self.s.observe('usage:test','off',self.t+50,'off',True)
  self.assertEqual(self.s.db.execute("select count(*) from sessions where subject='usage:test' and quality='complete'").fetchone()[0],0)


if __name__=='__main__':unittest.main()
