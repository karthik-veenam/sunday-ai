from pathlib import Path
import sys,unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'audio/gateway'))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'voice'))
import ha_mqtt as H
class Availability(unittest.TestCase):
 def test_host_controls_independent_of_body(self):
  rows=H.discovery_messages()
  for name in H.HOST_CONTROLS:
   p=next(p for _,p in rows if p['unique_id']=='sunday_body_'+name)
   self.assertEqual(p['availability_topic'],H.GATEWAY_AVAIL)
 def test_body_controls_require_both(self):
  for _,p in H.discovery_messages()+H.battery_discovery_messages():
   if p['unique_id'].removeprefix('sunday_body_') not in H.HOST_CONTROLS:
    self.assertEqual(p['availability_mode'],'all');self.assertEqual({x['topic'] for x in p['availability']},{H.AVAIL,H.GATEWAY_AVAIL})
 def test_last_will_and_online_republish(self):
  with patch('paho.mqtt.client.Client') as client:
   b=H.HaBridge(None,Mock(),'localhost',1883,Mock());client.return_value.will_set.assert_called_once_with(H.GATEWAY_AVAIL,'offline',retain=True)
   b._on_connect(client.return_value,None,None,0)
   client.return_value.publish.assert_any_call(H.GATEWAY_AVAIL,'online',retain=True)
   client.return_value.publish.assert_any_call(H.AVAIL,'offline',retain=True)
if __name__=='__main__':unittest.main()
