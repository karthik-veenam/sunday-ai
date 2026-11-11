"""Use the preferred Home Assistant Assist pipeline's STT and TTS providers."""
import asyncio
from collections import deque
import io
import json
from pathlib import Path
import time
import wave
import aiohttp
import numpy as np

STT_PROVIDER_FILE = Path('/home/djpi/sunday/stt_provider.json')   # {"provider": "elevenlabs" | "ha"}
TRANSCRIPT_TOGGLE = 'input_boolean.sunday_transcripts_to_telegram'
ELEVEN_URL = 'https://api.elevenlabs.io/v1/speech-to-text'
HA_CONFIG_ENTRIES = Path('/home/pi/homeassistant/.storage/core.config_entries')
DEBUG_TELEGRAM_CONFIG = Path('/home/djpi/sunday/debug_telegram.json')

class HASpeech:
    def __init__(self, *args, **kwargs):
        c=json.loads(Path('/home/djpi/sunday/config.json').read_text())
        self.base='http://127.0.0.1:8123'
        self.headers={'Authorization':'Bearer '+c['ha_token']}
        self.pipeline=None
        self.pipeline_loaded_at=0.0

    async def warmup(self):
        if self.pipeline is not None and time.monotonic()-self.pipeline_loaded_at<15:return
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.ws_connect(self.base+'/api/websocket') as ws:
                await ws.receive_json();await ws.send_json({'type':'auth','access_token':self.headers['Authorization'][7:]})
                if (await ws.receive_json()).get('type')!='auth_ok':raise RuntimeError('Home Assistant authentication failed')
                await ws.send_json({'id':1,'type':'assist_pipeline/pipeline/list'})
                while True:
                    result=await ws.receive_json()
                    if result.get('id')==1:break
                if not result.get('success'):raise RuntimeError('Could not read HA speech pipeline')
                data=result['result'];self.pipeline=next(p for p in data['pipelines'] if p['id']==data['preferred_pipeline'])
        self.pipeline_loaded_at=time.monotonic()
        print('[Speech] HA pipeline:',self.pipeline['name'],'STT:',self.pipeline['stt_engine'],'TTS:',self.pipeline['tts_engine'],flush=True)

