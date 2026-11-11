import asyncio,json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from device_patterns.store import Store,timing
from device_patterns.service import Service
from config import Config

class Sessions(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'p.db';self.s=Store(self.path);self.t=1793313000.;self.d={'id':'switch.test','name':'Test device','domain':'switch','mode':'reported active time'};self.s.select([self.d],self.t)
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def obs(self,value,seconds,live=True):self.s.observe(self.d['id'],value,self.t+seconds,'event'+str(seconds),live)
 def sessions(self):return [dict(x) for x in self.s.db.execute('select * from sessions order by start')]
 def test_complete_session_and_duplicate_event(self):
  self.obs('off',0,False);self.obs('on',10);self.obs('on',10);self.obs('off',70)
  rows=self.sessions();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['quality'],'complete');self.assertEqual(rows[0]['end']-rows[0]['start'],60)
 def test_baseline_on_is_partial_not_a_fabricated_start(self):
  self.obs('on',0,False);self.obs('off',60);r=self.sessions()[0];self.assertFalse(r['start_known']);self.assertEqual(r['quality'],'partial_start')
 def test_unavailability_never_becomes_a_complete_off(self):
  self.obs('off',0,False);self.obs('on',10);self.obs(None,50);self.obs('on',80,False);self.obs('off',100)
  self.assertEqual(len(self.sessions()),2);self.assertFalse(any(r['quality']=='complete' for r in self.sessions()))
 def test_snapshot_disagreement_is_not_a_verified_boundary(self):
  self.obs('off',0,False);self.obs('on',10);self.obs('off',80,False)
  self.assertEqual(self.sessions()[0]['quality'],'snapshot_gap');self.assertFalse(self.sessions()[0]['end_known'])
 def test_restart_preserves_history_and_marks_gap(self):
  self.obs('off',0,False);self.obs('on',10);self.obs('on',50);self.s.db.commit();self.s.close();self.s=Store(self.path)
  self.s.select([self.d],self.t+80,restarting=True);self.obs('on',80,False);self.obs('off',100)
  self.assertEqual([x['quality'] for x in self.sessions()],['restart_gap','partial_start'])
 def test_input_disabled_stops_collection_without_erasing_history(self):
  self.obs('off',0,False);self.obs('on',10);self.s.select([],self.t+20);self.obs('off',30)
  self.assertEqual(len(self.sessions()),1);self.assertEqual(self.sessions()[0]['quality'],'input_disabled');self.assertFalse(self.s.get(self.d['id'])['selected'])
 def test_out_of_order_events_do_not_reopen_old_sessions(self):
  self.obs('off',0,False);self.obs('on',10);self.obs('off',70);self.obs('on',20)
  self.assertEqual(len(self.sessions()),1);self.assertEqual(self.s.get(self.d['id'])['last_value'],'inactive')
 def test_climate_and_media_have_explicit_reported_session_semantics(self):
  for domain,active,inactive in [('climate','cool','off'),('media_player','playing','standby')]:
   d={'id':domain+'.test','name':domain,'domain':domain};self.s.select([self.d,d],self.t);self.s.observe(d['id'],inactive,self.t,'a',False);self.s.observe(d['id'],active,self.t+10,'b',True);self.s.observe(d['id'],inactive,self.t+100,'c',True)
   row=self.s.db.execute('select * from sessions where subject=?',(d['id'],)).fetchone();self.assertEqual(row['quality'],'complete')
 def test_presence_records_coarse_zone_visits(self):
  d={'id':'person:Alex','name':'Alex','domain':'presence'};self.s.select([d],self.t)
  for value,offset in [('HOME',0),('OFFICE',10),('HOME',100)]:self.s.observe(d['id'],value,self.t+offset,'p'+str(offset),offset>0)
  rows=self.s.db.execute('select value,quality from sessions where subject=? order by start',(d['id'],)).fetchall();self.assertEqual([tuple(x) for x in rows],[('HOME','partial_start'),('OFFICE','complete'),('HOME','open')])
 def test_numeric_observations_never_invent_on_sessions(self):
  d={'id':'sensor.temp','name':'Temperature','domain':'sensor'};self.s.select([d],self.t)
  self.s.observe(d['id'],25,self.t);self.s.observe(d['id'],27,self.t+1)
  self.assertEqual(self.s.db.execute('select count(*) from sessions').fetchone()[0],0);r=self.s.db.execute('select * from observations').fetchone();self.assertEqual((r['low'],r['high'],r['count']),(25,27,2))
 def test_partial_and_open_sessions_do_not_establish_duration_patterns(self):
  self.obs('on',0,False);self.obs('off',60);self.obs('on',100)
  self.s.analyze('first',self.t+200);p=json.loads(self.s.db.execute('select data from patterns').fetchone()[0]);self.assertEqual(p['complete_sessions'],0);self.assertIsNone(p['median_minutes']);self.assertFalse(p['strong_enough'])
 def test_cross_midnight_rollups_and_week_month_totals_preserve_evidence(self):
  from datetime import datetime
  from zoneinfo import ZoneInfo
  self.t=datetime(2026,10,31,23,58,tzinfo=ZoneInfo('Asia/Kolkata')).timestamp();self.s.select([self.d],self.t);self.obs('off',0,False);self.obs('on',60);self.obs('off',180);self.s.analyze('midnight',self.t+240)
  rows=[json.loads(x[0]) for x in self.s.db.execute("select data from rollups where kind='day' order by period")];self.assertEqual([r['active_seconds'] for r in rows],[60,60]);self.assertEqual(self.s.db.execute('select count(*) from sessions').fetchone()[0],1)
  months=[json.loads(x[0]) for x in self.s.db.execute("select data from rollups where kind='month'")];self.assertEqual(sum(x['active_seconds'] for x in months),120)
 def test_first_week_is_tentative_even_with_many_toggles(self):
  self.obs('off',0,False)
  for n in range(20):self.obs('on',n*100+1);self.obs('off',n*100+60)
  self.s.analyze('cold',self.t+4000);p=json.loads(self.s.db.execute('select data from patterns').fetchone()[0]);self.assertFalse(p['strong_enough']);self.assertFalse(p['warmup_complete'])
 def test_observed_off_days_count_in_frequency_denominator(self):
  self.obs('off',0,False);self.obs('on',100);self.obs('off',200);self.obs('off',8*86400)
  self.s.analyze('coverage',self.t+8*86400);p=json.loads(self.s.db.execute('select data from patterns').fetchone()[0]);self.assertGreaterEqual(p['covered_days'],7);self.assertGreater(sum(x['observed_days'] for x in p['weekday']),sum(x['days_used'] for x in p['weekday']))
 def test_repeat_manual_analysis_updates_summaries_without_duplicate_rows(self):
  self.obs('off',0,False);self.obs('on',10);self.obs('off',70);self.s.refresh_rollups(self.t+100)
  self.s.analyze('one',self.t+100);before=self.s.db.execute('select count(*) from rollups').fetchone()[0]
  self.s.analyze('two',self.t+200);self.assertEqual(self.s.db.execute('select count(*) from rollups').fetchone()[0],before)
  self.assertEqual(self.s.db.execute('select count(*) from sessions').fetchone()[0],1)
 def test_circular_clock_window_handles_midnight(self):
  r=timing([1430,10,5,1435]);self.assertLess(r['width_minutes'],40);self.assertTrue(r['crosses_midnight'])

class HttpTests(unittest.IsolatedAsyncioTestCase):
 async def test_authenticated_read_and_manual_analyze_only(self):
  from aiohttp.test_utils import TestClient,TestServer
  from main import create_app
  service=NS(snapshot=AsyncMock(return_value={'selected':[]}),analyze=lambda:{'status':'QUEUED'})
  runtime=NS(config=Config(daemon_token='test'),device_patterns=service,start=AsyncMock(),close=AsyncMock())
  client=TestClient(TestServer(create_app(runtime)));await client.start_server()
  try:
   self.assertEqual((await client.get('/v1/device-patterns')).status,401)
   headers={'Authorization':'Bearer test'}
   self.assertEqual((await client.get('/v1/device-patterns',headers=headers)).status,200)
   self.assertEqual((await client.post('/v1/device-patterns/analyze',json={'action':'act'},headers=headers)).status,400)
   r=await client.post('/v1/device-patterns/analyze',json={'action':'analyze'},headers=headers);self.assertEqual((await r.json())['status'],'QUEUED')
  finally:await client.close()
if __name__=='__main__':unittest.main()
