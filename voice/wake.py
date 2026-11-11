"""Live detection using the evaluated model and pinned openWakeWord pipeline."""
import asyncio
import io
import json
import re
import sys
import time
import wave
from pathlib import Path
import numpy as np
from negative_dataset import enqueue as enqueue_negative
RUNTIME = '/home/djpi/sunday-openwakeword-colab/full-suite-v1/runtime'
if RUNTIME not in sys.path:
    sys.path.insert(0, RUNTIME)
from openwakeword.model import Model


class WakeVerifier:
    """Configurable keyword verification after every wake-model candidate.

    When enabled, errors retry the same audio once, then reject. Bypass is explicit only.
    """
    CONFIG = Path('/home/djpi/sunday/wake_verifier.json')
    LOG = Path('/home/djpi/sunday-wake-verifier/verdicts.jsonl')

    ALERT_EVERY_S = 1800  # at most one Telegram heads-up per failure kind per 30 min
    TELEGRAM_CONFIG = Path('/home/djpi/sunday/debug_telegram.json')
    _last_alert = {}
    _alert_tasks = set()

    def config(self):
        try:
            cfg = json.loads(self.CONFIG.read_text())
            if not isinstance(cfg, dict) or not isinstance(cfg.get('aliases'), list) or not cfg['aliases']:
                raise ValueError('Invalid verifier configuration')
            if not isinstance(cfg.get('enabled', True), bool):
                raise ValueError('Invalid verifier enabled setting')
            cfg['mode'] = 'enforce' if cfg.get('enabled', True) else 'bypass'
            return cfg
        except Exception as error:
            return {'mode': 'enforce', 'configuration_error': type(error).__name__}

    def alert(self, kind, detail):
        """Throttled diagnostic after a wake is blocked by verifier failure."""
        try:
            now = time.monotonic()
            if now - self._last_alert.get(kind, -1e9) < self.ALERT_EVERY_S:
                return
            self._last_alert[kind] = now
            text = ('Sunday wake verifier unavailable after retry: ' + detail + '. This wake was blocked; '
                    'V5-only fallback is disabled. The next wake will try verification again. '
                    'Details: /home/djpi/sunday-wake-verifier/verdicts.jsonl')
            task = asyncio.get_running_loop().create_task(asyncio.to_thread(self._send, text))
            self._alert_tasks.add(task)
            task.add_done_callback(self._alert_tasks.discard)
        except Exception:
            pass

    def _send(self, text):
        try:
            import requests
            cfg = json.loads(self.TELEGRAM_CONFIG.read_text())
            token, chat = cfg.get('telegram_bot_token'), cfg.get('telegram_chat_id')
            if token and chat:
                requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                              json={'chat_id': chat, 'text': text}, timeout=8)
        except Exception:
            pass  # never log: exception text can contain the bot URL

    @staticmethod
    def normalize(text):
        return ' '.join(re.sub(r'[^a-z0-9]+', ' ', str(text).casefold()).split())

    @classmethod
    def match(cls, normalized, aliases):
        tokens = normalized.split()
        for alias in aliases:
            words = cls.normalize(alias).split()
            if words and any(tokens[i:i+len(words)] == words for i in range(len(tokens)-len(words)+1)):
                return alias
        return None

    @staticmethod
    def window(mic, audio, pre_s, post_s):
        history = list(mic._feedback_buffer)
        index = next((i for i in range(len(history)-1, -1, -1) if history[i][1] is audio), None)
        if index is None:
            return None
        pre_chunks = int(round(pre_s * 16000 / 1280))
        post_chunks = int(round(post_s * 16000 / 1280))
        chunks = [c for _, c in history[max(0, index+1-pre_chunks):index+1+post_chunks]]
        return np.concatenate(chunks) if chunks else None

    def _call(self, pcm, cfg):
        import requests
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(np.asarray(pcm, dtype='<i2').tobytes())
        started = time.monotonic()
        response = requests.post(cfg.get('url', 'http://127.0.0.1:8183/inference'),
                                 files={'file': ('candidate.wav', buf.getvalue(), 'audio/wav')},
                                 data={'response_format': 'json', 'temperature': '0', 'temperature_inc': '0',
                                       'language': 'en', 'audio_ctx': str(cfg.get('audio_ctx', 128))},
                                 timeout=cfg.get('timeout_s', 1.5))
        response.raise_for_status()
        data = response.json()
        if cfg.get('provider') == 'sherpa':
            alias = data.get('matched_alias')
            accepted = data.get('accepted')
            if data.get('provider') != 'sherpa' or not isinstance(accepted, bool) or (accepted and alias not in cfg['aliases']) or (not accepted and alias is not None):
                raise ValueError('Invalid keyword verifier response')
            return {'provider':'sherpa', 'model':data.get('model'), 'text':'', 'matched_alias':alias,
                    'accepted':accepted, 'stop':response.headers.get('X-Sunday-Early-Stop'),
                    'latency_ms':round((time.monotonic() - started) * 1000, 1)}
        return {'text': data['text'], 'stop': response.headers.get('X-Sunday-Early-Stop'),
                'latency_ms': round((time.monotonic() - started) * 1000, 1)}

    async def verify(self, mic, audio, score, event_id, mode):
        cfg = self.config()
        record = {'provider':cfg.get('provider','whisper'),'t': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'mode': mode, 'event_id': event_id, 'score': float(score)}
        if cfg.get('enabled', True) is False and not cfg.get('configuration_error'):
            record.update(mode='bypass', provider='none', accepted=True, matched=None,
                          latency_ms=0, bypass_reason='user_disabled_verifier')
            try:
                self.LOG.parent.mkdir(parents=True, exist_ok=True)
                with self.LOG.open('a') as f:
                    f.write(json.dumps(record) + '\n')
            except Exception:
                pass
            print('[Wake verifier] BYPASS: explicitly disabled in configuration', flush=True)
            return {'accepted': True, 'record': record}
        accepted = False  # A successful verifier alias match is required.
        try:
            await asyncio.sleep(cfg.get('post_s', 0.16) + 0.02)  # non-consuming: reads the mic ring buffer only
            pcm = self.window(mic, audio, cfg.get('pre_s', 1.6), cfg.get('post_s', 0.16))
            if pcm is None:
                raise RuntimeError('trigger audio not in ring buffer')
            failures = []
            for attempt in range(2):
                record['attempts'] = attempt + 1
                try:
                    if cfg.get('configuration_error'):
                        cfg = self.config()
                        if cfg.get('configuration_error'):
                            raise RuntimeError('Verifier configuration unavailable')
                    attempt_cfg = dict(cfg)
                    if attempt:
                        attempt_cfg['timeout_s'] = max(3.0, cfg.get('timeout_s', 1.5))
                    result = await asyncio.wait_for(asyncio.to_thread(self._call, pcm, attempt_cfg),
                                                    timeout=attempt_cfg.get('timeout_s', 1.5) + 0.5)
                    if not isinstance(result.get('text'), str):
                        raise ValueError('Verifier returned an invalid transcript')
                    break
                except Exception as error:
                    failures.append(type(error).__name__)
                    record['retry_errors'] = failures[:]
                    if attempt:
                        raise
                    await asyncio.sleep(0.25)

            normalized = self.normalize(result['text'])
            if result.get('provider') == 'sherpa':
                alias = result['matched_alias']
                accepted = result['accepted']
                record['keyword_result'] = 'matched' if accepted else 'no_match'
                record['verifier_model'] = result.get('model')
            else:
                alias = self.match(normalized, cfg.get('aliases', ['sunday']))
                accepted = alias is not None
            record.update(text=result['text'], normalized=normalized, matched=alias, accepted=accepted,
                          stop=result['stop'], latency_ms=result['latency_ms'], window_s=round(len(pcm)/16000, 3))
        except Exception as error:
            record.update(error=type(error).__name__ + ': ' + str(error)[:120], accepted=False, blocked_reason='verification_unavailable')
            accepted = False
            if mode == 'enforce':
                self.alert('verify', type(error).__name__)
        if mode == 'enforce' and not accepted and 'error' not in record:
            try:
                enqueue_negative(np.asarray(pcm, dtype='<i2').tobytes(), record, cfg)
            except Exception as error:
                print('[Wake dataset] Capture queue failed: ' + type(error).__name__, flush=True)
        try:
            self.LOG.parent.mkdir(parents=True, exist_ok=True)
            with self.LOG.open('a') as f:
                f.write(json.dumps(record) + '\n')
        except Exception:
            pass
        print(f"[Wake verifier] {mode}: {'ACCEPT' if accepted else 'REJECT'} text={record.get('text')!r} "
              f"alias={record.get('matched')} {record.get('latency_ms')}ms {record.get('error', '')}", flush=True)
        return {'accepted': accepted, 'record': record}


