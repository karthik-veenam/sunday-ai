"""Authenticated local daemon and dashboard. No audio process is created."""
import asyncio
import hmac
import json
from pathlib import Path
import uuid
from aiohttp import web as http, WSMsgType
from config import Config
from runtime import Runtime
import web as ui


def create_app(runtime):
    @http.middleware
    async def auth(request, handler):
        if request.path in ("/", "/health"):
            return await handler(request)
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if not hmac.compare_digest(supplied, runtime.config.daemon_token):
            raise http.HTTPUnauthorized()
        return await handler(request)

    app = http.Application(middlewares=[auth], client_max_size=128 * 1024)

    async def health(request):
        task = getattr(runtime, "telegram_task", None)
        reply=runtime.reply_models.status() if hasattr(runtime,'reply_models') and runtime.config.llm_provider=='openai' else None
        poller_failed = runtime.config.telegram_enabled and (task is None or task.done())
        return http.json_response({"name": "Sunday 2.0", "version": "0.1.0", "status": "degraded" if poller_failed else "ready",
                    "telegram_poller": "failed" if poller_failed else "running" if task else "disabled",
                    "provider": reply["provider"] if reply else runtime.config.llm_provider, "model": reply["model"] if reply else ("gemini-3.8-flash" if runtime.config.llm_provider == "gemini" else runtime.config.openai_voice_model),
                    "reply_model":runtime.reply_models.status() if hasattr(runtime,'reply_models') else None,
                    "active_turns": len(runtime.active), "outbound_speech": runtime.config.outbound_speech_enabled}, status=503 if poller_failed else 200)

    async def reply_model(request):
        if request.method=='GET':return http.json_response(runtime.reply_models.status() if hasattr(runtime,'reply_models') else None)
        data=await request.json()
        if not isinstance(data,dict) or set(data)!={'selected'} or not isinstance(data['selected'],str):raise http.HTTPBadRequest()
        if runtime.active:raise http.HTTPConflict(text='Wait for the current reply to finish.')
        try:return http.json_response(await runtime.reply_models.select(data['selected']))
        except ValueError as error:raise http.HTTPBadRequest(text=str(error))
        except OSError:raise http.HTTPServiceUnavailable(text='Model setting could not be saved.')

    async def codex_deliver(request):
        import codex_delivery
        data=await request.json()
        if not isinstance(data,dict) or set(data)!={"job_id"} or not isinstance(data["job_id"],str):raise http.HTTPBadRequest()
        return http.json_response(await codex_delivery.deliver(runtime,data["job_id"]))

    async def audio_noise(request):
        import time
        try:
            data=json.loads(Path('/home/djpi/sunday-alive/audio_level.json').read_text())
            age=max(0,time.time()-float(data['at']));data.update(available=age<20,age_seconds=round(age,1))
            if age>=20:data.update(sound_dbfs=None,noise_floor_dbfs=None)
            return http.json_response(data)
        except (OSError,ValueError,KeyError,TypeError):
            return http.json_response({'available':False,'sound_dbfs':None,'noise_floor_dbfs':None})

    async def alive_status(request):
        alive=runtime.alive
        return http.json_response({'enabled':alive.enabled,'flags':alive.flags,'errors':alive.errors,'dropped':alive.dropped,'queue':alive.queue.qsize(),'snapshot':alive.cached,'sleep':getattr(alive,'sleep_cached',None),'reply_model':runtime.reply_models.status() if hasattr(runtime,'reply_models') else None})

    settings_lock=asyncio.Lock()
    async def alive_settings(request):
        import os,tempfile
        data=await request.json()
        if not isinstance(data,dict) or set(data)!={'idle_sounds'} or type(data['idle_sounds']) is not bool:raise http.HTTPBadRequest()
        def save():
            p=Path('/home/djpi/sunday-alive/config.json');cfg=json.loads(p.read_text());cfg['idle_sounds']=data['idle_sounds']
            fd,name=tempfile.mkstemp(dir=p.parent,prefix='.alive-setting-')
            try:
                with os.fdopen(fd,'w') as f:json.dump(cfg,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
                os.chmod(name,0o600);os.replace(name,p)
            finally:
                if os.path.exists(name):os.unlink(name)
        async with settings_lock:
            try:await asyncio.to_thread(save)
            except Exception:raise http.HTTPServiceUnavailable(text='Setting could not be saved')
            runtime.alive.flags['idle_sounds']=data['idle_sounds']
        return http.json_response({'ok':True,'idle_sounds':data['idle_sounds']})

    async def alive_sleep(request):
        import math,time
        data=await request.json()
        if not isinstance(data,dict) or len(data)>30:raise http.HTTPBadRequest()
        states=('Offline','Awake','Drowsy','Engaged','Temporarily awake','Falling asleep','Asleep','Waking up')
        if data.get('state') not in states or not isinstance(data.get('reasons'),list) or len(data['reasons'])>5:raise http.HTTPBadRequest()
        if any(not isinstance(x,str) or len(x)>240 for x in data['reasons']):raise http.HTTPBadRequest()
        for key in ('sleepiness','updated_at'):
            n=data.get(key)
            if isinstance(n,bool) or not isinstance(n,(float,int)) or not math.isfinite(n):raise http.HTTPBadRequest()
        if not 0<=data['sleepiness']<=100 or not 0<=time.time()-data['updated_at']<=60:raise http.HTTPBadRequest()
        runtime.alive.sleep_cached={key:data.get(key) for key in ('state','sleepiness','reasons','woken_from_sleep','desired_night','updated_at')}
        return http.json_response({'ok':True})

    async def alive_event(request):
        from alive.service import Event
        if not runtime.alive.enabled:raise http.HTTPNotFound()
        data=await request.json()
        # This endpoint accepts only anonymous authenticated body touch references.
        if not isinstance(data,dict) or set(data)!={'event_id','kind','ref'} or data['kind']!='touch':raise http.HTTPBadRequest()
        try:accepted=runtime.alive.submit(Event(data['event_id'],'touch',data['ref']))
        except (ValueError,TypeError):raise http.HTTPBadRequest()
        return http.json_response({'accepted':accepted})

    async def index(request):
        return http.FileResponse(Path(__file__).parent / "index.html")

    async def chat(request):
        data = await request.json()
        data.setdefault("request_id", uuid.uuid4().hex)
        if data.get("stream"):
            response = http.StreamResponse(headers={"Content-Type": "application/x-ndjson"})
            await response.prepare(request)
            async def stream_emit(event):
                await response.write((json.dumps(event) + "\n").encode())
            try:
                async with asyncio.timeout(120):
                    await runtime.turn(data, stream_emit)
            except Exception as error:
                await stream_emit({"type": "error", "error": type(error).__name__})
            await response.write_eof()
            return response
        events = []
        async def emit(event):
            events.append(event)
        try:
            async with asyncio.timeout(120):
                await runtime.turn(data, emit)
        except (ValueError, RuntimeError) as error:
            return http.json_response({"error": str(error)}, status=400)
        final = next((event for event in reversed(events) if event["type"] == "final"), None)
        return http.json_response({"result": final, "events": events})

    async def turns(request):
        ws = http.WebSocketResponse(heartbeat=30, max_msg_size=128 * 1024)
        await ws.prepare(request)
        task = None
        request_id = None
        sequence = 0
        async def emit(event):
            nonlocal sequence
            sequence += 1
            await ws.send_json({"protocol": 1, "request_id": request_id, "sequence": sequence, **event})
        async def run_turn(data):
            try:
                async with asyncio.timeout(120):
                    await runtime.turn(data, emit)
            except asyncio.CancelledError:
                if not ws.closed:
                    await emit({"type": "cancelled"})
                raise
            except Exception as error:
                if not ws.closed:
                    await emit({"type": "error", "error": f"{type(error).__name__}: {error}"})
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                if data.get("type") == "cancel":
                    if task and data.get("request_id") == request_id:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                elif data.get("type") == "begin_turn":
                    if task and not task.done():
                        await emit({"type": "error", "error": "A turn is already active on this connection."})
                        continue
                    request_id = data.get("request_id") or uuid.uuid4().hex
                    data["request_id"] = request_id
                    sequence = 0
                    task = asyncio.create_task(run_turn(data))
                else:
                    await emit({"type": "error", "error": "Expected begin_turn or cancel."})
        finally:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        return ws

    async def events(request):
        ws = http.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        ui._clients.add(ws)
        try:
            for event in ui._state_cache.values():
                await ws.send_json(event)
            async for _ in ws:
                pass
        finally:
            ui._clients.discard(ws)
        return ws

    async def presence_status(request):
        snapshot=await runtime.presence.get_presence(force=request.query.get('refresh')=='1')
        return http.json_response(snapshot.diagnostic())

    async def history_status(request):
        provider=runtime.history
        return http.json_response({'enabled':bool(provider),'metrics':provider.metrics() if provider else {},'limits':{'raw_days':7,'statistics_days':366,'raw_samples':6000,'series_points':48,'concurrent_queries':2,'query_timeout_seconds':20}})

    async def history_read(request):
        import history_tools,jsonschema
        args=await request.json()
        try:jsonschema.validate(args,history_tools.DEFINITIONS[0]['input_schema'])
        except jsonschema.ValidationError:return http.json_response({'error':'Invalid historical query arguments'},status=400)
        if not runtime.history:return http.json_response({'status':'HISTORY_UNAVAILABLE'},status=503)
        return http.json_response(runtime.history.projection(await runtime.history.query(**args)))

    async def world_status(request):
        world=runtime.world
        if world is None:return http.json_response({'enabled':False})
        return http.json_response({'enabled':True,'connected':world.connected,'last_error':world.last_error,
            'metrics':world.store.metrics(),'schema_version':1,'fact_count':world.store.db.execute('select count(*) from facts').fetchone()[0],
            'last_projection':world.last_projection})

    async def world_read(request):
        import world_tools
        if runtime.world is None:return http.json_response({'error':'World Model disabled'},status=503)
        args=dict(request.query)
        return http.json_response(await world_tools.read(runtime.world,args))

    async def memory_dashboard(request):
        from memory_v1.dashboard import snapshot
        try:
            return http.json_response(snapshot(getattr(runtime, 'learned_memory', None), request.query))
        except ValueError as error:
            return http.json_response({'error':str(error)}, status=400)

    async def memory_manage(request):
        from memory_v1.management import change, Conflict
        service=getattr(runtime, 'learned_memory', None)
        if service is None:return http.json_response({'error':'Memory is disabled'},status=503)
        try:
            body=await request.json()
            if isinstance(body,dict) and body.get('action')=='sync':
                return http.json_response({'sync':service.start_sync()})
            return http.json_response(change(service,body))
        except Conflict as error:return http.json_response({'error':str(error)},status=409)
        except KeyError:return http.json_response({'error':'Memory not found'},status=404)
        except (ValueError,TypeError) as error:return http.json_response({'error':str(error)},status=400)

    async def notification_dashboard(request):
        service=getattr(runtime,'state_notifications',None)
        if not service:return http.json_response({'error':'Notifications unavailable'},status=503)
        try:return http.json_response(await service.snapshot())
        except RuntimeError:return http.json_response({'error':'Home Assistant notification configuration is unavailable'},status=502)

    async def notification_manage(request):
        from state_notifications.service import Conflict
        service=getattr(runtime,'state_notifications',None)
        if not service:return http.json_response({'error':'Notifications unavailable'},status=503)
        try:return http.json_response(await service.manage(await request.json()))
        except Conflict as error:return http.json_response({'error':str(error)},status=409)
        except (ValueError,TypeError,KeyError) as error:return http.json_response({'error':str(error)},status=400)
        except RuntimeError:return http.json_response({'error':'Home Assistant did not confirm the notification change; refresh before retrying'},status=502)

    async def device_patterns(request):
        service=getattr(runtime,'device_patterns',None)
        if not service:return http.json_response({'enabled':False,'error':'Device patterns are disabled'})
        try:return http.json_response(await service.snapshot(request.query.get('subject'),request.query.get('day')))
        except ValueError as error:return http.json_response({'error':str(error)},status=400)

    async def device_patterns_analyze(request):
        service=getattr(runtime,'device_patterns',None)
        if not service:return http.json_response({'error':'Device patterns are disabled'},status=503)
        if await request.json()!={'action':'analyze'}:return http.json_response({'error':'Expected action analyze'},status=400)
        return http.json_response(service.analyze())

    async def input_dashboard(request):
        policy=getattr(runtime,'input_policy',None)
        if policy is None:return http.json_response({'error':'Input controls are starting'},status=503)
        await runtime.home.refresh()
        return http.json_response(policy.snapshot())

    async def input_manage(request):
        from input_policy import Conflict
        policy=getattr(runtime,'input_policy',None)
        if policy is None:return http.json_response({'error':'Input controls are starting'},status=503)
        try:
            body=await request.json()
            async with runtime.input_controls_lock:
                policy.change(body)
                if getattr(runtime,'device_patterns',None):runtime.device_patterns.refresh_selection()
                if runtime.proactivity:
                    runtime.proactivity.episodes.exclude_automations=not policy.source('proactivity','automations')
                    runtime.proactivity.enforce_scope()
                    runtime.proactivity.scan_expectations()
                # Reconcile is factual only: no model call or HA action is triggered by toggling.
                if runtime.world:await runtime.world.reconcile()
                return http.json_response(policy.snapshot())
        except Conflict as error:return http.json_response({'error':str(error)},status=409)
        except (ValueError,TypeError,KeyError) as error:return http.json_response({'error':str(error)},status=400)

    async def proactivity_status(request):
        from proactivity_v1.inspection import inspect
        try:
            return http.json_response(inspect(getattr(runtime, 'proactivity', None), request.query, runtime.config))
        except ValueError as error:
            return http.json_response({'error':str(error)}, status=400)

    async def proactive_diagnostic(request):
        body=await request.json()
        if body!={'diagnostic':'telegram-delivery-test'}:
            return http.json_response({'error':'Only the fixed authorized release diagnostic is supported'},status=400)
        service=getattr(runtime,'proactivity',None)
        if not service or not service.delivery:return http.json_response({'error':'Trusted adapter unavailable'},status=503)
        return http.json_response(service.diagnostic())

    async def cost_dashboard(request):
        from cost_telemetry.dashboard import snapshot
        try:
            return http.json_response(snapshot(getattr(runtime, 'costs', None), request.query))
        except ValueError as error:
            return http.json_response({'error':str(error)}, status=400)

    async def state(request):
        return http.json_response({"events": ui._state_cache, "jobs": runtime.jobs.list(),
                                   "active_turns": len(runtime.active)})

    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_post("/v1/chat", chat)
    app.router.add_get("/v1/turns", turns)
    app.router.add_get("/v1/events", events)
    app.router.add_get("/v1/state", state)
    app.router.add_get("/v1/costs", cost_dashboard)
    app.router.add_get("/v1/notifications", notification_dashboard)
    app.router.add_post("/v1/notifications/manage", notification_manage)
    app.router.add_post("/v1/codex/deliver", codex_deliver)
    app.router.add_get("/v1/audio/noise", audio_noise)
    app.router.add_get("/v1/alive/status", alive_status)
    app.router.add_post("/v1/alive/events", alive_event)
    app.router.add_get("/v1/reply-model",reply_model)
    app.router.add_post("/v1/reply-model",reply_model)
    app.router.add_post("/v1/alive/settings", alive_settings)
    app.router.add_post("/v1/alive/sleep", alive_sleep)
    app.router.add_get("/v1/device-patterns",device_patterns)
    app.router.add_post("/v1/device-patterns/analyze",device_patterns_analyze)
    app.router.add_get("/v1/inputs", input_dashboard)
    app.router.add_post("/v1/inputs/manage", input_manage)
    app.router.add_get("/v1/memory", memory_dashboard)
    app.router.add_post("/v1/memory/manage", memory_manage)
    app.router.add_get("/v1/proactivity", proactivity_status)
    app.router.add_post("/v1/proactivity/diagnostic", proactive_diagnostic)
    app.router.add_get("/v1/presence", presence_status)
    app.router.add_get("/v1/world/status", world_status)
    app.router.add_get("/v1/history/status", history_status)
    app.router.add_post("/v1/history", history_read)
    app.router.add_get("/v1/world", world_read)
    async def start(app):
        await runtime.start()
    async def close(app):
        await runtime.close()
    app.on_startup.append(start)
    app.on_cleanup.append(close)
    return app


if __name__ == "__main__":
    config = Config.load(str(Path(__file__).parent / "config.json"))
    if not config.daemon_token:
        raise SystemExit("Configure daemon_token before starting Sunday 2.0.")
    http.run_app(create_app(Runtime(config)), host=config.daemon_host, port=config.daemon_port)
