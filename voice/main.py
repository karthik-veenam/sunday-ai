import asyncio
import os
import subprocess
import sys
import time

# Force PulseAudio to connect via the known socket (works from SSH)
os.environ["XDG_RUNTIME_DIR"] = "/run/user/1000"
os.environ["PULSE_RUNTIME_PATH"] = "/run/user/1000/pulse/"
os.environ["PULSE_SERVER"] = "unix:/run/user/1000/pulse/native"

import memory
import web as ui
from config import Config
from mic import MicStream
from clap import ClapDetector
from wake import WakeWordDetector
from wake_feedback import Capture
from ha_speech import STT, TTS
from agent import Agent
from home import HAClient
from hogar import HogarClient
from reflection import ReflectionEngine
import tools

GOODBYE = {"goodbye", "bye sunday", "that's all", "thats all", "stop listening"}

# ── Fast-path device commands (skip LLM entirely) — logic lives in tools.fast_command() ──


async def _fast_path(text: str, tts) -> bool:
    """Try to handle simple device commands without hitting the LLM.
    Returns True if handled, False to fall through to agent."""
    reply = await tools.fast_command(text)
    if reply is None:
        return False
    await tts.speak(reply)
    await ui.emit({"type": "idle"})
    return True


async def _voice_loop(
    mic: MicStream,
    wake: WakeWordDetector,
    stt: STT,
    tts: TTS,
    agent: Agent,
    config: Config,
    engine: ReflectionEngine,
    chime,
) -> None:
    async def _on_recorded():
        chime(config.done_sound)
        await ui.emit({"type": "transcribing"})

    print("[Sunday] Waiting for wake word...")

    while True:
        if not config.wake_enabled:
            await asyncio.sleep(3600)
            continue
        t_wake = time.perf_counter()
        await wake.wait_for_wake_word(mic)
        if getattr(wake,'listening_mode',None) and wake.listening_mode.muted:
            agent.reset();continue
        chime(config.awake_sound, getattr(wake, 'last_wake_trace', None))
        await ui.emit({"type": "wake"})
        if config.wakeword_test_mode:
            print("[Wake] TEST MODE — skipping STT, back to listening")
            await asyncio.sleep(1.5)
            await ui.emit({"type": "idle"})
            continue
        await asyncio.sleep(0.4)  # let chime finish + echo decay before recording

        t_stt = time.perf_counter()
        transcript = await stt.listen_and_transcribe(
            mic,
            silence_threshold=config.stt_silence_threshold,
            speech_threshold=config.stt_speech_threshold,
            silence_seconds=0.8,
            speech_start_timeout=20.0,
            max_seconds=25.0,
            on_recorded=_on_recorded,
        )
        print(f"[⏱] STT: {time.perf_counter() - t_stt:.2f}s")
        if not transcript:
            if getattr(stt, "last_error", None):
                await tts.speak("I could not transcribe that. Please try again.")
            await ui.emit({"type": "idle"})
            # Brief cooldown so chime echo decays before re-entering wake word detection
            mic.drain()
            await asyncio.sleep(1.0)
            continue
        print(f"[You] {transcript}")
        print("[Sunday] Transcribed, processing...")
        await ui.emit({"type": "transcript", "text": transcript})
        await ui.emit({"type": "thinking"})

        while True:
            if getattr(wake,'listening_mode',None) and wake.listening_mode.muted:
                agent.reset();await ui.emit({'type':'idle'});break
            if wake.feedback and await wake.feedback.correction(transcript, stt):
                await tts.speak("Got it. I saved that false activation for review.")
                agent.reset()
                await ui.emit({"type": "idle"})
                break
            if any(phrase in transcript.lower() for phrase in GOODBYE):
                await tts.speak("Goodbye! Call me if you need anything.")
                if hasattr(agent, "clear_context"):
                    agent.clear_context()
                else:
                    agent.reset()
                break

            if not getattr(agent, "handles_fast_commands", False) and await _fast_path(transcript, tts):
                agent.reset()
                break

            engine.speaking = True
            t_agent = time.perf_counter()
            keep_listening = await agent.process(transcript, tts)
            engine.speaking = False
            engine._last_spoke_at = time.time()
            print(f"[⏱] Agent total: {time.perf_counter() - t_agent:.2f}s")
            memory.get().emit_event("voice_done", {"user_said": transcript})

            if getattr(wake,'listening_mode',None) and wake.listening_mode.muted:
                agent.reset();await ui.emit({'type':'idle'});break
            if not keep_listening:
                print("[Sunday] Conversation ended by Claude — returning to wake word.")
                await ui.emit({"type": "idle"})
                agent.reset()
                break

            print("[Sunday] Listening for follow-up...")
            chime(config.awake_sound)
            await ui.emit({"type": "wake"})
            await asyncio.sleep(0.4)  # let chime finish + echo decay before recording

            t_stt = time.perf_counter()
            transcript = await stt.listen_and_transcribe(
                mic,
                silence_threshold=config.stt_silence_threshold,
                speech_threshold=config.stt_speech_threshold,
                silence_seconds=0.8,
                speech_start_timeout=3.0,
                on_recorded=_on_recorded,
            )
            if transcript:
                print(f"[⏱] STT: {time.perf_counter() - t_stt:.2f}s")

            if not transcript:
                await ui.emit({"type": "idle"})
                print("[Sunday] Waiting for wake word...")
                break
            print(f"[You] {transcript}")
            print("[Sunday] Transcribed, processing...")
            await ui.emit({"type": "transcript", "text": transcript})
            await ui.emit({"type": "thinking"})