class WakeWordDetector:
    CHUNK_SAMPLES = 1280
    CONFIRM_FRAMES = 1  # Same per-frame threshold criterion as offline evaluation.

    def __init__(self, model_path, threshold=.99):
        self.model_path = model_path
        self.threshold = threshold
        self.feedback = None
        self.verifier = WakeVerifier()
        self._shadow_tasks = set()
        self._refractory_until = 0.0
        self.model,self.key=self._build_model(model_path)
        print(f'[Wake] Evaluated openWakeWord pipeline loaded: {Path(model_path).parent.name}, threshold={threshold}', flush=True)

    @staticmethod
    def _build_model(model_path):
        assets=Path(model_path).parent
        model=Model(wakeword_models=[model_path], inference_framework='onnx', ncpu=1,
                    melspec_model_path=str(assets/'melspectrogram.onnx'),
                    embedding_model_path=str(assets/'embedding_model.onnx'))
        return model,next(iter(model.models))

    def _process_chunk(self, audio):
        return float(self.model.predict(np.asarray(audio, dtype=np.int16))[self.key])

    def _flush(self):
        self.model.reset()
        # Settle startup features with silence before accepting real microphone input.
        quiet = np.zeros(self.CHUNK_SAMPLES, np.int16)
        for _ in range(50):
            self._process_chunk(quiet)

    async def wait_for_wake_word(self, mic):
        await asyncio.to_thread(self._flush)
        mic.drain()
        paused=False
        print(f'[Wake] Listening for Sunday ({Path(self.model_path).parent.name})', flush=True)
        while True:
            profile=getattr(self,'profile',None)
            if profile:
                try:
                    if await profile.apply_pending():
                        await asyncio.to_thread(self._flush)
                        mic.drain()
                except Exception as error:
                    print('[Wake profile] Switch failed; retaining current model:',type(error).__name__,flush=True)
                    profile.requested=profile.applied
            audio = await mic.read()
            mode=getattr(self,'listening_mode',None)
            if mode and mode.muted:
                if not paused:self.model.reset();paused=True
                continue
            if paused:
                await asyncio.to_thread(self._flush);mic.drain();paused=False
                continue
            score = self._process_chunk(audio)
            if mode and mode.muted:continue
            if score >= self.threshold:
                if time.monotonic() < self._refractory_until:
                    continue  # still inside the 2 s spacing after a verifier rejection
                detected_ns = time.monotonic_ns()
                print(f'[Wake] Sunday detected, score={score:.6f}', flush=True)
                event_id = None
                if self.feedback:
                    try:
                        event_id = self.feedback.triggered(mic, audio, score, self.threshold, self.model_path)
                    except Exception as error:
                        print('[Wake feedback] Could not save event:', type(error).__name__, flush=True)
                verdict = await self.verifier.verify(mic, audio, score, event_id, 'enforce')
                accepted_ns = time.monotonic_ns()
                if not verdict['accepted']:
                    self._refractory_until = time.monotonic() + 2.0
                    continue
                self.last_wake_trace = {'id': event_id or str(detected_ns), 'detected_ns': detected_ns, 'accepted_ns': accepted_ns, 'verifier_ms': verdict['record'].get('latency_ms')}
                print('[Wake audit] ' + json.dumps({**self.last_wake_trace, 'stage': 'accepted', 'at_ns': accepted_ns, 'since_detection_ms': round((accepted_ns-detected_ns)/1e6,3)}), flush=True)
                return
