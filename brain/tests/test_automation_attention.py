import copy,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from config import Config
from proactivity_v1.service import Service
from proactivity_v1.episodes import Episodes
from proactivity_v1.store import Store
from proactivity_v1.scheduler import Scheduler
from proactivity_v1.fixtures import ev,Clock,Models
class Tests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.temp=tempfile.TemporaryDirectory();self.clock=Clock();self.s=Store(Path(self.temp.name)/'p.db',self.clock);self.e=Episodes(self.s,Scheduler(self.s),exclude_automations=True)
 async def asyncTearDown(self):self.s.close();self.temp.cleanup()
 async def test_automation_start_with_unknown_initiator_excluded(self):
  event=ev();event['event_type']='AUTOMATION_TRIGGERED';self.assertIsNone(self.e.add(event));self.assertEqual(self.s.rows('temporal'),[])
 async def test_automation_device_results_excluded(self):
  for event in [ev(automation=True),ev()|{'correlation':{'automation_id':'automation.fixture'}},ev()|{'provenance':{'via':{'type':'HA_AUTOMATION'}}},ev()|{'provenance':{'evidence':[{'kind':'automation_triggered'}]}}]:self.assertIsNone(self.e.add(event))
 async def test_manual_and_unattributed_changes_preserved(self):
  for n,typ in enumerate(['USER','PHYSICAL_OR_DEVICE','UNKNOWN']):
   event=ev(str(n));event['provenance']['initiator']['type']=typ;self.assertIsNotNone(self.e.add(event))
 async def test_manual_script_preserved(self):
  event=ev();event['event_type']='SCRIPT_EXECUTED';event['subject']['id']='script.fixture';event['provenance']['initiator']['type']='USER';self.assertIsNotNone(self.e.add(event))
 async def test_late_attribution_removes_before_after(self):
  a=ev();b=ev('manual');b['subject']['id']='light.manual';i=self.e.add(a);self.e.add(b);d=self.s.get('episodes',i)['data'];d['events'][0]['provenance']['initiator']['type']='HA_AUTOMATION';filtered=self.e.filter_closed(d);self.assertEqual(len(filtered['events']),1);self.assertEqual(list(filtered['before']),['light.manual:state'])
 async def test_service_default_rejects_automation_zero_calls(self):
  models=Models();service=Service(Path(self.temp.name)/'svc.db',Config(ha_attention_enabled=True),clock=self.clock,models=models)
  try:service.on_event(ev(automation=True));self.assertEqual(service.store.rows('episodes'),[]);self.assertEqual(models.calls,[])
  finally:await service.close()
 async def test_late_enrichment_closes_without_llm(self):
  models=Models();service=Service(Path(self.temp.name)/'svc.db',Config(ha_attention_enabled=True),clock=self.clock,models=models)
  try:
   event=ev();i=service.episodes.add(event);event['provenance']['initiator']['type']='HA_AUTOMATION';row=service.store.get('episodes',i);row['data']['events']=[event];service.store.change('episodes',i,data=row['data']);self.clock.t+=10;await service.close_episode(i);self.assertEqual(service.store.get('episodes',i)['state'],'EXCLUDED_AUTOMATION');self.assertEqual(models.calls,[])
  finally:await service.close()
 async def test_explicit_opt_in_preserves_previous_behavior(self):
  e=Episodes(self.s,Scheduler(self.s),exclude_automations=False);self.assertIsNotNone(e.add(ev(automation=True)))
