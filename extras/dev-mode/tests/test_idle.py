import json,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
import devmode_bridge as bridge

class IdleTests(unittest.IsolatedAsyncioTestCase):
 async def test_settings_restored_and_originals_survive_repeated_open(self):
  originals={'secure:screensaver_enabled':'1','secure:sleep_timeout':'1200000','system:screen_off_timeout':'300000'}
  async def adb(*args):
   if 'get' in args:return 0,originals[args[-2]+':'+args[-1]]+'\n'
   return 0,''
  with tempfile.TemporaryDirectory() as directory,patch.object(bridge,'IDLE_SETTINGS',Path(directory)/'idle.json'),patch.object(bridge,'adb',AsyncMock(side_effect=adb)) as commands:
   await bridge.keep_display_awake(True)
   self.assertEqual(json.loads(bridge.IDLE_SETTINGS.read_text()),originals)
   await bridge.keep_display_awake(True)
   self.assertEqual(sum('get' in c.args for c in commands.await_args_list),3)
   await bridge.keep_display_awake(False)
   self.assertFalse(bridge.IDLE_SETTINGS.exists())
   for key,value in originals.items():
    namespace,name=key.split(':')
    self.assertTrue(any(c.args[-4:]==('put',namespace,name,value) for c in commands.await_args_list))