async def _context_loop(engine=None) -> None:
    """Push room sensors + device states to UI every 60s."""
    import json as _json
    import tools as tool_registry
    while True:
        try:
            await asyncio.sleep(0)  # yield to let other tasks start first
            ctx: dict = {}
            if tool_registry._ha:
                temp_s = await tool_registry._ha.get_state("sensor.lumi_lumi_sensor_ht_agl02_temperature")
                hum_s  = await tool_registry._ha.get_state("sensor.lumi_lumi_sensor_ht_agl02_humidity")
                tv_s   = await tool_registry._ha.get_state("media_player.tv")
                try:
                    ctx["temp"] = round(float(temp_s.get("state", 0)), 1)
                except (ValueError, TypeError):
                    pass
                try:
                    ctx["humidity"] = round(float(hum_s.get("state", 0)), 1)
                except (ValueError, TypeError):
                    pass
                ctx["tv"]       = tv_s.get("state", "unknown")

            # Real device states from Hogar hub (authoritative, real-time)
            hogar_states = tool_registry._hogar.get_all_states() if tool_registry._hogar else {}
            if hogar_states:
                ctx["devices"] = {name: ("on" if s.get("on") else "off") for name, s in hogar_states.items()}
                ctx["uptimes"] = {}

            # Kasa/Tapo light strips (fresh state each loop)
            if tool_registry._kasa:
                kasa_states = await tool_registry._kasa.refresh_all_states()
                for name, state in kasa_states.items():
                    ctx.setdefault("devices", {})[name] = "on" if state.get("on") else "off"

            # Tuya devices via HA (geyser + projector)
            if tool_registry._ha:
                for _key, _entity in [("geyser",    "switch.geyser_socket_1"),
                                       ("projector", "switch.16amp_smart_plug_2_socket_1")]:
                    try:
                        _s = await tool_registry._ha.get_state(_entity)
                        _raw = _s.get("state", "off")
                        ctx.setdefault("devices", {})[_key] = "on" if _raw == "on" else "off"
                    except Exception:
                        pass
                # AC state
                try:
                    ac_s = await tool_registry._ha.get_state('climate.bedroom_ac')
                    ac_attrs = ac_s.get('attributes', {})
                    ctx['ac'] = {
                        'state': ac_s.get('state', 'off'),
                        'temperature': ac_attrs.get('temperature'),
                        'fan_mode': ac_attrs.get('fan_mode'),
                    }
                except Exception:
                    pass
                # Projector screen state — local state file is authoritative; HA scripts only for live-moving check
                try:
                    down_s  = await tool_registry._ha.get_state("script.projector_screen_down")
                    close_s = await tool_registry._ha.get_state("script.projector_screen_close")
                    down_run  = (down_s.get("attributes",{}).get("current",0) or 0) > 0
                    close_run = (close_s.get("attributes",{}).get("current",0) or 0) > 0
                    if down_run:
                        ctx["screen"] = "moving_down"
                    elif close_run:
                        ctx["screen"] = "moving_up"
                    else:
                        import json as _sj
                        try:
                            with open(os.path.join(os.path.dirname(__file__), "screen_state.json")) as _sf:
                                ctx["screen"] = _sj.load(_sf).get("state", "unknown")
                        except Exception:
                            ctx["screen"] = "unknown"
                except Exception:
                    pass
            else:
                # Fallback: infer states from last actions in memory
                import datetime as _dt
                devices: dict = {}
                uptimes: dict = {}
                now = _dt.datetime.now()
                for row in memory.get().last_actions_per_device():
                    try:
                        inp = _json.loads(row["inputs"]) if isinstance(row["inputs"], str) else row["inputs"]
                        ts = _dt.datetime.fromisoformat(row["timestamp"])
                        mins = int((now - ts).total_seconds() / 60)
                        uptime_str = f"{mins//60}h" if mins >= 60 else f"{mins}m"

                        if row["tool_name"] == "control_device":
                            key = inp.get("device", "").lower()
                            action = inp.get("action", "unknown")
                            devices[key] = action
                            if action == "on":
                                uptimes[key] = uptime_str
                        elif row["tool_name"] == "send_google_assistant_command":
                            cmd = inp.get("command", "").lower()
                            for kw in ["fan", "ac", "moon", "dashboard", "panels", "projector", "geyser",
                                       "light 1", "light 2", "cove", "spots", "foot lamp", "top light"]:
                                if kw in cmd:
                                    state = "on" if "turn on" in cmd or "set" in cmd else "off"
                                    devices[kw] = state
                                    if state == "on":
                                        uptimes[kw] = uptime_str
                    except Exception:
                        pass
                ctx["devices"] = devices
                ctx["uptimes"] = uptimes

            if engine:
                presence = await engine._ping_phone()
                ctx["presence"] = presence.get("home", True)
            await ui.emit({"type": "context", **ctx})
        except Exception as e:
            print(f"[Context] Error: {e}")
        await asyncio.sleep(10)  # presence refreshes every 30s


