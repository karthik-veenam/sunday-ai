"""Persistent conversation runtime backed by a private copy of Sunday's brain."""
import asyncio
from collections import OrderedDict
import json
import hashlib
import copy
from pathlib import Path
import re
import uuid
import jsonschema

from agent import Agent
from background import Outbound
from home_model import HomeModel
from presence import PresenceProvider
from world import WorldModel
from historical import HistoricalEvidenceProvider
from telegram_transport import TelegramTransport
from types import SimpleNamespace
import home_tools
from fast_mode import FastMode, compare_shadow
import camera_tools
try:
    import dev_mode_tools         # dev mode: voice routed to Codex on the projector page (optional)
except Exception:
    dev_mode_tools = None
try:
    import body_commands          # fast voice commands for the body's volume and battery (optional: never blocks start-up)
except Exception:
    body_commands = None
from config import Config
from ha import HAClient, ha_context
from jobs import Jobs
import memory
from output import TurnOutput, current_output
import tools
import web
import wiki_io

ROOT = Path(__file__).parent


class Runtime:
    def __init__(self, config, agent_factory=None):
        self.config = config
        self.costs = None
        self.ha = HAClient(config.ha_url, config.ha_token, config.allow_device_actions)
        self.agent_factory = agent_factory or self.make_agent
        from reply_models import ReplyModels
        self.reply_models=ReplyModels(config)
        self.agents = OrderedDict()
        self.active = {}
        self.completed = OrderedDict()
        self.receipts_path = ROOT / "state" / "receipts.json"
        if self.receipts_path.exists():
            self.completed.update(json.loads(self.receipts_path.read_text()))
        self.histories = ROOT / "state" / "conversations.json"
        self.saved_histories = json.loads(self.histories.read_text()) if self.histories.exists() else {}
        self.background_tasks = []
        self.telegram_task = None
        memory.init(str(ROOT / "state" / "sunday.db"))
        tools.set_ha_client(self.ha)
        tools.set_tavily_api_key(config.tavily_api_key)
        tools.set_calendar_urls(config.outlook_ics_url, config.gcal_ics_url)
        if (ROOT / "calendar_token.json").exists():
            tools.set_gcal_token_path(str(ROOT / "calendar_token.json"))
        self.outbound = Outbound(config, self.ha, lambda: bool(self.active))
        self.jobs = Jobs(ROOT / "state" / "jobs.db", self.outbound.deliver)
        tools.set_jobs(self.jobs)
        self.engine = SimpleNamespace(speaking=False)
        from alive.service import Service as AliveService
        self.alive = AliveService(ROOT / "state" / "life.db")
        self.household = json.loads((ROOT / 'household.json').read_text())
        self.home = HomeModel(self.ha, self.household, config.ha_config_path)
        from phrase_automations import PhraseAutomations
        self.phrase_automations = PhraseAutomations(self.ha, config.ha_config_path)
        home_tools.configure(self.home)
        self.presence = PresenceProvider(self.ha, self.household)
        self.world = WorldModel(ROOT / 'state' / 'world.db', self.home, self.presence, config) if config.world_model_enabled else None
        if self.world:self.world.bus.subscribe(self.alive.on_world_event)
        self.ha.world = self.world
        self.home.world = self.world
        self.history = HistoricalEvidenceProvider(self.home,self.presence,self.world,config,ROOT / 'state' / 'history_queries.db') if config.historical_evidence_enabled else None
        self.home.history = self.history
        from state_notifications.service import Service as StateNotifications
        from state_notifications import tools as notification_tools
        self.state_notifications = StateNotifications(self.home, config, ROOT / 'state')
        notification_tools.configure(self.state_notifications)
        self.device_patterns = None
        self.input_policy = None
        self.input_controls_lock = asyncio.Lock()
        self.learned_memory = None
        if config.memory_write_enabled or config.memory_read_enabled:
            from memory_v1.service import MemoryService
            self.learned_memory = MemoryService(ROOT / 'state' / 'memory_v1.db', config, self.history, self.home, self.presence)
        self.proactivity = None
        if config.proactive_cognition_enabled or config.ha_attention_enabled or config.expectation_engine_enabled or config.self_watch_enabled:
            from proactivity_v1.service import Service as Proactivity
            self.proactivity = Proactivity(ROOT / 'state' / 'proactivity_v1.db', config,
                world=self.world, home=self.home, presence=self.presence, history=self.history,
                memory=self.learned_memory, conversations=lambda: [m for value in self.saved_histories.values()
                    for m in value.get('messages', []) if isinstance(m, dict) and m.get('role') in ('user', 'assistant')])
            if self.world:
                self.world.bus.subscribe(self.proactivity.on_event)
            if self.learned_memory:
                self.learned_memory.on_change = self.proactivity.scan_expectations
        home_tools.configure(self.home)
        self.telegram = TelegramTransport(self, ROOT / 'state' / 'telegram.db') if config.telegram_enabled else None
        if self.proactivity and self.telegram:
            from proactivity_v1.delivery import read_controls
            self.telegram.primary_person = self.household.get('primary_person','Karthik')
            self.proactivity.bind_telegram(self.telegram, controls=lambda: read_controls(ROOT / 'config.json'), on_delivered=self.record_proactive_delivery)
        self.fast_mode = FastMode(ROOT / 'state' / 'fast_mode.db') if config.fast_mode_enabled else None
        self.compatibility = None
        self.compat_runner = None
        self.schemas = {d["name"]: d["input_schema"] for d in tools.DEFINITIONS}

    def record_proactive_delivery(self,receipt):
        """Add a delivered assistant utterance to the existing Telegram conversation."""
        chat=self.proactivity.delivery.destination();key='telegram:'+chat;data=receipt['data']
        if key in getattr(self,'active',{}).values():return # Preserve an in-flight turn's model/tool ordering.
        saved=self.saved_histories.setdefault(key,{'provider':self.config.llm_provider,'messages':[]})
        ids=saved.setdefault('proactive_delivery_ids',[])
        if receipt['id'] not in ids:
            message={'role':'assistant','content':data['message']}
            if self.config.llm_provider=='gemini':
                message={'role':'model','parts':[{'text':data['message']}]}
            if key in self.agents:
                backend=self.agents[key]._backend
                backend.history.append(message if self.config.llm_provider=='openai' else backend._types.Content.model_validate(message))
            else:saved['messages'].append(message)
            ids.append(receipt['id']);saved['proactive_delivery_ids']=ids[-128:];self.persist()
        self.proactivity.store.change('deliveries',receipt['id'],data=data|{'conversation_linked':True,'conversation_ref':'existing-primary-telegram-thread'})

    def make_agent(self):
        return Agent(self.config.google_api_key, provider=self.config.llm_provider,
                     openai_api_key=self.config.openai_api_key,
                     openai_voice_model=self.config.openai_voice_model,
                     openai_chat_model=self.config.openai_chat_model,
                     openai_voice_service_tier=self.config.openai_voice_service_tier,
                     anthropic_api_key=self.config.anthropic_api_key)

    def agent(self, conversation_id):
        if conversation_id not in self.agents:
            agent = self.agent_factory()
            saved = self.saved_histories.get(conversation_id, {})
            if hasattr(agent, "_backend") and isinstance(saved, dict) and saved.get("provider") == self.config.llm_provider:
                messages = saved.get("messages", [])
                agent._backend.history = (messages if self.config.llm_provider == "openai" else
                    [agent._backend._types.Content.model_validate(m) for m in messages])
            self.agents[conversation_id] = agent
        self.agents.move_to_end(conversation_id)
        if len(self.agents) > 64:
            self.agents.popitem(last=False)
        return self.agents[conversation_id]

    def persist(self):
        for key, agent in self.agents.items():
            if hasattr(agent, "_backend"):
                if self.config.llm_provider == 'openai':
                    from agent import _without_capture_history
                    agent._backend.history = _without_capture_history(agent._backend.history)
                if self.config.llm_provider == 'gemini':
                    agent._backend._clean_capture_history()
                messages = agent._backend.history[-80:]
                if self.config.llm_provider == "gemini":
                    messages = [m.model_dump(mode="json", exclude_none=True) for m in messages]
                linked=self.saved_histories.get(key,{}).get("proactive_delivery_ids",[])
                self.saved_histories[key] = {"provider": self.config.llm_provider, "messages": messages,"proactive_delivery_ids":linked}
        # Limit transient conversation storage; durable memories are elsewhere.
        self.saved_histories = dict(list(self.saved_histories.items())[-128:])
        temp = self.histories.with_suffix(".tmp")
        temp.write_text(json.dumps(self.saved_histories))
        temp.chmod(0o600)
        temp.replace(self.histories)

    def persist_receipts(self):
        temp = self.receipts_path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.completed))
        temp.chmod(0o600)
        temp.replace(self.receipts_path)

    async def turn(self, request, emit):
        if not isinstance(request, dict) or request.get("protocol", 1) != 1:
            raise ValueError("Expected a protocol-1 request object.")
        supplied_id = request.get("request_id")
        if supplied_id is not None and not isinstance(supplied_id, str):
            raise ValueError("Invalid request ID.")
        request_id = supplied_id or uuid.uuid4().hex
        if not isinstance(request_id, str) or len(request_id) > 128:
            raise ValueError("Invalid request ID.")
        if not isinstance(request.get("text"), str):
            raise ValueError("Text must be a string.")
        text = request["text"].strip()
        if not text or len(text) > 20000:
            raise ValueError("Text must contain between 1 and 20000 characters.")
        fingerprint = hashlib.sha256(json.dumps({k: request.get(k) for k in
                    ("text", "conversation_id", "satellite_id", "source")}, sort_keys=True).encode()).hexdigest()
        conversation_id = request.get("conversation_id") or uuid.uuid4().hex
        if not isinstance(conversation_id, str) or len(conversation_id) > 128:
            raise ValueError("Invalid conversation ID.")
        if request_id in self.completed:
            # Never replay tools when a client repeats an already completed turn.
            saved = self.completed[request_id]
            if saved["fingerprint"] != fingerprint:
                raise ValueError("Request ID was reused with different input.")
            if saved.get("status") != "completed":
                raise ValueError("Previous turn did not complete; action outcomes need review before retrying.")
            await emit({**saved["result"], "replayed": True})
            return
        endpoint = request.get("satellite_id") or conversation_id
        if not isinstance(endpoint, str) or len(endpoint) > 256:
            raise ValueError("Invalid satellite ID.")
        if endpoint in self.active or conversation_id in self.active.values():
            raise RuntimeError("This endpoint/conversation already has an active turn.")
        self.active[endpoint] = conversation_id
        self.completed[request_id] = {"fingerprint": fingerprint, "status": "started"}
        self.persist_receipts()
        self.engine.speaking = True
        output = TurnOutput(request_id, emit, request.get("satellite_id"))
        output.codex_conversation=conversation_id
        output.codex_user_text=text
        output.codex_source=request.get("source","unknown")
        from cost_telemetry.tracking import category_for
        output.telemetry_category = category_for(request)
        output.telemetry_started = __import__('time').time()
        output.extra_context = str(request.get("extra_system_prompt") or "")[:8000]
        output.read_only = bool(request.get("read_only", False))
        try:
            voice_delivery=json.loads(Path('/home/djpi/sunday/tts_provider.json').read_text())
            output.audio_tags_enabled=request.get('source')=='voice' and voice_delivery.get('provider')=='elevenlabs' and voice_delivery.get('audio_tags') is True
        except Exception:output.audio_tags_enabled=False
        output.execution_path = 'normal_tool'
        output.initiator = {'type':'UNKNOWN'}
        if request.get('source')=='telegram' and conversation_id.startswith('telegram:'):
            person=(self.config.telegram_users or {}).get(conversation_id.split(':',1)[1])
            if person:output.initiator={'type':'USER','id':person}
        elif request.get('source')=='ha' and isinstance(request.get('context'),dict) and request['context'].get('user_id'):
            output.initiator={'type':'USER','id':request['context']['user_id']}

        self.alive.bind(output, request.get("source"))
        output_token = current_output.set(output)
        context_token = ha_context.set(request.get("context"))
        agent = None
        initial_history = None
        fast_record = None
        try:
            await emit({"type": "started", "conversation_id": conversation_id})
            await web.emit({"type": "transcript", "text": text, "conversation_id": conversation_id})
            memory.get().log_message("user", text)
            snapshot = await self.presence.get_presence()
            if self.world:self.world.ingest_presence(snapshot)
            output.presence_snapshot = snapshot
            output.presence_context = snapshot.prompt_context(text)
            goodbye = bool(re.fullmatch(r"(?:goodbye|bye sunday|that's all|thats all|stop listening)[.!?]*", text.lower()))
            if not goodbye and not output.extra_context:
                fast_record = await self.phrase_automations.attempt(request, output)
            if not fast_record and not goodbye and not output.extra_context:
                fast_record = await camera_tools.attempt(request, output)
            if not fast_record and not goodbye and not output.extra_context and body_commands:
                fast_record = await body_commands.attempt(request, output)
            if not fast_record and not goodbye and not output.extra_context and dev_mode_tools:
                fast_record = await dev_mode_tools.attempt(request, output)
            if not fast_record and self.fast_mode and not goodbye and not output.extra_context:
                fast_record = await self.fast_mode.attempt(self.home, text, conversation_id, request_id,
                    request.get('source','ha'), self.config.fast_mode_execute, output.read_only, self.config.fast_mode_stage, presence=snapshot, **({'contextual_ambiguity':True} if self.config.conversation_learning_enabled else {}))
            if fast_record and fast_record.get('handled'):
                output.alive_fast_utility=fast_record.get('decision') not in ('CLARIFY','REJECT','ERROR')
                await output.speak(fast_record['reply'])
                keep_listening = fast_record['decision']=='CLARIFY'
                # Preserve dialogue continuity without constructing/calling a provider.
                if self.config.llm_provider=='openai':
                    if conversation_id in self.agents:
                        history=self.agents[conversation_id]._backend.history
                    else:
                        saved=self.saved_histories.setdefault(conversation_id,{'provider':'openai','messages':[]})
                        history=saved['messages']
                    history.extend([{'role':'user','content':text},{'role':'assistant','content':fast_record['reply']}])
            elif goodbye:
                agent=self.agent(conversation_id)
                self.reply_models.apply(agent,request.get("source"))
                agent.reset()
                await output.speak("Alright. Call me when you need me.")
                keep_listening = False
            else:
                if self.learned_memory:
                    person = output.initiator.get('id')
                    if self.config.memory_write_enabled and not output.read_only:
                        output.memory_capture = lambda args: self.learned_memory.capture(args,
                            request_id=request_id, user_text=text, source=request.get('source', 'ha'), person=person, conversation_id=conversation_id, context_messages=initial_history or [])
                        output.memory_learning = bool(self.config.conversation_learning_enabled)
                        output.memory_capture_status = self.learned_memory.capture_status
                agent=self.agent(conversation_id)
                self.reply_models.apply(agent,request.get("source"))
                initial_history=copy.deepcopy(agent._backend.history) if hasattr(agent,'_backend') else None
                output.home_context = await self.home.context(text)
                import speech_mode_tools
                from body_context import prompt_context as body_prompt_context
                try:output.body_context = await body_prompt_context(self.home)
                except Exception:output.body_context = 'Body status unavailable for this turn; do not infer current body state.'
                output.body_context += '\n'+speech_mode_tools.context()
                if self.world:
                    # Keep current state for home questions without leaking an
                    # inventory into unrelated work/general-knowledge requests.
                    from home_model import snapshot_relevant
                    if self.config.world_context_injection_enabled and not snapshot_relevant(text,self.home):
                        facts={k:v for k,v in self.household.items() if k in ('primary_person','relationships')}
                        primary=self.household['primary_area'];names=self.household.get('area_names',{}).get(primary,[])
                        facts['shared_room']={'name':names[0] if names else primary,'residents':self.household.get('occupants',{}).get(primary,[])}
                        output.home_context=json.dumps({'household':facts,'room_snapshot':self.home.room_snapshot()})
                    try:
                        projection=await self.world.relevant_world_context(text, request_id=request_id)
                        if self.config.world_context_injection_enabled and projection:
                            output.home_context+='\nRelevant World facts and causal evidence (use fresh state observations; unknown is not a cause):\n'+projection
                    except Exception:
                        if self.config.world_context_injection_enabled:
                            output.home_context+='\nWorld causal evidence unavailable. Use the current home snapshot for present states; do not infer missing causes.'

                from home_controls import relevant_controls
                controls=relevant_controls(self.home,text)
                if controls:
                    output.home_context+='\nRelevant named program controls (settings, not physical outputs):\n'+json.dumps(controls,default=str)
                if self.history:
                    try:
                        from presence import contextual_text
                        historical=(fast_record or {}).get('historical_evidence')
                        if historical is None:
                            plan=self.history.plan_text(contextual_text(text,snapshot))
                            if plan:historical=await self.history.query(**__import__('dataclasses').asdict(plan))
                        if historical is not None:
                            output.home_context+='\nHistorical factual evidence (calculated, bounded; respect coverage and unknown causes; answer naturally, keeping source mechanics internal and mentioning only material gaps):\n'+json.dumps(self.history.projection(historical),default=str)
                    except ValueError:pass
                if self.learned_memory:
                    try:
                        learned_context = self.learned_memory.turn_context(text, request_id, person=output.initiator.get('id'))
                        if learned_context:
                            output.home_context += '\n' + learned_context
                    except Exception:
                        pass  # Memory failure must not break normal replies or control.
                reply = None  # Shared generic HA tools own device resolution.
                if reply is not None:
                    await output.speak(reply)
                    agent.reset()
                    keep_listening = False
                else:
                    if request.get("source") == "telegram":
                        await output.speak(await agent.respond_as_text(text))
                        keep_listening = False
                    else:
                        keep_listening = await agent.process(text, output)
            speech = " ".join(output.spoken)
            if not speech:
                speech = "I couldn't finish that response. Please try again."
                await output.speak(speech)
                keep_listening = False
            result = {"type": "final", "conversation_id": conversation_id,
                      "text": speech, "continue_conversation": bool(keep_listening), "model_calls": output.model_calls}
            if output.alive_hints:
                result['performance']=output.performance
                result['performance_status']=output.performance_status
                result['model_continuation']=output.reply_continuation
            if output.audio_tags_enabled:
                result['voice_delivery']={'tags':list(output.applied_voice_tags),'decision':output.voice_delivery_status}
            self.persist()
            memory.get().log_message("assistant", speech)
            memory.get().emit_event("voice_done", {"user_said": text, "source": request.get("source", "ha")})
            self.completed[request_id] = {"fingerprint": fingerprint, "status": "completed", "result": result}
            if len(self.completed) > 256:
                self.completed.popitem(last=False)
            self.persist_receipts()
            self.alive.completed(output)
            await emit(result)
        except asyncio.CancelledError:
            if initial_history is not None:
                agent._backend.history = initial_history
            memory.get().emit_event("turn_cancelled", {"request_id": request_id,
                              "note": "Dispatched HA actions may have completed; do not replay blindly."})
            raise
        finally:
            if self.costs:
                from cost_telemetry.tracking import request_ref
                try:
                    self.costs.turn(request_ref(request_id), output.telemetry_started, output.telemetry_category,
                        'completed' if self.completed.get(request_id,{}).get('status')=='completed' else 'interrupted',
                        'phrase' if (fast_record or {}).get('decision')=='PHRASE_AUTOMATION' else 'fast' if (fast_record or {}).get('handled') else 'normal', output.model_calls)
                except Exception:
                    self.costs.errors += 1
            if self.history and getattr(output,'historical_retrieved',False):
                self.history.record_answer(normal=not bool((fast_record or {}).get('historical') and (fast_record or {}).get('handled')),model_calls=max(0,output.model_calls-getattr(output,'history_base_model_calls',0)))
            if fast_record:
                fast_record['model_calls']=output.model_calls
                fast_record['actual_tools']=output.tool_events
                fast_record['shadow_comparison']=compare_shadow(fast_record,output.tool_events)
                fast_record['turn_latency_ms']=(__import__('time').perf_counter()-output.started_at)*1000
                fast_record['turn_completed']=self.completed.get(request_id,{}).get('status')=='completed'
                try:self.fast_mode.save(fast_record)
                except Exception:pass  # Receipts still prohibit redispatch; cleanup must always run.
            await output.close()
            current_output.reset(output_token)
            ha_context.reset(context_token)
            self.active.pop(endpoint, None)
            if self.proactivity and self.proactivity.delivery:
                try:
                    pending=self.proactivity.store.db.execute("select id from deliveries where state='DELIVERED' and coalesce(json_extract(data,'$.conversation_linked'),0)=0 order by created limit 30").fetchall()
                    for (delivery_id,) in pending:self.record_proactive_delivery(self.proactivity.store.get('deliveries',delivery_id))
                except Exception:
                    pass # Delivery is durable; a conversation-link failure never re-sends.
            self.engine.speaking = bool(self.active)
            await web.emit({"type": "idle"})

    async def context_loop(self):
        while True:
            try:
                await self.home.refresh()
                rows=self.home.search('',limit=30)
                await web.emit({'type':'context','devices':{e['entity_id']:e['state'] for e in rows}})
            except Exception:
                pass
            await asyncio.sleep(60)

    async def start(self):
        await self.alive.start()
        from cost_telemetry.store import Ledger
        from cost_telemetry.tracking import configure, refresh_prices
        try:
            self.costs = Ledger(ROOT / 'state' / 'costs.db', self.config.timezone)
        except Exception:
            print('[Sunday costs] Telemetry unavailable; normal service continues.')
        configure(self.costs)
        if self.config.compatibility_enabled:
            from aiohttp import web as http
            from compatibility import Compatibility
            self.compatibility = Compatibility(self, ROOT / 'state' / 'compatibility.db')
            self.compat_runner = http.AppRunner(self.compatibility.app(self.config.compatibility_token))
            await self.compat_runner.setup()
            await http.TCPSite(self.compat_runner, self.config.compatibility_host, self.config.compatibility_port).start()
            await self.compatibility.resume()
        if self.world:await self.world.start()
        await self.home.refresh()
        from input_policy import InputPolicy
        self.input_policy = InputPolicy(ROOT / 'state' / 'input_policy.json', self.home, self.config)
        self.input_policy.initialize()
        self.home.input_policy = self.input_policy
        if self.config.device_patterns_enabled and self.world:
            from device_patterns.service import Service as DevicePatterns
            self.device_patterns=DevicePatterns(ROOT/'state'/'device_patterns.db',self.home,self.world,self.config,self.learned_memory,busy=lambda:bool(self.active))
            await self.device_patterns.start()
        if self.learned_memory:
            await self.home.refresh()
            self.learned_memory.enforce_scope()
        if self.proactivity:await self.proactivity.start()
        self.background_tasks = [asyncio.create_task(self.jobs.loop()), asyncio.create_task(self.context_loop())]
        if self.costs:
            self.background_tasks.append(asyncio.create_task(refresh_prices(self.costs), name='cost-pricing-refresh'))
        if self.learned_memory:
            self.background_tasks.append(asyncio.create_task(self.learned_memory.loop(lambda: bool(self.active)), name='memory-v1-maintenance'))
        if self.config.background_enabled:
            raise RuntimeError('Autonomous cognition is deferred; background_enabled must be false')
        if self.telegram:
            # Preflight runs synchronously so ownership failure prevents startup.
            self.telegram.acquire()
            self.telegram_task = asyncio.create_task(self.telegram.loop(), name="telegram-poller")
            self.background_tasks.append(self.telegram_task)

    async def close(self):
        await self.alive.close()
        if self.compat_runner:
            await self.compat_runner.cleanup()
        if self.compatibility:
            await self.compatibility.close()
        for task in self.background_tasks:
            task.cancel()
        await asyncio.gather(*self.background_tasks, return_exceptions=True)
        self.persist()
        if self.device_patterns:await self.device_patterns.close()
        if self.proactivity:await self.proactivity.close()
        if self.learned_memory:await self.learned_memory.shutdown()
        if self.history:self.history.close()
        if self.world:await self.world.close()
        await self.ha.close()
        if wiki_io._client:
            await wiki_io._client.close()
        for agent in list(self.agents.values()):
            client = getattr(getattr(agent, "_backend", None), "_client", None)
            if client:
                await client.close()
            elif self.config.llm_provider == "gemini" and hasattr(agent, "_backend"):
                await agent._backend.client.aio.aclose()
        if self.telegram:
            await self.telegram.close()
        if self.fast_mode:self.fast_mode.db.close()
        self.jobs.close()
        memory.get()._conn.close()
        if self.costs:
            from cost_telemetry.tracking import configure
            configure(None)
            self.costs.close()
