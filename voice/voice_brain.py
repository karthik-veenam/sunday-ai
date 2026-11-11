"""Send local microphone turns to the persistent Sunday brain and play its text."""
import json
import uuid
import time
from pathlib import Path
import aiohttp

class VoiceBrain:
    handles_fast_commands = True

    def __init__(self, config_path='/home/djpi/sunday2-rebuild/config.json'):
        config=json.loads(Path(config_path).read_text())
        self.url=f"http://127.0.0.1:{config['daemon_port']}/v1/chat"
        self.token=config['daemon_token']
        self.context_idle_seconds = 15 * 60
        self._last_voice_activity = None
        self.clear_context()

    def clear_context(self):
        """Explicitly forget the current voice session (for example, goodbye)."""
        self.conversation_id = 'local-voice:' + uuid.uuid4().hex
        self._last_voice_activity = None

    def reset(self):
        """End a listening window without discarding recent conversation."""
        pass

    def _begin_turn(self):
        now = time.monotonic()
        if (self._last_voice_activity is not None
                and now - self._last_voice_activity >= self.context_idle_seconds):
            self.clear_context()
        self._last_voice_activity = now

    async def process(self, text, tts):
        self._begin_turn()
        payload={'protocol':1,'request_id':uuid.uuid4().hex,
                 'conversation_id':self.conversation_id,'satellite_id':'local-airhug',
                 'source':'voice','text':text,'stream':True}
        spoken=False;final=None
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=150)) as session:
                async with session.post(self.url,headers={'Authorization':'Bearer '+self.token},json=payload) as response:
                    response.raise_for_status()
                    async for line in response.content:
                        if not line.strip():continue
                        event=json.loads(line)
                        if event.get('type')=='error':raise RuntimeError('Persistent brain could not finish the turn')
                        if event.get('type')=='assistant_text' and event.get('text','').strip():
                            spoken_text=(event.get('tts_text') or event['text']).strip()
                            if getattr(tts,'supports_speech_context',False) and event.get('request_id')==payload['request_id'] and event.get('utterance_id'):
                                context={k:event[k] for k in ('request_id','utterance_id','performance') if k in event}
                                await tts.speak(spoken_text,speech_context=context)
                            else:await tts.speak(spoken_text)
                            spoken=True
                        elif event.get('type')=='final':final=event
            if final is None:raise RuntimeError('No final result from persistent brain')
            if not spoken and final.get('text'):await tts.speak(final['text'])
            self._last_voice_activity = time.monotonic()
            return bool(final.get('continue_conversation'))
        except Exception as error:
            # Never replay a failed request through the older agent: tools may have run.
            print('[Voice] Persistent brain turn failed:',type(error).__name__,flush=True)
            await tts.speak("I couldn't complete that request. Please check Sunday before trying it again.")
            return False

        finally:
            if getattr(tts,'supports_speech_context',False):
                try:await tts.finish_reply(payload['request_id'],cancelled=final is None)
                except Exception:pass