async def _tuya_watchdog_loop(ha_client) -> None:
    """Reload the Tuya HA integration automatically if devices go unavailable."""
    import time
    await asyncio.sleep(90)  # let HA fully settle on boot
    last_reload_at = 0.0
    SENTINEL = "switch.geyser_socket_1"
    CHECK_INTERVAL = 300   # check every 5 minutes
    RELOAD_COOLDOWN = 600  # don't reload more than once per 10 minutes
    while True:
        try:
            state = await ha_client.get_state(SENTINEL)
            if state.get("state") == "unavailable":
                now = time.monotonic()
                if now - last_reload_at > RELOAD_COOLDOWN:
                    print("[Tuya] Devices unavailable — finding config entry...")
                    entries = await ha_client.get_config_entries("tuya")
                    if entries:
                        eid = entries[0]["entry_id"]
                        ok = await ha_client.reload_config_entry(eid)
                        last_reload_at = now
                        print(f"[Tuya] Reloaded entry {eid}: {'ok' if ok else 'failed'}")
                    else:
                        print("[Tuya] No tuya config entry found in HA")
        except Exception as e:
            print(f"[Tuya] Watchdog error: {e}")
        await asyncio.sleep(CHECK_INTERVAL)


async def run(config: Config) -> None:
    # Set speaker volume at startup — prevents it resetting low after service restarts
    try:
        subprocess.run(
            ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "100%"],
            timeout=3, check=False,
        )
        subprocess.run(
            ["amixer", "-c", "2", "sset", "PCM", "100%"],
            timeout=3, check=False,
        )
        print("[Sunday] Volume set to 100%.")
    except Exception as e:
        print(f"[Sunday] Volume set failed: {e}")

    mic = MicStream(config.mic_device)
    mic.start(asyncio.get_event_loop())

    wake = WakeWordDetector(config.wakeword_model, config.wakeword_threshold)
    wake.feedback = Capture()
    from wake_profile import WakeProfile
    wake_profile=WakeProfile(config.ha_url,config.ha_token,wake)
    try:
        await wake_profile.initialize()
    except Exception as error:
        print('[Wake profile] Startup selector unavailable:',type(error).__name__,flush=True)
    wake.profile=wake_profile

    stt = STT()
    from eleven_realtime_stt import enabled as live_stt_enabled, start_warm
    if live_stt_enabled() and stt._provider() == "elevenlabs":
        start_warm(stt)
    tts = TTS()
    from listening_mode import ListeningMode
    listening_mode=ListeningMode(config.ha_url,config.ha_token)
    await listening_mode.initialize()
    wake.listening_mode=listening_mode
    stt.listening_mode=listening_mode

    # Wiki-based memory: per-turn retrieval + LLM-routed saves
    import wiki_io
    if config.openai_api_key:
        wiki_io.init(config.openai_api_key)
        print("[Sunday] Wiki memory initialised.")
    else:
        print("[Sunday] WARN: no openai_api_key — wiki memory disabled.")


    # Pre-warm cloud connections — eliminates TCP/TLS overhead on first real request
    async def _warmup():
        import asyncio
        try:
            await asyncio.gather(
                stt.warmup(),
                tts.warmup(),
            )
            print('[Sunday] Cloud connections warmed up.')
        except Exception as e:
            print(f'[Sunday] Warmup warning: {e}')

    await _warmup()
    agent = Agent(
        config.google_api_key,
        provider=config.llm_provider,
        openai_api_key=config.openai_api_key,
        openai_voice_model=config.openai_voice_model,
        openai_chat_model=config.openai_chat_model,
    )

    async def _handle_ui_command(text: str) -> None:
        print(f"[UI Command] {text!r}")
        await ui.emit({"type": "transcript", "text": text})
        await ui.emit({"type": "thinking"})
        if await _fast_path(text, tts):
            return
        reply = await agent.respond_as_text(text)
        if reply:
            await ui.emit({"type": "assistant_message", "text": reply})

    ui.set_command_handler(_handle_ui_command)

    async def _handle_announce(message: str, channel: str) -> None:
        """Deliver an announcement from HA automations via voice/telegram/both."""
        print(f"[Announce] channel={channel}: {message!r}")
        if channel in ("voice", "both"):
            try:
                await tts.speak(message)
            except Exception as e:
                print(f"[Announce] tts error: {e}")
        if channel in ("telegram", "both"):
            try:
                await engine._send_telegram(message)
            except Exception as e:
                print(f"[Announce] telegram error: {e}")

    ui.set_announce_handler(_handle_announce)

    async def _handle_ask(text: str) -> None:
        """Full LLM + TTS pipeline triggered from HA via POST /ask."""
        print(f"[Ask] {text!r}")
        await ui.emit({"type": "transcript", "text": text})
        await ui.emit({"type": "thinking"})
        if not await _fast_path(text, tts):
            await agent.process(text, tts)
        await ui.emit({"type": "idle"})

    ui.set_ask_handler(_handle_ask)

    hogar = HogarClient(ip=config.hogar_ip)
    await hogar.start()
    tools.set_hogar_client(hogar)

    from firetv import FireTVClient
    firetv = FireTVClient(config.firetv_ip or None)
    if await firetv.connect():
        print(f"[Sunday] Fire TV connected at {firetv._ip}.")
    else:
        print("[Sunday] Fire TV unavailable — will discover on demand.")
    tools.set_firetv_client(firetv)
    tools.set_tavily_api_key(config.tavily_api_key)

    if config.smartthings_soundbar_id:
        from smartthings import SmartThingsClient
        st = SmartThingsClient(config.smartthings_soundbar_id)
        tools.set_smartthings_client(st)
        print("[Sunday] SmartThings soundbar ready.")

    kasa = None
    if config.tapo_username and config.tapo_devices:
        from kasa_client import KasaClient
        kasa = KasaClient(config.tapo_username, config.tapo_password, config.tapo_devices)
        await kasa.start()
        tools.set_kasa_client(kasa)

    if config.tuya_api_key and config.tuya_geyser_id:
        from tuya import GeyserClient
        geyser = GeyserClient(config.tuya_api_key, config.tuya_api_secret, config.tuya_geyser_id, config.tuya_region)
        tools.set_geyser_client(geyser)
        print("[Sunday] Geyser (Tuya) connected.")

    ha_client = None
    if config.ha_token:
        ha_client = HAClient(config.ha_url, config.ha_token)
        tools.set_ha_client(ha_client)
        print("[Sunday] Home Assistant connected.")
    else:
        print("[Sunday] Home Assistant not configured — add ha_token to config.json")

    if config.outlook_ics_url or config.gcal_ics_url:
        tools.set_calendar_urls(config.outlook_ics_url, config.gcal_ics_url)

    # Google Calendar write access (OAuth2 token from setup_gcal_auth.py)
    import os as _os
    _gcal_token = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "calendar_token.json")
    if _os.path.exists(_gcal_token):
        tools.set_gcal_token_path(_gcal_token)
    else:
        print("[Calendar] calendar_token.json not found — create_calendar_event unavailable. Run setup_gcal_auth.py.")

    from wake_chime import CachedWakeChime
    _cached_wake = None
    _chime_tasks = set()
    try:
        from pathlib import Path as ChimePath
        if ChimePath('/home/djpi/sunday-audio/state/backend').read_text().strip() == 'stackchan':
            _cached_wake = CachedWakeChime(config.awake_sound)
    except Exception as error:
        print('[Wake chime] cache unavailable: ' + type(error).__name__, flush=True)

    def chime(sound: str, trace=None) -> None:
        if not sound:
            return
        import json as audit_json
        def audit(stage, **extra):
            if trace:
                now = time.monotonic_ns()
                print('[Wake audit] ' + audit_json.dumps({**trace, 'stage': stage, 'at_ns': now,
                    'since_accepted_ms': round((now-trace['accepted_ns'])/1e6,3), **extra}), flush=True)
        player = ["afplay", sound] if sys.platform == "darwin" else ["aplay", "-D", "plughw:2,0", "-q", sound]
        audit('chime_scheduled')
        if trace and _cached_wake is not None:
            task = asyncio.create_task(_cached_wake.play(trace))
            _chime_tasks.add(task)
            task.add_done_callback(_chime_tasks.discard)
            return
        def play():
            import os
            env = os.environ.copy()
            if trace:env['SUNDAY_WAKE_TRACE'] = audit_json.dumps(trace)
            audit('player_spawn')
            result = subprocess.run(player, env=env)
            audit('player_return', returncode=result.returncode)
        asyncio.get_event_loop().run_in_executor(None, play)

    engine = ReflectionEngine(
        config, tts,
        ha_client=ha_client,
        stt=stt,
        agent=agent,
        chime=chime,
    )
    tools.set_reflector(engine)

    from voice_brain import VoiceBrain
    voice_agent = VoiceBrain()
    tasks = [
        listening_mode.watch(),
        wake_profile.watch(),
        _voice_loop(mic, wake, stt, tts, voice_agent, config, engine, chime),
        # engine.heartbeat_loop(),  # disabled
        engine.alert_loop(),
        engine.scheduler_loop(),
        engine.presence_loop(),
        engine.suggestion_loop(),
        *([engine.telegram_loop()] if os.environ.get("SUNDAY_TELEGRAM_ENABLED", "1") != "0" else []),
        engine.event_loop(),
        engine.works_watcher(),
        _presence_watcher(config),
        ui.run(),
        _context_loop(engine),
        engine.condition_watcher_loop(),
        ClapDetector(mic, config.mqtt_broker, config.mqtt_port, config.ha_url, config.ha_token).run(),
    ]
    if ha_client:
        tasks.append(_tuya_watchdog_loop(ha_client))
    if kasa:
        tasks.append(kasa.retry_loop())

    await asyncio.gather(*tasks)