class STT(HASpeech):
    RATE=16000
    CHUNK=1280

    def _provider(self):
        try:
            return json.loads(STT_PROVIDER_FILE.read_text()).get('provider', 'elevenlabs')
        except Exception:
            return 'elevenlabs'

    def _eleven_key(self):
        now = time.monotonic()
        if getattr(self, '_ekey', None) and now - self._ekey_at < 300:
            return self._ekey
        entries = json.loads(HA_CONFIG_ENTRIES.read_text())['data']['entries']
        self._ekey = next(e['data']['api_key'] for e in entries if e['domain'] == 'elevenlabs')
        self._ekey_at = now
        return self._ekey

    async def transcribe_eleven(self, pcm):
        # No fallback by design: any failure propagates and is reported like any other STT failure.
        started = time.perf_counter()
        form = aiohttp.FormData()
        mode=json.loads(Path('/home/djpi/sunday-alive/stt_streaming.json').read_text())
        language='tel' if mode.get('telugu_mode') is True and mode.get('enabled') is not True else 'eng'
        for k, v in (('model_id', 'scribe_v2'), ('file_format', 'pcm_s16le_16'), ('language_code', language),
                     ('tag_audio_events', 'false'), ('num_speakers', '1'), ('diarize', 'false'), ('keyterms', 'Sunday')):
            form.add_field(k, v)
        form.add_field('file', pcm, filename='audio.pcm', content_type='application/octet-stream')
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.post(ELEVEN_URL, headers={'xi-api-key': self._eleven_key()}, data=form) as r:
                if r.status != 200:
                    raise RuntimeError('ElevenLabs STT HTTP ' + str(r.status))
                result = await r.json()
        print(f"[Latency] STT request (elevenlabs scribe_v2 {language}+keyterms): {time.perf_counter()-started:.3f}s", flush=True)
        return (result.get('text') or '').strip()

    _forward_tasks = set()

    def _forward(self, text):
        try:
            task = asyncio.get_running_loop().create_task(self._forward_task(text))
            self._forward_tasks.add(task)
            task.add_done_callback(self._forward_tasks.discard)
        except Exception:
            pass

    async def _telegram_send(self, text):
        cfg = json.loads(DEBUG_TELEGRAM_CONFIG.read_text())
        token, chat = cfg.get('telegram_bot_token'), cfg.get('telegram_chat_id')
        if token and chat:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.post(f'https://api.telegram.org/bot{token}/sendMessage',
                                        json={'chat_id': chat, 'text': text}) as r:
                    await r.read()

    async def _forward_task(self, text):
        # Runs in the background; never blocks or breaks the voice turn, never logs the bot URL.
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(self.base + '/api/states/' + TRANSCRIPT_TOGGLE, headers=self.headers) as r:
                    if r.status != 200 or (await r.json()).get('state') != 'on':
                        return
            await self._telegram_send('\U0001F399 ' + text)
        except Exception:
            pass

    async def transcribe(self, pcm):
        from stt_cleanup import clean
        pcm, cleanup = await clean(pcm)
        print('[STT cleanup]', json.dumps(cleanup, sort_keys=True), flush=True)
        if self._provider() == 'elevenlabs':
            return await self.transcribe_eleven(pcm)
        await self.warmup()
        started=time.perf_counter()
        headers={**self.headers,'Content-Type':'audio/pcm',
                 'X-Speech-Content':f"format=wav; codec=pcm; sample_rate=16000; bit_rate=16; channel=1; language={self.pipeline['stt_language']}"}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as session:
            async with session.post(self.base+'/api/stt/'+self.pipeline['stt_engine'],headers=headers,data=pcm) as r:
                r.raise_for_status();result=await r.json()
        print(f"[Latency] STT request ({self.pipeline['stt_engine']}): {time.perf_counter()-started:.3f}s",flush=True)
        if result.get('result')!='success':raise RuntimeError('HA speech recognition failed')
        return (result.get('text') or '').strip()

    async def listen_and_transcribe(self,mic,silence_threshold=200,speech_threshold=400,
        silence_seconds=.8,max_seconds=25.,speech_start_timeout=20.,on_recorded=None):
        from stt_thresholds import read as listening_settings
        settings = listening_settings({'stt_silence_threshold': silence_threshold,
            'stt_speech_threshold': speech_threshold, 'stt_silence_seconds': silence_seconds})
        silence_threshold = settings['stt_silence_threshold']
        speech_threshold = settings['stt_speech_threshold']
        silence_seconds = settings['stt_silence_seconds']
        from eleven_realtime_stt import enabled as live_enabled,listen as live_listen
        diagnostic=Path('/home/djpi/sunday2-rebuild/deployment/stt-audio-diagnostic-20261024/capture-next')
        diagnostic_active=diagnostic.exists() and time.time()-diagnostic.stat().st_mtime<600
        if self._provider()=='elevenlabs' and live_enabled() and not diagnostic_active:
            return await live_listen(self,mic,silence_threshold,speech_threshold,silence_seconds,max_seconds,speech_start_timeout,on_recorded)
        self.last_audio=b'';self.last_started_at=None;self.last_error=None
        mode=getattr(self,'listening_mode',None)
        generation=mode.generation if mode else None
        if mode and mode.muted:return ''
        diagnostic_root=Path('/home/djpi/sunday2-rebuild/deployment/stt-audio-diagnostic-20261024')
        diagnostic_flag=diagnostic_root/'capture-next'
        diagnostic_enabled=diagnostic_flag.exists() and time.time()-diagnostic_flag.stat().st_mtime<600
        handoff_prefix=b''
        if diagnostic_enabled:
            now=time.monotonic()
            handoff_prefix=b''.join(a.tobytes() for at,a in list(getattr(mic,'_feedback_buffer',[])) if at>=now-0.8)
        from adaptive_audio import Gate
        gate=Gate(mic,speech_threshold,silence_threshold)
        mic.drain();pre=deque(maxlen=3);frames=[];speech=False;quiet=0
        waited=0.;duration=0.
        print('[STT] Listening via Home Assistant',flush=True)
        while duration<max_seconds:
            chunk=await mic.read()
            if mode and (mode.muted or mode.generation!=generation):
                self.last_audio=b'';return ''
            seconds=len(chunk)/self.RATE;duration+=seconds
            rms=float(np.sqrt(np.mean(chunk.astype(np.float32)**2)))
            starts,continues=gate.decide(chunk)
            if not speech:
                pre.append(chunk.tobytes());waited+=seconds
                if starts:
                    speech=True;frames.extend(pre);self.last_started_at=time.monotonic()-sum(map(len,pre))/32000
                elif waited>=speech_start_timeout:break
                continue
            frames.append(chunk.tobytes())
            quiet=quiet+seconds if not continues else 0
            if quiet>=silence_seconds:break
        if mode and (mode.muted or mode.generation!=generation):return ''
        self.last_audio=b''.join(frames)
        if on_recorded:await on_recorded()
        if not speech:return ''
        if diagnostic_enabled and diagnostic_flag.exists():
            diagnostic_flag.unlink()
            for name,audio in [('received.wav',self.last_audio),('with-handoff.wav',handoff_prefix+self.last_audio)]:
                target=diagnostic_root/name
                with wave.open(str(target),'wb') as out:
                    out.setnchannels(1);out.setsampwidth(2);out.setframerate(self.RATE);out.writeframes(audio)
                target.chmod(0o600)
            print('[STT diagnostic] One spoken test captured privately; skipping brain/actions for this test.',flush=True)
            return ''
        try:
            if mode:
                request=asyncio.create_task(self.transcribe(self.last_audio))
                pause=asyncio.create_task(mode.wait_muted())
                try:
                    done,_=await asyncio.wait((request,pause),return_when=asyncio.FIRST_COMPLETED)
                    if pause in done or mode.muted or mode.generation!=generation:
                        request.cancel();await asyncio.gather(request,return_exceptions=True)
                        self.last_audio=b'';return ''
                    text=await request
                finally:
                    pause.cancel()
                    if not request.done():request.cancel()
                    await asyncio.gather(request,pause,return_exceptions=True)
            else:text=await self.transcribe(self.last_audio)
            if mode and (mode.muted or mode.generation!=generation):return ''
            print('[STT] HA transcript:',repr(text),flush=True)
            if text:
                self._forward(text)
            return text
        except Exception as error:
            self.last_error=type(error).__name__
            print('[STT] HA request failed:',self.last_error,flush=True)
            return ''

