import unittest
from unittest.mock import AsyncMock
import numpy as np
from ha_speech import STT
class Mic:
 def __init__(self,levels):self.chunks=iter([np.full(1280,n,np.int16) for n in levels])
 def drain(self):pass
 async def read(self):return next(self.chunks)
class HASpeechTests(unittest.IsolatedAsyncioTestCase):
 async def test_utterance_keeps_feedback_audio_and_transcribes(self):
  stt=STT.__new__(STT);stt.transcribe=AsyncMock(return_value='false alarm');done=AsyncMock()
  result=await stt.listen_and_transcribe(Mic([0,0]+[1000]*5+[0]*12),speech_threshold=300,silence_threshold=150,on_recorded=done)
  self.assertEqual(result,'false alarm');self.assertTrue(stt.last_audio);self.assertIsNotNone(stt.last_started_at);done.assert_awaited_once();stt.transcribe.assert_awaited_once_with(stt.last_audio)
 async def test_silence_is_not_sent_for_transcription(self):
  stt=STT.__new__(STT);stt.transcribe=AsyncMock()
  self.assertEqual(await stt.listen_and_transcribe(Mic([0]*5),speech_start_timeout=.16),'');stt.transcribe.assert_not_awaited()
 async def test_provider_failure_is_visible(self):
  stt=STT.__new__(STT);stt.transcribe=AsyncMock(side_effect=RuntimeError('test'))
  self.assertEqual(await stt.listen_and_transcribe(Mic([1000]*5+[0]*12)), '');self.assertEqual(stt.last_error,'RuntimeError')
if __name__=='__main__':unittest.main()
