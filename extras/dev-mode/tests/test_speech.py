import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from devmode_speech import Speech, spoken_text

class SpeechTests(unittest.IsolatedAsyncioTestCase):
    def test_prose(self):
        self.assertEqual(spoken_text('**Ready** [details](/tmp/x)\n```py\nsecret()\n```'), 'Ready details')

    async def test_deduplicate_and_ignore_tools(self):
        s = Speech(AsyncMock(), AsyncMock())
        item = {'id':'one', 'type':'agentMessage', 'text':'Hello'}
        s.enqueue(item); s.enqueue(item)
        s.enqueue({'id':'two','type':'commandExecution','text':'echo hi'})
        self.assertEqual(s.queue.qsize(), 1)

    async def test_playback_and_failure_restore_mic(self):
        for fail in (False, True):
            gate, report = AsyncMock(), AsyncMock()
            s = Speech(gate, report)
            s.tts.synthesize_pcm = AsyncMock(return_value=b'\0\0'*100)
            s.tts.close = AsyncMock()
            player = AsyncMock(); player.returncode = 1 if fail else 0
            s.enqueue({'id':'one','type':'agentMessage','text':'Hello'})
            with patch('asyncio.create_subprocess_exec', AsyncMock(return_value=player)):
                task = asyncio.create_task(s.run())
                await asyncio.wait_for(s.queue.join(), 3)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.assertEqual([c.args[0] for c in gate.await_args_list], [True, False])
            self.assertFalse(s.speaking)
