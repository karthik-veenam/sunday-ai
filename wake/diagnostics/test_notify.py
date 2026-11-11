import unittest,tempfile,json
from pathlib import Path
from unittest.mock import AsyncMock,patch
import notify
class Tests(unittest.IsolatedAsyncioTestCase):
 def record(self,**kw):return {'mode':'enforce','accepted':False,'event_id':'id','text':' Someday?\n',**kw}
 async def test_enabled_rejection_sends_once(self):
  f=notify.Forwarder(None);f.toggle_on=AsyncMock(return_value=True);f.send=AsyncMock(return_value=True)
  with patch.object(notify.CONFIG.__class__,'read_text',return_value='{}'):
   await f.handle(self.record());await f.handle(self.record())
  f.send.assert_awaited_once();self.assertIn('Whisper heard: Someday?',f.send.call_args.args[1])
 async def test_off_sends_nothing(self):
  f=notify.Forwarder(None);f.toggle_on=AsyncMock(return_value=False);f.send=AsyncMock()
  with patch.object(notify.CONFIG.__class__,'read_text',return_value='{}'):await f.handle(self.record())
  f.send.assert_not_awaited()
 async def test_accepted_shadow_and_error_records_never_send(self):
  f=notify.Forwarder(None);f.toggle_on=AsyncMock();f.send=AsyncMock()
  for r in [self.record(accepted=True),self.record(mode='shadow'),{'error':'timeout','accepted':True}]:await f.handle(r)
  f.toggle_on.assert_not_awaited();f.send.assert_not_awaited()
 async def test_empty_transcription_is_reported(self):
  f=notify.Forwarder(None);f.toggle_on=AsyncMock(return_value=True);f.send=AsyncMock(return_value=True)
  with patch.object(notify.CONFIG.__class__,'read_text',return_value='{}'):await f.handle(self.record(text=''))
  self.assertIn('[no speech recognized]',f.send.call_args.args[1])
 def test_tail_skips_history_and_waits_for_complete_new_line(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'v';p.write_text(json.dumps(self.record())+'\n');tail=notify.Tail(p);self.assertEqual(tail.read(),[])
   with p.open('a') as f:f.write(json.dumps(self.record(event_id='new')))
   self.assertEqual(tail.read(),[])
   with p.open('a') as f:f.write('\n')
   self.assertEqual(tail.read()[0]['event_id'],'new');self.assertEqual(tail.read(),[])
if __name__=='__main__':unittest.main()
