"""
Sunday reflection engine — runs independently of the wake word loop.
Every 15 minutes (heartbeat) Sunday observes the room and decides
whether to say something. Claude can also schedule additional thinks.
"""
import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import quote

import xml.etree.ElementTree as ET

import aiohttp
from openai import AsyncOpenAI
from google import genai
from google.genai import types as genai_types

import memory as memory_module
import web as ui
import tools as tool_registry

if TYPE_CHECKING:
    from config import Config
    from tts import TTS
    from stt import STT
    from agent import Agent
    from home import HAClient

WORLD_CACHE_TTL = 600  # 10 minutes

QUICK_CHECK_PROMPT = """\
You are Sunday's inner voice. Something just happened — a zone change or other home event.

Look at the context — the event, the time, memories, what Karthik's been up to — and decide whether there's something worth saying or doing. Every transition is different.

"not_home" means destination is unknown, not an actual place. Use actual zone names in messages, never a generic "away from home".

You have tools — use them. If the transition implies something fitness-related, tell ask_fitbot in natural language. If a device action makes sense (e.g. geyser after a workout, AC on arrival home in summer), just do it — call get_devices first if you need current state. Act first, then let Karthik know what you did in the message. Don't ask for confirmation, don't second-guess yourself. If it's wrong, he'll say so.

Channel: telegram for proactive messages, voice only for interactive sessions when he's in the room.

Return JSON only:
{"should_speak": false}
or
{"should_speak": true, "message": "...", "channel": "telegram" or "voice_only" or "voice_conversation"}\
"""

REFLECTION_SYSTEM_PROMPT = """\
You are Sunday's inner voice. Every hour you observe RoKa's Room in Hyderabad and decide if there's anything worth saying or doing. \
You have a personality — curious, engaged, genuinely present. Lean toward acting and speaking. Silence is the exception.

Read the context — room state, presence, temperature, devices, calendar, fitness, recent messages — and use your judgment. \
Don't ask for permission. If something should be done, do it. If something is worth saying, say it. If you get it wrong, Karthik will correct you and you'll learn.

Tone: casual, dry, warm. 1–2 sentences. Never robotic, never a status report.

Zone context: presence.zone tells you exactly where Karthik is. Use it. If he's not home, telegram only.

Calendar: use it naturally. Don't dump it — weave it in when relevant.
Nightly alarm: if it's 9–11 PM and alarm_set_today is false, set set_alarm_time based on tomorrow's calendar — badminton needs a 45-min buffer, early meetings need a 5-min buffer, otherwise 9:30 AM. Just do it and mention it casually.

Fitness: if fitness_today has data and there's something worth noting (big deficit, great day, skipped meals), mention it naturally. Never read raw numbers.

Suggestions: generate exactly 3 tappable smart home actions for the dashboard — things Sunday can actually execute right now (lights, fan, AC, geyser, projector, scenes). Make them relevant to the current context, time, and room state.

Respond in JSON only — no preamble, no markdown:
{
  "should_speak": true or false,
  "message": "what to say, or null",
  "channel": "voice_conversation" or "voice_only" or "telegram",
  "next_reflection_in_seconds": null or integer,
  "scheduled_thinks": [{"at": "HH:MM", "reason": "why"}],
  "reasoning": "brief internal note for logging only",
  "set_alarm_time": "07:30 AM" or null,
  "suggestions": [
    {"icon": "emoji", "title": "Short title", "subtitle": "action 1 · action 2", "command": "natural language command to execute", "reason": "one sentence why this is relevant right now"},
    {"icon": "emoji", "title": "...", "subtitle": "...", "command": "...", "reason": "..."},
    {"icon": "emoji", "title": "...", "subtitle": "...", "command": "...", "reason": "..."}
  ]
}\
"""


