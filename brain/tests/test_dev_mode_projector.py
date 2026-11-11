import unittest
from unittest.mock import AsyncMock, patch
import dev_mode_tools as dev

class ProjectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_already_on_navigates_without_power_wake_or_boot_wait(self):
        ha = AsyncMock()
        with patch('tools._ha',ha), patch.object(dev,'_call',AsyncMock()) as call, patch.object(dev.asyncio,'sleep',AsyncMock()) as sleep:
            await dev.bring_up_display('on')
        ha.call_service.assert_not_awaited();sleep.assert_not_awaited()
        self.assertEqual(call.await_args.args[2], {'wake':False})

    async def test_off_powers_on_then_waits_before_launch(self):
        ha=AsyncMock();ha.call_service.return_value='ok'
        with patch('tools._ha',ha), patch.object(dev,'_call',AsyncMock()) as call, patch.object(dev.asyncio,'sleep',AsyncMock()) as sleep:
            await dev.bring_up_display('off')
        ha.call_service.assert_awaited_once_with('switch','turn_on',{'entity_id':dev.PROJECTOR})
        sleep.assert_awaited_once_with(dev.BOOT_WAIT)
        self.assertEqual(call.await_args.args[2], {'wake':True})

    async def test_unknown_never_sends_power_command(self):
        ha=AsyncMock()
        with patch('tools._ha',ha),patch.object(dev,'_call',AsyncMock()) as call:
            await dev.bring_up_display('unavailable')
        ha.call_service.assert_not_awaited()
        self.assertEqual(call.await_args.args[2], {'wake':False})

    async def test_failed_power_does_not_claim_display_ready(self):
        ha=AsyncMock();ha.call_service.return_value='HA action failed'
        with patch('tools._ha',ha),patch.object(dev,'_call',AsyncMock()) as call:
            await dev.bring_up_display('off')
        call.assert_not_awaited()
