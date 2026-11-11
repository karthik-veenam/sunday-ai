import unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch,AsyncMock
import speech_mode_tools as m
from output import TurnOutput,current_output
class SpeechModeTests(unittest.IsolatedAsyncioTestCase):
 async def test_on_off_persist_and_preserve(self):
  with tempfile.TemporaryDirectory() as d,patch.object(m,'CONFIG',Path(d)/'mode.json'):
   m.CONFIG.write_text(json.dumps({'version':1,'enabled':True,'other':7}))
   self.assertTrue((await m.execute({'action':'on'}))['telugu_mode'])
   self.assertEqual(m.status()['recognition'],'after_speech');self.assertEqual(m.status()['language_hint'],'tel')
   self.assertIn('true',m.context());self.assertEqual(m.read()['other'],7)
   self.assertFalse((await m.execute({'action':'off'}))['telugu_mode'])
   self.assertEqual(m.status()['recognition'],'live')
 async def test_status_never_writes_and_invalid_action(self):
  with tempfile.TemporaryDirectory() as d,patch.object(m,'CONFIG',Path(d)/'mode.json'):
   self.assertTrue((await m.execute({'action':'status'}))['ok']);self.assertFalse(m.CONFIG.exists())
   self.assertFalse((await m.execute({'action':'invalid'}))['ok']);self.assertFalse(m.CONFIG.exists())
 async def test_read_only_cannot_change_mode(self):
  import tools
  o=TurnOutput('mode-test',AsyncMock());o.read_only=True;t=current_output.set(o)
  try:
   with patch.object(m,'execute',AsyncMock()) as execute:
    self.assertIn('Read-only',await tools.execute('set_telugu_mode',{'action':'on'}));execute.assert_not_awaited()
  finally:current_output.reset(t);await o.close()