class TTS(HASpeech):
    async def warmup(self):
        from eleven_tts import settings, ElevenTTS
        c=settings()
        if c.get('provider')=='elevenlabs':
            if not hasattr(self,'_eleven'):self._eleven=ElevenTTS()
            self._eleven.api_key()
            print('[Speech] TTS: ElevenLabs '+c.get('model_id','')+' voice='+c.get('voice_id',''),flush=True)
            return
        await super().warmup()

    async def synthesize_audio(self,text):
        await super().warmup()
        p=self.pipeline
        body={'engine_id':p['tts_engine'],'message':text,'language':p['tts_language'],'cache':True}
        if p.get('tts_voice'):body['options']={'voice':p['tts_voice']}
        async with aiohttp.ClientSession(headers=self.headers,timeout=aiohttp.ClientTimeout(total=45)) as session:
            async with session.post(self.base+'/api/tts_get_url',json=body) as r:
                r.raise_for_status();result=await r.json()
            async with session.get(self.base+result['path']) as r:
                r.raise_for_status();return await r.read()

    supports_speech_context = True

    async def finish_reply(self, request_id, cancelled=False):
        # Best effort after playback, never before first speech. No audio/model work.
        try:
            from pathlib import Path
            import json
            import os
            cfg=json.loads(Path(os.environ.get('SUNDAY_ALIVE_CONFIG','/home/djpi/sunday-alive/config.json')).read_text())
            if cfg.get('alive_enabled') is not True or cfg.get('brain_performance_hints') is not True:return
            key=Path('/home/djpi/sunday-audio/secrets/device.key').read_text().strip()
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=.3)) as session:
                async with session.post('http://127.0.0.1:8773/alive/reply_end',headers={'Authorization':'Bearer '+key},json={'request_id':request_id,'cancelled':bool(cancelled)}) as response:
                    await response.read()
        except Exception:pass

    async def speak(self,text,speech_context=None):
        from eleven_tts import settings, plain, ElevenTTS
        if not plain(text):return
        c=settings();pcm=None
        if c.get('provider')=='elevenlabs':
            if not hasattr(self,'_eleven'):self._eleven=ElevenTTS()
            try:pcm=await self._eleven.synthesize_pcm(text,c)
            except Exception as error:
                if c.get('fallback_provider')!='ha':raise
                print('[Speech] ElevenLabs TTS unavailable ('+type(error).__name__+'); HA fallback for this sentence.',flush=True)
        if pcm is None:
            audio=await self.synthesize_audio(plain(text))
            decode=await asyncio.create_subprocess_exec('ffmpeg','-v','error','-i','pipe:0','-f','s16le','-ar','22050','-ac','1','pipe:1',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            pcm,err=await decode.communicate(audio)
            if decode.returncode:raise RuntimeError('HA speech audio decode failed')
        import os, json
        child_env=os.environ.copy()
        child_env.pop('SUNDAY_SPEECH_CONTEXT',None)
        if speech_context:
            child_env['SUNDAY_SPEECH_CONTEXT']=json.dumps(speech_context,separators=(',',':'))
        from adaptive_audio import suppress
        suppress(len(pcm)/44100+.8)
        play=await asyncio.create_subprocess_exec('aplay','-D','pulse','-r','22050','-f','S16_LE','-c','1','-t','raw','-q',stdin=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=child_env)
        _,err=await play.communicate(pcm)
        suppress(.4)
        if play.returncode:raise RuntimeError('HA speech playback failed')