async def test_tts(config: Config, text: str) -> None:
    """Skip wake+STT, feed text directly to agent — for silent debugging."""
    tts = TTS(config.piper_model, config.piper_length_scale, elevenlabs_api_key=config.elevenlabs_api_key)
    agent = Agent(
        config.google_api_key,
        provider=config.llm_provider,
        openai_api_key=config.openai_api_key,
        openai_voice_model=config.openai_voice_model,
        openai_chat_model=config.openai_chat_model,
    )
    if config.ha_token:
        tools.set_ha_client(HAClient(config.ha_url, config.ha_token))
    print(f"[Test] Sending: {text!r}")
    await agent.process(text, tts)
    print("[Test] Done.")


async def _presence_watcher(config: Config) -> None:
    """Watch sunday/presence MQTT — turn off fan+lights when room empties."""
    import paho.mqtt.client as mqtt

    loop = asyncio.get_event_loop()

    async def _turn_off_room() -> None:
        await tools.execute("control_device", {"device": "fan", "action": "off"}, "")
        await tools.execute("control_device", {"device": "all lights", "action": "off"}, "")
        print("[Sunday] Room empty — fan and all lights off.")

    def on_message(client, userdata, msg):
        payload = msg.payload.decode().strip().lower()
        if payload == "false":
            asyncio.run_coroutine_threadsafe(_turn_off_room(), loop)

    while True:
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            client.on_message = on_message
            client.connect(config.mqtt_broker, config.mqtt_port, keepalive=60)
            client.subscribe("sunday/presence")
            client.loop_start()
            print("[Sunday] MQTT presence watcher connected — watching sunday/presence")
            while True:
                await asyncio.sleep(30)
        except Exception as e:
            print(f"[Sunday] MQTT presence watcher error: {e} — retrying in 15s")
            try:
                client.loop_stop()
            except Exception:
                pass
            await asyncio.sleep(15)


def main() -> None:
    cfg_path = "config.json"
    if "--config" in sys.argv:
        cfg_path = sys.argv[sys.argv.index("--config") + 1]
    config = Config.load(cfg_path)
    memory.init()

    if config.llm_provider == "openai" and not config.openai_api_key:
        print("ERROR: llm_provider=openai but openai_api_key is not set in config.json")
        sys.exit(1)
    if config.llm_provider == "gemini" and not config.google_api_key:
        print("ERROR: Set google_api_key in config.json")
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        text = " ".join(sys.argv[2:]) or "Hello, say something."
        try:
            asyncio.run(test_tts(config, text))
        except KeyboardInterrupt:
            pass
        return

    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        print("\n[Sunday] Bye.")


if __name__ == "__main__":
    main()
