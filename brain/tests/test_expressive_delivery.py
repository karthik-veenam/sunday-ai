import unittest
from unittest.mock import AsyncMock
from output import TurnOutput,current_output
from agent import _OpenAIBackend
class Tests(unittest.IsolatedAsyncioTestCase):
 async def test_tags_only_in_voice_envelope_plain_history(self):
  emit=AsyncMock();out=TurnOutput('x',emit);out.audio_tags_enabled=True
  await out.speak('[happy] Good to hear from you.');self.assertEqual(out.spoken,['Good to hear from you.']);self.assertEqual(emit.await_args.args[0]['tts_text'],'[happy] Good to hear from you.');self.assertEqual(emit.await_args.args[0]['text'],'Good to hear from you. ')
 async def test_default_and_telegram_do_not_get_audio_cues(self):
  emit=AsyncMock();out=TurnOutput('x',emit);await out.speak('[laughs] That was funny.');self.assertNotIn('tts_text',emit.await_args.args[0]);self.assertEqual(out.spoken,['That was funny.'])
 async def test_unknown_directions_removed_and_tag_only_cannot_speak(self):
  emit=AsyncMock();out=TurnOutput('x',emit);out.audio_tags_enabled=True
  await out.speak('[invented direction] Hello.');self.assertNotIn('tts_text',emit.await_args.args[0]);emit.reset_mock();await out.speak('[laughs]');emit.assert_not_awaited()
 async def test_voice_prompt_marker_not_in_telegram(self):
  out=TurnOutput('x',AsyncMock());out.audio_tags_enabled=True;token=current_output.set(out)
  try:
   b=_OpenAIBackend.__new__(_OpenAIBackend);b._turn_context=''
   self.assertIn('Expressive ElevenLabs audio delivery is enabled',b._system_messages(False)[-1]['content']);self.assertNotIn('Expressive ElevenLabs audio delivery is enabled',str(b._system_messages(True)))
  finally:current_output.reset(token)
if __name__=='__main__':unittest.main()
