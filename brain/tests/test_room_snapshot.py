import unittest,json
from unittest.mock import AsyncMock
from types import SimpleNamespace as NS
from home_model import HomeModel
class Tests(unittest.IsolatedAsyncioTestCase):
 async def test_registry_before_states_recovers_without_rediscovery(self):
  entity='sensor.room_air';ha=NS(ws_command=AsyncMock(side_effect=[ [{'area_id':'bedroom','name':'Room'}],[{'id':'d','area_id':'bedroom'}],[{'entity_id':entity,'device_id':'d','original_name':'Temperature','original_device_class':'temperature','platform':'zha'}] ]),get_states=AsyncMock(side_effect=[[],[{'entity_id':entity,'state':'26.7','attributes':{'device_class':'temperature','unit_of_measurement':'°C'},'last_updated':'now'}]]))
  m=HomeModel(ha,{'primary_area':'bedroom'});await m.refresh();self.assertIn(entity,m.entities);self.assertFalse(m.entities[entity]['available']);self.assertEqual(m.room_snapshot()['environment']['temperature']['status'],'temporarily_unavailable')
  await m.refresh_current_states();reading=m.room_snapshot()['environment']['temperature'];self.assertEqual(reading['status'],'available');self.assertEqual(reading['readings'][0]['value'],26.7);self.assertEqual(ha.ws_command.await_count,3)
 def test_ambiguity_and_invalid_values_are_explicit(self):
  m=HomeModel(None,{'primary_area':'bedroom'});m.entities={}
  for i,state in enumerate(['25','27','nan']):m.entities[str(i)]={'entity_id':str(i),'domain':'sensor','area_id':'bedroom','state':state,'available':True,'attributes':{'device_class':'temperature','unit_of_measurement':'°C'}}
  snap=m.room_snapshot()['environment']['temperature'];self.assertEqual(snap['status'],'multiple_sources');self.assertEqual(len(snap['readings']),2)
