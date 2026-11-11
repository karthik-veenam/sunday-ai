import unittest,json
from display_projection import Projection,validate_packet,clean
class Tests(unittest.TestCase):
 def setUp(self):
  self.now=1000.;self.p=Projection(lambda:self.now);self.p.ha_ready=True;self.p.members={'climate.ac','sensor.temp','input_number.room_target','media_player.tv','timer.room','binary_sensor.washing'};self.p.states={'input_boolean.sunday_no_listening':{'state':'off'}}
 def state(self,eid,state,attrs=None,updated='1970-01-01T00:16:40+00:00'):
  e={'entity_id':eid,'state':state,'attributes':attrs or {},'last_updated':updated};self.p.states[eid]=e;return e
 def test_mute_unknown_on_ha_disconnect(self):
  self.assertEqual(self.p.packet()['muted'],0);self.p.ha_ready=False;self.assertEqual(self.p.packet()['muted'],2)
 def test_room_actual_separate_target(self):
  self.state('sensor.temp','28.1',{'device_class':'temperature','unit_of_measurement':'C'});self.state('input_number.room_target','26.5');p=self.p.packet();self.assertIn('28.1',p['room1']);self.assertIn('26.5',p['room2'])
 def test_no_internal_ids_or_quotes_in_wire(self):
  self.p.show('Test','"x" \\ foo');p=self.p.packet();self.assertNotIn('"',p['line1']);validate_packet(p)
 def test_partial_only_after_wake_and_final_overrides(self):
  self.p.voice({'type':'partial_transcript','text':'stale'});self.assertEqual(self.p.packet()['title'],'');self.p.voice({'type':'wake'});self.p.voice({'type':'partial_transcript','text':'turn ac'});self.assertEqual(self.p.packet()['title'],'Hearing');self.p.voice({'type':'transcript','text':'turn AC off'});self.assertEqual(self.p.packet()['line1'],'turn AC off')
 def test_listening_ends_without_transcript(self):
  self.p.voice({'type':'wake'});self.p.voice({'type':'transcribing'});self.p.voice({'type':'idle'});self.assertEqual(self.p.packet()['title'],'Did not catch that')
 def test_mute_cancel_is_not_transcription_failure(self):
  self.p.voice({'type':'wake'});self.p.states['input_boolean.sunday_no_listening']['state']='on';self.p.voice({'type':'idle'});self.assertEqual(self.p.packet()['title'],'')
 def test_unsupported_script_not_shown_as_truncated_english_fragment(self):
  self.p.voice({'type':'transcript','text':'ఫ్యాన్ fan on'});self.assertEqual(self.p.packet()['line1'],'Transcript received')
 def test_live_words_take_priority_over_background_action_feedback(self):
  self.p.voice({'type':'wake'});self.p.action({'targets':['climate.ac'],'status':'failed'},self.now);self.p.voice({'type':'partial_transcript','text':'my room'});self.assertEqual(self.p.packet()['title'],'Hearing')
 def test_new_wake_replaces_previous_failure(self):
  self.p.show('Failure','old',10,4);self.p.voice({'type':'wake'});self.assertEqual(self.p.packet()['title'],'Listening')
 def test_service_completed_not_physical_success(self):
  self.state('climate.ac','off',{'friendly_name':'Bedroom AC'});self.p.action({'targets':['climate.ac'],'service':'turn_on','status':'service_completed'},self.now);self.assertEqual(self.p.packet()['title'],'Command sent')
 def test_state_feedback_can_be_reported_updated(self):
  self.state('climate.ac','off',{'friendly_name':'Bedroom AC'});self.p.action({'targets':['climate.ac'],'service':'turn_off','status':'service_completed','changed_entities':['climate.ac']},self.now);self.assertEqual(self.p.packet()['title'],'Updated')
 def test_recent_state_without_receipt_correlation_not_confirmation(self):
  self.state('climate.ac','off',{'friendly_name':'AC'});self.p.action({'targets':['climate.ac'],'service':'turn_off','status':'service_completed'},self.now);self.assertEqual(self.p.packet()['title'],'Command sent')
 def test_climate_on_accepts_cool_state_only_with_receipt(self):
  self.state('climate.ac','cool',{'friendly_name':'AC','temperature':24});self.p.action({'targets':['climate.ac'],'service':'turn_on','status':'service_completed','changed_entities':['climate.ac']},self.now);self.assertEqual(self.p.packet()['title'],'Updated');self.assertIn('24 C',self.p.packet()['line1'])
 def test_stale_matching_state_is_not_confirmation(self):
  self.state('climate.ac','off',{},'1970-01-01T00:00:00+00:00');self.p.action({'targets':['climate.ac'],'service':'turn_off','status':'service_completed'},self.now);self.assertEqual(self.p.packet()['title'],'Command sent')
 def test_failure_feedback_includes_other_rooms(self):
  self.state('climate.other','off',{'friendly_name':'Hall AC'});self.p.action({'targets':['climate.other'],'status':'failed'},self.now);self.assertEqual(self.p.packet()['title'],'Did not complete');self.assertIn('Hall AC',self.p.packet()['line1'])
 def test_cancelled_timer_does_not_alert(self):
  old={'state':'active','attributes':{'finishes_at':'1970-01-01T00:30:00+00:00'}};new={'state':'idle','attributes':{}};self.p.changed('timer.room',old,new);self.assertEqual(self.p.alert,('',''))
 def test_expired_timer_alert(self):
  old={'state':'active','attributes':{'finishes_at':'1970-01-01T00:16:40+00:00'}};self.p.changed('timer.room',old,{'state':'idle','attributes':{'friendly_name':'Tea'}});self.assertEqual(self.p.alert,('Tea','Timer finished'))
 def test_nearly_done_timer_expands_only_once(self):
  self.p.jobs=[{'id':'a','kind':'timer','status':'pending','due':1008}];self.assertEqual(self.p.packet()['title'],'Timer almost done');self.now+=6;self.assertEqual(self.p.packet()['title'],'')
 def test_ha_timer_start_expands(self):
  self.p.changed('timer.room',{'state':'idle'},{'state':'active','attributes':{'friendly_name':'Tea'}});self.assertEqual(self.p.packet()['title'],'Timer started')
 def test_multiple_timers_nearest_first_and_paused(self):
  self.p.jobs=[{'id':'a','kind':'timer','status':'pending','due':1060},{'id':'b','kind':'timer','status':'pending','due':1020}];p=self.p.packet();self.assertEqual(p['timer_seconds'],20);self.assertIn('+1 more',p['timer1']);self.now+=10;self.assertEqual(self.p.packet()['timer_seconds'],10)
 def test_overlay_expires(self):
  self.p.show('Hi','Text',3);self.now+=4;self.assertEqual(self.p.packet()['ttl_ms'],0)
 def test_sleep_reason_and_media(self):
  self.p.sleep={'state':'Asleep','sleepiness':72,'reason':'Forced Night Mode is on.'};self.state('media_player.tv','paused',{'media_title':'Loki'});p=self.p.packet();self.assertIn('72%',p['sleep1']);self.assertEqual(p['media1'],'Loki');self.assertIn('Paused',p['media2'])
 def test_html_notification_cannot_break_snapshot_worker(self):
  self.p.notify('Notice','&lt;b&gt;Tea &amp; coffee&lt;/b&gt;');p=self.p.packet();self.assertEqual(p['line1'],'Tea & coffee');validate_packet(p)
 def test_wire_rejects_bad_fields(self):
  p=self.p.packet();p['muted']=True
  with self.assertRaises(ValueError):validate_packet(p)
  p=self.p.packet();p['line1']='bad"text'
  with self.assertRaises(ValueError):validate_packet(p)
 def test_offline_ha_does_not_claim_fresh_media(self):
  self.state('media_player.tv','playing',{'media_title':'Loki'});self.p.ha_ready=False;self.assertEqual(self.p.packet()['media1'],'Media status unavailable')
if __name__=='__main__':unittest.main()
