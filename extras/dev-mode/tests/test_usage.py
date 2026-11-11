import unittest
from unittest.mock import AsyncMock,patch
from devmode_usage import project_limits

class UsageTests(unittest.TestCase):
 def test_real_windows_remaining_and_no_account_identifiers(self):
  value=project_limits({'accountId':'private','rateLimits':{'primary':{'usedPercent':72,'windowDurationMins':300,'resetsAt':100},'secondary':{'usedPercent':64,'windowDurationMins':10080,'resetsAt':200}}})
  self.assertEqual([(w['label'],w['remainingPercent']) for w in value['windows']],[('5-hour',28),('Weekly',36)])
  self.assertNotIn('private',str(value))
 def test_single_weekly_window_not_mislabeled_daily(self):
  value=project_limits({'rateLimitsByLimitId':{'codex':{'primary':{'usedPercent':15,'windowDurationMins':10080},'secondary':None}}})
  self.assertEqual(value['windows'][0]['label'],'Weekly')
  self.assertEqual(len(value['windows']),1)
 def test_missing_is_unavailable_not_full(self):
  self.assertFalse(project_limits({})['available'])
 def test_explicit_daily_is_daily(self):
  self.assertEqual(project_limits({'rateLimits':{'primary':{'usedPercent':3,'windowDurationMins':1440}}})['windows'][0]['label'],'Daily')

class LaunchTests(unittest.IsolatedAsyncioTestCase):
 async def test_warm_launch_does_not_wake(self):
  import devmode_bridge as bridge
  with patch.object(bridge,'adb',AsyncMock(return_value=(0,''))) as adb,patch.object(bridge.asyncio,'sleep',AsyncMock()) as sleep:
   self.assertTrue(await bridge.launch_display(wake=False))
  self.assertFalse(any('KEYCODE_WAKEUP' in c.args for c in adb.await_args_list))
  sleep.assert_not_awaited()
 async def test_cold_launch_wakes(self):
  import devmode_bridge as bridge
  with patch.object(bridge,'adb',AsyncMock(return_value=(0,''))) as adb,patch.object(bridge.asyncio,'sleep',AsyncMock()) as sleep:
   self.assertTrue(await bridge.launch_display(wake=True))
  self.assertTrue(any('KEYCODE_WAKEUP' in c.args for c in adb.await_args_list))
  sleep.assert_awaited_once_with(2)
