"""Text and tool events; this module never synthesizes or plays audio."""
import asyncio
import contextvars
import re
import uuid
import time

current_output = contextvars.ContextVar("sunday2_output", default=None)


from voice_delivery import TAGS as AUDIO_TAGS, plain as speech_text, delivery as tts_text
from voice_delivery import validate as validate_voice_delivery, render as render_voice, tags_in


def spawn_task(coro):
    task = asyncio.create_task(coro)
    output = current_output.get()
    if output:
        output.tasks.add(task)
        task.add_done_callback(output.tasks.discard)
    return task


class TurnOutput:
    def __init__(self, request_id, emit, satellite_id=None):
        self.started_at = time.perf_counter()
        self.request_id = request_id
        self.satellite_id = satellite_id
        self.emit = emit
        self.spoken = []
        self.tasks = set()
        self.speech_started = asyncio.Event()
        self.tool_count = 0
        self.model_calls = 0
        self.tool_events = []
        self.extra_context = ""
        self.read_only = False
        self.alive_hints = False
        self.performance = None
        self.reply_continuation = None
        self.performance_status = "auto_no_hint"
        self._performance_locked = False
        self.voice_tags=[]
        self._voice_explicit=False
        self.voice_delivery_status='neutral'
        self._voice_delivery_used=False
        self._voice_tag_budget=2
        self.applied_voice_tags=[]

    def capture_performance(self, arguments):
        if not isinstance(arguments,dict):return
        # Model schemas carry a stable symbol, never a changing request ID. Bind only
        # at the trusted per-turn output boundary, then retain strict validators.
        arguments=dict(arguments)
        for field in ('emotion','interaction'):
            value=arguments.get(field)
            if isinstance(value,dict) and value.get('reason_ref')=='current_turn':
                arguments[field]={**value,'reason_ref':self.request_id}
        if getattr(self,'audio_tags_enabled',False):
            voice=validate_voice_delivery(arguments.get('voice_delivery'))
            if not self._voice_delivery_used:
                self.voice_tags=voice or []
                self._voice_explicit=voice is not None
                self.voice_delivery_status='selected' if voice else 'invalid' if arguments.get('voice_delivery') is not None and voice is None else 'neutral'
            elif voice:self.voice_delivery_status='late_hint_ignored'
        if self.alive_hints or getattr(self,'alive_capture_enabled',False):
            from alive.contracts import interaction, emotion
            classified=interaction(arguments.get('interaction'),self.request_id)
            if classified:self.interaction_classification=classified
            affect=emotion(arguments.get('emotion'),self.request_id)
            if affect and getattr(self,'alive_capture',None):self.alive_capture(affect)
            # A grounded negative reaction must reach this reply, not only a later idle poll.
            if affect and (affect['affect']=='concern' and affect.get('cause')=='user_difficulty' and affect['strength']*affect['confidence']>=.48 or affect['affect']=='disappointment' and affect.get('cause')=='own_mistake' and affect['confidence']>=.7 and affect['strength']>=.3):
                arguments['performance']={'expression':'Sad','scope':'reply','afterglow_ms':3000}
                self.mood_voice_tags=['sad']
                if not self._voice_delivery_used:
                    self.voice_tags=['sad'];self._voice_explicit=True
                    self.voice_delivery_status='grounded_reaction'
        if not self.alive_hints:return
        from alive.contracts import performance, emotion
        decision=arguments.get('continue_conversation')
        self.reply_continuation=decision if type(decision) is bool else None
        hint=performance(arguments.get('performance'))
        if not self._performance_locked:
            self.performance=hint
            self.performance_status='selected' if hint else 'invalid_hint' if arguments.get('performance') is not None else 'auto_no_hint'
        elif hint and not self.performance:
            self.performance_status='late_hint_ignored' 

    async def speak(self, text):
        delivery=None
        if self.voice_delivery_status=='grounded_reaction':text=speech_text(text)
        if getattr(self,'audio_tags_enabled',False):
            cues=self.voice_tags if self._voice_explicit else getattr(self,'mood_voice_tags',[])
            # Same streaming path and tag budget. A specific model/inline delivery wins.
            if tags_in(text):cues=[]
            delivery=render_voice(text,cues if not self._voice_delivery_used else (),self._voice_tag_budget)
            if cues and not self._voice_explicit:self.voice_delivery_status='mood_baseline'
        text = speech_text(text)
        if not text:
            return
        if not self.spoken:
            print(f"[Latency] first assistant text: {time.perf_counter() - self.started_at:.3f}s")
        if getattr(self,'audio_tags_enabled',False):
            applied=tags_in(delivery or '')
            self.applied_voice_tags.extend(applied)
            if applied and not self.voice_tags:self.voice_delivery_status='inline'
            self._voice_tag_budget=max(0,self._voice_tag_budget-len(applied))
            self._voice_delivery_used=True
        self._performance_locked = True
        self.spoken.append(text)
        event={"type": "assistant_text", "text": text + " "}
        if self.alive_hints:
            event.update(request_id=self.request_id, utterance_id=uuid.uuid4().hex)
            if self.performance:event['performance']=dict(self.performance)
            if len(self.spoken)==1:
                print('[Alive reply] '+__import__('json').dumps({'request_id':self.request_id,'utterance_id':event['utterance_id'],'expression':(self.performance or {}).get('expression'),'decision':self.performance_status,'continue_conversation':self.reply_continuation,'voice_tags':self.applied_voice_tags},separators=(',',':')),flush=True)
        if delivery and delivery!=text:event['tts_text']=delivery
        await self.emit(event)
        self.speech_started.set()

    async def tool(self, name, inputs, execute):
        call_id = uuid.uuid4().hex
        self.tool_count += 1
        await self.emit({"type": "tool_start", "call_id": call_id,
                         "name": name, "arguments": inputs})
        try:
            result = await execute()
        except asyncio.CancelledError:
            # An already dispatched HA action can outlive a cancelled request.
            raise
        except Exception as error:
            result = f"Tool failed: {type(error).__name__}: {error}"
        self.tool_events.append({"name": name, "arguments": inputs, "result": str(result)})
        await self.emit({"type": "tool_result", "call_id": call_id,
                         "name": name, "result": str(result)})
        return str(result)

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def record_model_call():
    output=current_output.get()
    if output: output.model_calls += 1
