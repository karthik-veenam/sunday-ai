import asyncio,json,unittest
from unittest.mock import AsyncMock,Mock,patch
from types import SimpleNamespace as NS
from eleven_tts import ElevenTTS,delivery,plain
from ha_speech import TTS
C={'provider':'elevenlabs','voice_id':'bxiObU1YDrf7lrFAyV99','model_id':'eleven_v4_turbo','audio_tags':True,'fallback_provider':'ha'}
class Content:
 async def iter_chunked(self,n):yield b'\x00\x00'*2205
class Response:
 status=200;content=Content()
 async def __aenter__(self):return self
 async def __aexit__(self,*args):pass
class Tests(unittest.IsolatedAsyncioTestCase):
 def test_tag_sanitizer(self):
  self.assertEqual(delivery('[HAPPY] Hello. [hack]'),'[happy] Hello.');self.assertEqual(plain('[happy] Hello.'),'Hello.');self.assertEqual(delivery('[happy] Hello.',False),'Hello.')
 async def test_voice_model_language_pcm_and_tags(self):
  e=ElevenTTS();e.api_key=Mock(return_value='fake');session=NS(closed=False,post=Mock(return_value=Response()));e.session=session
  pcm=await e.synthesize_pcm('[happy] Hello. [hack]',C);self.assertEqual(len(pcm),4410);args=session.post.call_args;self.assertIn(C['voice_id'],args.args[0]);self.assertEqual(args.kwargs['json']['model_id'],'eleven_v4_turbo');self.assertEqual(args.kwargs['json']['language_code'],'en');self.assertEqual(args.kwargs['json']['text'],'[happy] Hello.');self.assertEqual(args.kwargs['params']['output_format'],'pcm_22050')
 async def test_bad_response_does_not_become_audio(self):
  e=ElevenTTS();e.api_key=Mock(return_value='fake');r=Response();r.status=401;e.session=NS(closed=False,post=Mock(return_value=r))
  with self.assertRaisesRegex(RuntimeError,'HTTP 401'):await e.synthesize_pcm('Hello.',C)
 async def test_pcm_uses_existing_aplay_without_decode(self):
  t=TTS.__new__(TTS);t._eleven=NS(synthesize_pcm=AsyncMock(return_value=b'\x00\x00'*100));t.synthesize_audio=AsyncMock();play=NS(communicate=AsyncMock(return_value=(b'',b'')),returncode=0)
  with patch('eleven_tts.settings',return_value=C),patch('asyncio.create_subprocess_exec',AsyncMock(return_value=play)) as spawn:
   await t.speak('[happy] Hello.');self.assertEqual(spawn.await_count,1);self.assertEqual(spawn.await_args.args[0],'aplay');self.assertIn('22050',spawn.await_args.args);t.synthesize_audio.assert_not_awaited()
 async def test_generation_failure_falls_back_before_play_and_strips_tags(self):
  t=TTS.__new__(TTS);t._eleven=NS(synthesize_pcm=AsyncMock(side_effect=RuntimeError('unavailable')));t.synthesize_audio=AsyncMock(return_value=b'encoded');decode=NS(communicate=AsyncMock(return_value=(b'\x00\x00'*100,b'')),returncode=0);play=NS(communicate=AsyncMock(return_value=(b'',b'')),returncode=0)
  with patch('eleven_tts.settings',return_value=C),patch('asyncio.create_subprocess_exec',AsyncMock(side_effect=[decode,play])):
   await t.speak('[happy] Hello.');t.synthesize_audio.assert_awaited_once_with('Hello.')
 async def test_playback_failure_is_not_replayed_via_fallback(self):
  t=TTS.__new__(TTS);t._eleven=NS(synthesize_pcm=AsyncMock(return_value=b'\x00\x00'*100));t.synthesize_audio=AsyncMock();play=NS(communicate=AsyncMock(return_value=(b'',b'failed')),returncode=1)
  with patch('eleven_tts.settings',return_value=C),patch('asyncio.create_subprocess_exec',AsyncMock(return_value=play)) as spawn:
   with self.assertRaises(RuntimeError):await t.speak('Hello.')
   self.assertEqual(spawn.await_count,1);t.synthesize_audio.assert_not_awaited()
if __name__=='__main__':unittest.main()