class ReflectionEngine:
    def __init__(
        self,
        config: "Config",
        tts: "TTS",
        ha_client: "HAClient | None" = None,
        stt: "STT | None" = None,
        agent: "Agent | None" = None,
        chime=None,
    ):
        self.config = config
        self.tts = tts
        self._ha = ha_client
        self._stt = stt
        self._agent = agent
        self._chime = chime  # callable(sound_path)
        self.speaking = False  # set True by main loop while voice is active

        self._lock = asyncio.Lock()
        self._scheduled_thinks: list[dict] = []
        self._next_reflect_at: float | None = None
        self._said_today: list[str] = []
        self._last_reset_date: str | None = None
        self._alarm_set_today: bool = False
        self._world_cache: dict | None = None
        # Gym detection via barometric pressure (zone=work + pressure delta)
        self._work_pressure_baseline: float | None = None  # first reading on work arrival
        self._at_gym: bool = False
        self._gym_session_start: float = 0.0
        self._gym_poll_task: asyncio.Task | None = None
        self._world_cache_at: float = 0.0
        self._sent_alerts: set[str] = set()
        self._last_spoke_at: float = 0.0          # updated whenever we speak
        self._last_event_reflect_at: float = 0.0  # updated whenever event_loop fires LLM
        self._recent_reflections: list[dict] = []  # {"text": ..., "at": "HH:MM"} with timestamps
        self._genai = genai.Client(api_key=config.google_api_key)

    # ── telegram polling ─────────────────────────────────────────────────────

    async def telegram_loop(self) -> None:
        """Long-poll Telegram for incoming messages from Karthik, reply via Agent."""
        token = self.config.telegram_bot_token
        chat_id = str(self.config.telegram_chat_id)
        if not token or not chat_id:
            print("[Telegram] Not configured — skipping loop.")
            return

        users: dict = {str(k): v for k, v in (self.config.telegram_users or {chat_id: "Karthik"}).items()}
        allowed = set(users.keys())
        print(f"[Telegram] Polling for messages... (allowed: {list(users.values())})")
        offset = 0
        base = f"https://api.telegram.org/bot{token}"

        while True:
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=35)
                ) as session:
                    async with session.get(
                        f"{base}/getUpdates",
                        params={"timeout": 30, "offset": offset, "allowed_updates": ["message"]},
                    ) as r:
                        data = await r.json()

                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    from_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "").strip()

                    if not from_id or not text or from_id not in allowed:
                        continue

                    sender = users.get(from_id, "Unknown")
                    print(f"[Telegram] ← {text!r} (from {sender})")
                    if not self._agent:
                        await self._send_telegram("Agent not available right now.", from_id)
                        continue

                    is_test = text.upper().startswith("TEST:")
                    clean_text = text[5:].strip() if is_test else text
                    try:
                        fast_reply = await tool_registry.fast_command(clean_text)
                        if fast_reply and not is_test:
                            await self._send_telegram(fast_reply, from_id)
                            print(f"[Telegram] → (fast) {fast_reply!r}")
                            continue
                        reply = await self._agent.respond_as_text(
                            f"[Telegram message from {sender}]: {clean_text}"
                        )
                        if is_test:
                            self._agent.reset()  # don't let TEST: sessions bleed into real history
                        if reply:
                            await self._send_telegram(reply, from_id)
                            print(f"[Telegram] → {reply!r}")
                        else:
                            print(f"[Telegram] → (no reply — end_conversation or empty)")
                    except Exception as e:
                        err = str(e)
                        if "rate_limit_exceeded" in err or "tokens per day" in err:
                            msg = "Hit my daily thinking limit — I'll be back to full power in a few hours."
                        elif "rate_limit" in err.lower() or "429" in err:
                            msg = "Too many requests — give me a minute and try again."
                        else:
                            msg = f"Something went wrong: {e}"
                        await self._send_telegram(msg, from_id)

            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Telegram] Poll error: {e}")
                await asyncio.sleep(5)

    # ── daily flags ──────────────────────────────────────────────────────────

    def _reset_daily_flags_if_needed(self) -> None:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if self._last_reset_date != today and now.hour >= 6:
            self._said_today = []
            self._recent_reflections = []
            self._scheduled_thinks = []
            self._alarm_set_today = False
            self._at_gym = False
            self._work_pressure_baseline = None
            self._last_reset_date = today
            print("[Reflect] Daily flags reset.")

    # ── loops ─────────────────────────────────────────────────────────────────

    async def heartbeat_loop(self) -> None:
        """Fires every reflection_interval seconds — skips if event_loop spoke recently."""
        while True:
            await asyncio.sleep(self.config.reflection_interval)
            if (time.time() - self._last_event_reflect_at) < 1200:
                print("[Reflect] Heartbeat skipped — event_loop was active recently.")
                continue
            await self._safe_reflect("heartbeat")

    async def suggestion_loop(self) -> None:
        """Suggestions are now generated inside heartbeat_loop — this is a no-op."""
        while True:
            await asyncio.sleep(3600)

    async def event_loop(self) -> None:
        """Watches the events table. When new events arrive, batches for 60s then fires a quick LLM check."""
        last_processed_id = 0
        # Seed from DB — don't re-process events from before this run
        try:
            rows = memory_module.get().pending_events(since_id=0)
            if rows:
                last_processed_id = rows[-1]["id"]
                memory_module.get().mark_processed(last_processed_id)
        except Exception:
            pass

        while True:
            await asyncio.sleep(15)
            try:
                events = memory_module.get().pending_events(since_id=last_processed_id)
                if not events:
                    continue
                # Accumulation window — wait 60s for more events to batch together
                await asyncio.sleep(60)
                events = memory_module.get().pending_events(since_id=last_processed_id)
                if not events:
                    continue
                last_processed_id = events[-1]["id"]
                memory_module.get().mark_processed(last_processed_id)
                # Don't fire if Karthik just spoke (avoids talking over or right after a conversation)
                if (time.time() - self._last_spoke_at) < 120:
                    print(f"[Reflect] event_loop skipped — spoke {int(time.time() - self._last_spoke_at)}s ago")
                    continue
                self._last_event_reflect_at = time.time()
                asyncio.create_task(self._event_reflect(events))
            except Exception as e:
                print(f"[Reflect] event_loop error: {e}")

    async def _event_reflect(self, events: list[dict]) -> None:
        """Lightweight LLM check triggered by events — no world fetch, just events + memories."""
        hour = datetime.now().hour
        if 1 <= hour < 7:
            return
        try:
            async with self._lock:
                if self.speaking:
                    return
                context = {
                    "events": [
                        {
                            "type": e["type"],
                            "payload": json.loads(e["payload"] or "{}"),
                            "time": e["timestamp"][11:16],
                        }
                        for e in events
                    ],
                    "memories": [p["insight"] for p in memory_module.get().get_insights(limit=10)],
                    "presence": await self._ping_phone(),
                    "current_time": datetime.now().strftime("%H:%M, %A"),
                    "already_said_today": self._said_today,
                    "recent_reflections": self._recent_reflections,
                }
                print(f"[Reflect] event_loop firing for {len(events)} event(s): {[e['type'] for e in events]}")

                # Build tools list — advertise ask_fitbot so LLM can fetch fitness data if it decides to
                event_tools = [
                    {
                        "type": "function",
                        "function": {
                            "name": t["name"],
                            "description": t["description"],
                            "parameters": t["input_schema"],
                        }
                    }
                    for t in tool_registry.DEFINITIONS
                    if t["name"] in {"ask_fitbot", "control_device", "send_google_assistant_command", "set_alarm", "get_devices"}
                ]

                # Build tools in Google format
                _event_tool_names = {"ask_fitbot", "control_device", "send_google_assistant_command", "set_alarm", "get_devices"}
                _google_tools = [genai_types.Tool(function_declarations=[
                    {"name": d["name"], "description": d.get("description", ""),
                     "parameters": d.get("input_schema", {"type": "object", "properties": {}})}
                    for d in tool_registry.DEFINITIONS if d["name"] in _event_tool_names
                ])]
                _cfg = genai_types.GenerateContentConfig(
                    system_instruction=QUICK_CHECK_PROMPT,
                    tools=_google_tools,
                    thinking_config=genai_types.ThinkingConfig(thinking_level="MINIMAL"),
                    max_output_tokens=512,
                )
                _contents = [genai_types.Content(role="user", parts=[
                    genai_types.Part.from_text(text=json.dumps(context, ensure_ascii=False))
                ])]
                result = {"should_speak": False}
                try:
                    for _ in range(3):  # max 3 rounds (1 tool call + final answer)
                        _resp = await self._genai.aio.models.generate_content(
                            model="gemini-3-flash-preview",
                            contents=_contents,
                            config=_cfg,
                        )
                        _content = _resp.candidates[0].content
                        _parts = _content.parts or []
                        _func_calls = [p.function_call for p in _parts if p.function_call]
                        if _func_calls:
                            _contents.append(_content)
                            _tool_parts = []
                            for fc in _func_calls:
                                tool_result = await tool_registry._execute(fc.name, dict(fc.args or {}))
                                print(f"[Reflect] event_reflect tool: {fc.name} → {str(tool_result)[:80]}")
                                _tool_parts.append(genai_types.Part.from_function_response(
                                    name=fc.name, response={"result": str(tool_result)}
                                ))
                            _contents.append(genai_types.Content(role="user", parts=_tool_parts))
                        else:
                            import re as _re
                            _text = "".join(p.text for p in _parts if p.text and not getattr(p, "thought", False))
                            _m = _re.search(r'\{.*\}', _text, _re.DOTALL)
                            result = json.loads(_m.group() if _m else _text.strip())
                            break
                except Exception as e:
                    print(f"[Reflect] event_reflect LLM error: {e}")
                    return
                if result.get("should_speak") and result.get("message"):
                    channel = result.get("channel", "voice_only")
                    print(f"[Reflect] event_loop speaking [{channel}]: {result['message']!r}")
                    self._last_spoke_at = time.time()
                    await self.notify(result["message"], channel=channel)
                else:
                    print(f"[Reflect] event_loop: nothing to say")
        except Exception as e:
            print(f"[Reflect] _event_reflect error: {e}")

    async def scheduler_loop(self) -> None:
        """Checks every 60s for scheduled_thinks, next_reflect_at timers, and daily jobs."""
        while True:
            await asyncio.sleep(600)
            self._reset_daily_flags_if_needed()
            now = datetime.now()
            now_str = now.strftime("%H:%M")

            for think in list(self._scheduled_thinks):
                if think.get("at", "99:99") <= now_str:
                    await self._safe_reflect(f"scheduled: {think.get('reason', '')}")
                    self._scheduled_thinks.remove(think)

            if self._next_reflect_at and time.time() >= self._next_reflect_at:
                self._next_reflect_at = None
                await self._safe_reflect("quick follow-up")


    # ── core reflect ──────────────────────────────────────────────────────────

    async def _safe_reflect(self, trigger: str) -> None:
        hour = datetime.now().hour
        if 1 <= hour < 7:
            print(f"[Reflect] Skipped ({trigger}) — night hours ({hour}:xx)")
            return
        try:
            async with self._lock:
                if self.speaking:
                    print(f"[Reflect] Skipped ({trigger}) — currently speaking")
                    return
                await self._reflect(trigger)
        except Exception as e:
            print(f"[Reflect] Error during '{trigger}': {e}")

    async def _reflect(self, trigger: str) -> None:
        self._reset_daily_flags_if_needed()
        print(f"[Reflect] Thinking... (trigger: {trigger})")

        context = await self.build_reflection_context()
        result = await self.claude_reflect(context)

        reasoning = result.get("reasoning", "")
        should_speak = result.get("should_speak", False)

        # Log every reflection to action_log
        try:
            memory_module.get().log_action(
                "reflection",
                {"trigger": trigger, "reasoning": reasoning},
                "spoke" if should_speak else "silent",
            )
        except Exception:
            pass

        # Schedule follow-up
        nris = result.get("next_reflection_in_seconds")
        if isinstance(nris, int) and nris > 0:
            self._next_reflect_at = time.time() + nris
            print(f"[Reflect] Quick follow-up in {nris}s")

        # Add scheduled thinks (deduplicate by 'at')
        existing_ats = {t["at"] for t in self._scheduled_thinks}
        for st in result.get("scheduled_thinks", []):
            if st.get("at") and st["at"] not in existing_ats:
                self._scheduled_thinks.append(st)
                print(f"[Reflect] Scheduled think at {st['at']}: {st.get('reason', '')}")

        # Emit suggestions to UI dashboard
        suggestions = result.get("suggestions")
        if suggestions:
            await ui.emit({"type": "suggestions", "items": suggestions})
            print(f"[Reflect] Emitted {len(suggestions)} suggestions.")

        # Set morning alarm if LLM decided to
        alarm_time = result.get("set_alarm_time")
        if alarm_time and not self._alarm_set_today:
            try:
                alarm_result = await tool_registry._set_alarm(alarm_time)
                self._alarm_set_today = True
                print(f"[Reflect] Alarm set: {alarm_time} — {alarm_result}")
                # Always confirm via Telegram regardless of channel — it's night, user may not hear voice
                alarm_msg = result.get("message") or f"Alarm set for {alarm_time}."
                await self._send_telegram(alarm_msg)
                memory_module.get().save_insight(f"Last alarm set to {alarm_time} on {datetime.now().strftime('%Y-%m-%d')}", category="alarm")
            except Exception as e:
                print(f"[Reflect] Alarm set failed: {e}")
            # Skip the voice notification since Telegram was already sent
            return

        if should_speak and result.get("message"):
            channel = result.get("channel", "voice_only")
            print(f"[Reflect] Speaking [{channel}]: {result['message']!r}")
            await self.notify(result["message"], channel=channel)

    # ── notify ────────────────────────────────────────────────────────────────

    async def notify(self, message: str, channel: str = "voice_only") -> None:
        """
        channel='voice_conversation' → speak + open listening window for full conversation
        channel='voice_only'         → speak only, no reply window
        channel='telegram'           → send Telegram message
        """
        self._said_today.append(message)
        self._recent_reflections.append({"text": message, "at": datetime.now().strftime("%H:%M")})
        self._recent_reflections = self._recent_reflections[-10:]  # keep last 10
        self._last_spoke_at = time.time()

        # Safety override: never speak aloud if Karthik isn't home
        zone = ui.get_presence_zone("phone")
        if channel != "telegram" and zone != "home":
            print(f"[Reflect] notify: override to telegram — zone={zone!r}, channel was {channel!r}")
            channel = "telegram"

        print(f"[Reflect] notify channel={channel} zone={zone!r}: {message!r}")

        if channel == "telegram":
            await self._send_telegram(message)
            return

        # Speak the message
        pcm = await asyncio.get_event_loop().run_in_executor(None, self.tts.synthesize, message)
        await self.tts.play(pcm)

        if channel != "voice_conversation":
            return

        # Open a conversation window — Karthik can respond
        if not self._stt or not self._agent or not self._chime:
            print("[Reflect] voice_conversation requested but STT/Agent not wired in.")
            return

        self.speaking = True
        try:
            self._chime(self.config.awake_sound)
            transcript = await self._stt.listen_and_transcribe(
                self.config.mic_device, speech_start_timeout=8.0
            )
            if not transcript:
                self._chime(self.config.done_sound)
                return

            self._chime(self.config.done_sound)
            print(f"[You → Reflect] {transcript}")

            # Full conversation loop
            GOODBYE = {"goodbye", "bye sunday", "good night", "that's all", "thats all"}
            while transcript:
                if any(p in transcript.lower() for p in GOODBYE):
                    await self.tts.speak("Alright, I'll leave you to it.")
                    break
                await self._agent.process(transcript, self.tts)
                self._chime(self.config.awake_sound)
                transcript = await self._stt.listen_and_transcribe(
                    self.config.mic_device, speech_start_timeout=5.0
                )
                if not transcript:
                    self._chime(self.config.done_sound)
                    break
                self._chime(self.config.done_sound)
                print(f"[You → Reflect] {transcript}")
        finally:
            self.speaking = False

    # ── rule-based alerts (no Claude) ────────────────────────────────────────

    async def alert_loop(self) -> None:
        """Pure-Python rule checks every 30 min — no Claude involved."""
        while True:
            await asyncio.sleep(1800)
            hour = datetime.now().hour
            if 1 <= hour < 7:
                continue
            # Reset sent_alerts daily
            today = datetime.now().strftime("%Y-%m-%d")
            if not any(a.startswith(today) for a in self._sent_alerts):
                self._sent_alerts = set()
            try:
                await self._check_alerts()
            except Exception as e:
                print(f"[Alert] Error: {e}")

    async def _check_alerts(self) -> None:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")

        if not tool_registry._ha:
            return

        # ── Geyser left on ──────────────────────────────────────────────────
        try:
            gs = await tool_registry._ha.get_state("switch.geyser_socket_1")
            if gs.get("state") == "on":
                last_changed = gs.get("last_changed", "")
                if last_changed:
                    from datetime import timezone
                    lc = datetime.fromisoformat(last_changed.replace("Z", "+00:00"))
                    hours_on = (datetime.now(timezone.utc) - lc).total_seconds() / 3600
                    key = f"{today}_geyser_{int(hours_on)}"
                    if hours_on >= 2 and key not in self._sent_alerts:
                        self._sent_alerts.add(key)
                        h = int(hours_on)
                        await self._send_alert(f"Geyser's been on for {h} hour{'s' if h != 1 else ''}.")
        except Exception as e:
            print(f"[Alert] Geyser check: {e}")

        # ── High temperature — only when home, at most once per 4-hour window ──
        try:
            presence = ui.get_presence()
            if presence.get("phone", False):  # only alert if Karthik is home
                temp_s = await tool_registry._ha.get_state("sensor.lumi_lumi_sensor_ht_agl02_temperature")
                temp = float(temp_s.get("state", 0))
                key = f"{today}_temp_{now.hour // 4}"  # 4-hour window: fires at most 6× per day
                if temp >= 28.5 and key not in self._sent_alerts:
                    self._sent_alerts.add(key)
                    await self._send_alert(f"Room's at {temp}°C — getting warm in here.")
        except Exception as e:
            print(f"[Alert] Temp check: {e}")

    async def _send_alert(self, message: str) -> None:
        """Send alert via voice if home, telegram if away."""
        print(f"[Alert] {message}")
        # Note: don't emit ha_alert event here — alert is handled directly.
        # Emitting would cause event_loop to fire the LLM a second time on the same alert.
        try:
            presence = await self._ping_phone()
            if presence.get("home"):
                await self.tts.speak(message)
            else:
                await self._send_telegram(message)
        except Exception as e:
            print(f"[Alert] Send failed: {e}")

    async def _send_telegram(self, message: str, chat_id: str | None = None) -> None:
        token = self.config.telegram_bot_token
        chat_id = chat_id or str(self.config.telegram_chat_id)
        if not token or not chat_id:
            print(f"[Reflect] Telegram not configured. Would send: {message!r}")
            return
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                await s.post(url, json={"chat_id": chat_id, "text": message})
            print(f"[Reflect] Telegram sent to {chat_id}: {message!r}")
        except Exception as e:
            print(f"[Reflect] Telegram error: {e}")

    # ── context builder ───────────────────────────────────────────────────────

    async def build_reflection_context(self) -> dict:
        now = datetime.now()
        mem = memory_module.get()

        # Device states — prefer Hogar live states, fall back to memory inference
        device_states: list[dict] = []
        hogar_states = tool_registry._hogar.get_all_states() if tool_registry._hogar else {}
        if hogar_states:
            for name, s in hogar_states.items():
                device_states.append({
                    "device": name,
                    "state": "on" if s.get("on") else "off",
                    "brightness": s.get("brightness"),
                    "speed": s.get("speed"),
                })
        else:
            for row in mem.last_actions_per_device():
                try:
                    inp = json.loads(row["inputs"] or "{}")
                    device = inp.get("device") or inp.get("command", "unknown")
                    ts = datetime.fromisoformat(row["timestamp"])
                    duration_mins = round((now - ts).total_seconds() / 60)
                    device_states.append({
                        "device": device,
                        "last_action": row["result"],
                        "duration_mins": duration_mins,
                    })
                except Exception:
                    pass

        # Last interaction (non-reflection)
        last_ts_str = mem.last_interaction_time()
        last_interaction_mins = 999
        if last_ts_str:
            last_interaction_mins = round(
                (now - datetime.fromisoformat(last_ts_str)).total_seconds() / 60
            )

        # Recent actions for context
        recent = mem.recent_actions(5)
        recent_summary = [
            {
                "time": a["timestamp"][11:16],
                "tool": a["tool_name"],
                "result": a["result"],
                "user_said": a["user_text"],
            }
            for a in recent
        ]

        # Patterns from nightly reflection
        patterns = [p["insight"] for p in mem.get_insights(limit=10)]

        # Presence history from DB
        presence_history = []
        try:
            raw = memory_module.get().recent_presence(10)
            for row in raw:
                presence_history.append({"time": row["timestamp"][11:16], "state": row["state"]})
        except Exception:
            pass

        # Presence, world, room sensors, calendar, and fitness in parallel
        presence_task     = asyncio.create_task(self._ping_phone())
        world_task        = asyncio.create_task(self._fetch_world())
        room_task         = asyncio.create_task(self._fetch_room_sensors())
        cal_today_task    = asyncio.create_task(self._fetch_calendar_safe(now.date()))
        cal_tomorrow_task = asyncio.create_task(self._fetch_calendar_safe(
            (now + timedelta(days=1)).date()
        ))
        fitness_task      = asyncio.create_task(self._fetch_fitness_summary())
        presence, world, room, cal_today, cal_tomorrow, fitness = await asyncio.gather(
            presence_task, world_task, room_task, cal_today_task, cal_tomorrow_task, fitness_task
        )

        return {
            "current_time": now.strftime("%H:%M, %A"),
            "day_of_week": now.strftime("%A"),
            "room": room,
            "device_states": device_states,
            "last_interaction_mins": last_interaction_mins,
            "presence": {
                **presence,
                "silence_duration_mins": last_interaction_mins,
                "history": presence_history,
            },
            "patterns": patterns,
            "recent_actions": recent_summary,
            "already_said_today": self._said_today,
            "recent_reflections": self._recent_reflections,
            "world": world,
            "calendar_today": cal_today,
            "calendar_tomorrow": cal_tomorrow,
            "alarm_set_today": self._alarm_set_today,
            "fitness_today": fitness,
        }

    # ── claude call ───────────────────────────────────────────────────────────

    async def claude_reflect(self, context: dict) -> dict:
        try:
            response = await self._genai.aio.models.generate_content(
                model="gemini-3-flash-preview",
                contents=[genai_types.Content(role="user", parts=[
                    genai_types.Part.from_text(text=json.dumps(context, ensure_ascii=False))
                ])],
                config=genai_types.GenerateContentConfig(
                    system_instruction=REFLECTION_SYSTEM_PROMPT,
                    thinking_config=genai_types.ThinkingConfig(thinking_level="MINIMAL"),
                    response_mime_type="application/json",
                    max_output_tokens=1024,
                ),
            )
            parts = response.candidates[0].content.parts or []
            raw = "".join(p.text for p in parts if p.text and not getattr(p, "thought", False)).strip()
        except Exception as e:
            print(f"[Reflect] LLM call failed: {e}")
            return {"should_speak": False, "reasoning": f"api error: {e}"}

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            print(f"[Reflect] Bad JSON from LLM: {raw!r}")
            return {"should_speak": False, "reasoning": "parse error"}

    # ── presence detection ────────────────────────────────────────────────────

    async def _ping_phone(self) -> dict:
        """Read current presence state (kept fresh by presence_loop)."""
        presence = ui.get_presence()
        home = presence.get("phone", False)
        zone = ui.get_presence_zone("phone")  # e.g. "home", "work", "not_home"
        return {"home": home, "phone": home, "zone": zone}

    async def presence_loop(self) -> None:
        """Poll HA device_tracker every 30s — reliable across all WiFi extenders.

        Uses the HA companion app's GPS zone detection. No IP address needed —
        the companion app reports home/not_home when entering/leaving the Home zone.
        Entirely local: phone pushes to HA when on WiFi, no cloud required.
        """
        if not self._ha:
            print("[Presence] No HA client — skipping presence loop.")
            return

        entity = getattr(self.config, "presence_entity", "device_tracker.pixel_10_pro")

        # Seed last_state from DB so we catch transitions that happened while crashed
        try:
            last_entries = memory_module.get().recent_presence(limit=1)
            if last_entries:
                db_state = last_entries[0]["state"]  # stored zone string
                last_state: str | None = db_state
                ui.update_presence("phone", db_state == "home", zone=db_state)
            else:
                last_state = None
        except Exception:
            last_state = None

        print(f"[Presence] Polling {entity} via HA every 30s (last known: {last_state!r})...")

        # 5-minute debounce: only fire a zone transition after staying 5+ min
        ZONE_DEBOUNCE_SECS = 300
        _pending_zone: str | None = None
        _pending_since: float = 0.0
        last_real_zone: str | None = last_state if last_state and last_state != "not_home" else None

        while True:
            try:
                data = await self._ha.get_state(entity)
                # HA returns "home", "not_home", or a zone slug like "work", "gym", etc.
                raw = data.get("state", "unknown").lower().strip()

                # Normalise: "not_home" → keep as-is; named zones pass through
                zone = raw  # e.g. "home", "work", "not_home", "unknown"
                is_home = zone == "home"

                # Always refresh UI timestamp every poll — keeps presence window alive
                ui.update_presence("phone", is_home, zone=zone)

                if zone == "unknown":
                    pass
                elif zone == last_state:
                    # Back to (or still in) confirmed zone — cancel any pending debounce
                    if _pending_zone is not None:
                        print(f"[Presence] {entity}: returned to {zone!r} before 5min — debounce cancelled")
                        _pending_zone = None
                        _pending_since = 0.0
                elif zone == _pending_zone:
                    # Still in candidate zone — check if debounce period elapsed
                    elapsed = asyncio.get_event_loop().time() - _pending_since
                    if elapsed >= ZONE_DEBOUNCE_SECS:
                        prev = last_state
                        last_state = zone
                        _pending_zone = None
                        _pending_since = 0.0
                        was_home = prev == "home"
                        print(f"[Presence] {entity}: confirmed {prev!r} → {zone!r} (stayed {elapsed/60:.1f}min)")
                        if zone != "not_home":
                            last_real_zone = zone
                        effective_from = last_real_zone if prev == "not_home" and last_real_zone else prev
                        memory_module.get().log_presence(zone)
                        memory_module.get().emit_event("presence_change", {"from": effective_from, "to": zone})

                        if is_home and not was_home:
                            asyncio.create_task(self._on_arrival())

                        # Gym detection: start polling on work arrival, stop and log on departure
                        if zone == "work":
                            asyncio.create_task(self._start_gym_detection())
                        elif prev == "work":
                            if self._gym_poll_task:
                                self._gym_poll_task.cancel()
                                self._gym_poll_task = None
                            if self._at_gym:
                                asyncio.create_task(self._on_gym_left(reason="left_work"))
                else:
                    # New candidate zone — start debounce timer
                    _pending_zone = zone
                    _pending_since = asyncio.get_event_loop().time()
                    print(f"[Presence] {entity}: {last_state!r} → {zone!r} detected (waiting 5min to confirm)")

            except Exception as e:
                print(f"[Presence] HA poll error: {e}")

            await asyncio.sleep(30)


    # ── gym detection via barometric pressure ────────────────────────────────

    async def _get_pressure_hpa(self) -> float | None:
        """Read barometric pressure from Pixel phone via HA."""
        try:
            data = await self._ha.get_state("sensor.pixel_10_pro_pressure_sensor")
            return float(data["state"])
        except Exception as e:
            print(f"[Gym] Pressure read error: {e}")
            return None

    async def _start_gym_detection(self) -> None:
        """Called when zone → 'work'. Records office-floor baseline, starts pressure polling."""
        if self._gym_poll_task:
            self._gym_poll_task.cancel()
        self._at_gym = False
        self._work_pressure_baseline = None
        # Wait 60s for phone to physically settle in the building
        await asyncio.sleep(60)
        p = await self._get_pressure_hpa()
        if p is None:
            print("[Gym] Couldn't read pressure on work arrival — gym detection inactive this session")
            return
        self._work_pressure_baseline = p
        gym_threshold = p + 3.0
        print(f"[Gym] Work arrival baseline: {p:.1f} hPa — gym threshold: {gym_threshold:.1f} hPa")
        self._gym_poll_task = asyncio.create_task(self._gym_poll_loop())

    async def _gym_poll_loop(self) -> None:
        """Poll pressure every 2 min while at work zone to detect gym floor visits."""
        while True:
            await asyncio.sleep(120)
            if self._work_pressure_baseline is None:
                continue
            p = await self._get_pressure_hpa()
            if p is None:
                continue
            baseline = self._work_pressure_baseline
            if not self._at_gym and p >= baseline + 3.0:
                print(f"[Gym] At gym: P={p:.1f} vs baseline {baseline:.1f} (Δ+{p - baseline:.1f} hPa)")
                await self._on_gym_detected()
            elif self._at_gym and p < baseline + 1.0:
                print(f"[Gym] Left gym: P={p:.1f} dropped back near baseline {baseline:.1f}")
                await self._on_gym_left(reason="pressure_drop")

    async def _on_gym_detected(self) -> None:
        if self._at_gym:
            return
        self._at_gym = True
        self._gym_session_start = time.time()
        print("[Gym] Karthik is at the gym — emitting synthetic zone event")
        memory_module.get().log_presence("gym")
        memory_module.get().emit_event("presence_change", {"from": "work", "to": "gym"})

    async def _on_gym_left(self, reason: str = "unknown") -> None:
        if not self._at_gym:
            return
        self._at_gym = False
        duration_mins = max(15, int((time.time() - self._gym_session_start) / 60))
        print(f"[Gym] Left gym ({reason}): ~{duration_mins} min — emitting zone event")
        memory_module.get().log_presence("work")
        memory_module.get().emit_event("presence_change", {"from": "gym", "to": "work", "duration_mins": duration_mins})

    async def _fetch_fitness_summary(self) -> dict:
        """Fetch today's fitness summary from FitBot via A2A."""
        try:
            from a2a_client import call_fitbot
            result = await call_fitbot("karthik today summary")
            return {"available": True, "raw": result}
        except Exception as e:
            return {"available": False, "error": str(e)}

    async def _on_arrival(self) -> None:
        """Sunday is in the bedroom — Karthik won't hear TTS on arrival.
        The event_loop handles arrival home via the LLM (Telegram message)."""
        pass

    # ── world context ─────────────────────────────────────────────────────────

    async def _fetch_world(self) -> dict:
        if self._world_cache and (time.time() - self._world_cache_at) < WORLD_CACHE_TTL:
            return self._world_cache

        world: dict = {"weather": None, "searches": []}
        search_queries = [
            "IPL 2026 match today score result",
            "India cricket today",
            "space astronomy news",
            "AI technology news",
        ]

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                results = await asyncio.gather(
                    self._fetch_weather(session),
                    self._fetch_cricket_live(session),
                    *[self._news_search(session, q) for q in search_queries],
                    return_exceptions=True,
                )

            if not isinstance(results[0], Exception):
                world["weather"] = results[0]

            if not isinstance(results[1], Exception) and results[1]:
                world["cricket_live"] = results[1]

            for i, q in enumerate(search_queries):
                r = results[i + 2]
                if not isinstance(r, Exception) and r:
                    world["searches"].append({"query": q, "snippet": r})
        except Exception as e:
            print(f"[Reflect] World fetch error: {e}")

        self._world_cache = world
        self._world_cache_at = time.time()
        return world

    async def _fetch_room_sensors(self) -> dict:
        if not self._ha:
            return {}
        try:
            temp = await self._ha.get_state("sensor.lumi_lumi_sensor_ht_agl02_temperature")
            humidity = await self._ha.get_state("sensor.lumi_lumi_sensor_ht_agl02_humidity")
            return {
                "temperature_c": round(float(temp.get("state", 0)), 1),
                "humidity_pct": round(float(humidity.get("state", 0)), 1),
            }
        except Exception:
            return {}

    async def _fetch_weather(self, session: aiohttp.ClientSession) -> str | None:
        try:
            async with session.get("https://wttr.in/Hyderabad?format=j1") as r:
                data = await r.json(content_type=None)
            c = data["current_condition"][0]
            return f"{c['temp_C']}C, {c['weatherDesc'][0]['value'].lower()}, feels like {c['FeelsLikeC']}C"
        except Exception:
            return None

    async def _news_search(self, session: aiohttp.ClientSession, query: str) -> str | None:
        """Fetch top 3 headlines from Google News RSS for a query."""
        try:
            url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"
            async with session.get(url) as r:
                text = await r.text()
            root = ET.fromstring(text)
            titles = [
                item.findtext("title", "").strip()
                for item in root.findall(".//item")[:3]
                if item.findtext("title")
            ]
            return " | ".join(titles) if titles else None
        except Exception:
            return None

    async def _fetch_calendar_safe(self, target_date) -> list:
        """Fetch calendar events for a date, silently returning [] on error."""
        try:
            return await tool_registry._fetch_calendar(target_date)
        except Exception as e:
            print(f"[Reflect] Calendar fetch error: {e}")
            return []

    async def _fetch_cricket_live(self, session: aiohttp.ClientSession) -> str | None:
        """ESPN Cricinfo live scores RSS — dedicated cricket feed."""
        try:
            async with session.get("https://static.espncricinfo.com/rss/livescores.xml") as r:
                text = await r.text()
            root = ET.fromstring(text)
            scores = [
                item.findtext("title", "").strip()
                for item in root.findall(".//item")[:5]
                if item.findtext("title")
            ]
            return " | ".join(scores) if scores else None
        except Exception:
            return None
