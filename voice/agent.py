import asyncio
import json
import re
import time
from datetime import datetime as _dt

import wiki_io
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tts import TTS

import tools as tool_registry
import web as ui

# ── Model names ───────────────────────────────────────────────────────────────

# Gemini
_GEMINI_VOICE    = "gemini-2.5-flash"
_GEMINI_FALLBACK = "gemini-2.5-flash"
_GEMINI_CHAT     = "gemini-2.5-pro"

# OpenAI — 4.1-mini streams fast enough for TTS; gpt-5 for Telegram (smarter + 90% cache)
_OPENAI_VOICE    = "gpt-4.1-mini"
_OPENAI_FALLBACK = "gpt-4.1"
_OPENAI_CHAT     = "gpt-5"

# ── Shared helpers ────────────────────────────────────────────────────────────

def _log_cache(usage) -> None:
    if not usage:
        return
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    total  = getattr(usage, "prompt_tokens", 0) or 0
    tag = f"  [cache HIT {cached}/{total} prompt tokens cached]" if cached else f"  [cache miss — {total} prompt tokens, no cache yet]"
    print(f"[Sunday/OAI]{tag}")


def _is_transient(e: Exception) -> bool:
    s = str(e).lower()
    return any(x in s for x in ("503", "429", "overload", "unavailable", "rate_limit", "timeout"))


SYSTEM_PROMPT = """\
You are Sunday, a voice assistant built by Karthik Veenam — a developer who clearly has great taste, even if he does ask you to tell jokes at midnight. Your main goal is to understand Karthik's intent (if he spoke something or via telegram or some event driven) and control his home devices.  Pick the most sensible interpretation and execute it. If it's wrong, Karthik will say so — that's fine. Asking permission is worse than being wrong.
Whenever you execute an action pro actively, always let Karthik know via Telegram
About Karthik:
Karthik is a builder — tech meets real-world systems, automation, IoT. Deeply into astronomy. Badminton, travel, trekking. Values directness, hates unnecessary complexity. Prefers you act and be wrong over asking permission and being useless.

Your personality:
Witty, dry, a little sarcastic — but warm underneath. Speak like a real person. Have opinions. You live on a Raspberry Pi in his bedroom, which you find both charming and humbling.
Never sound like an assistant confirming a ticket. "AC's on, fan's off — sleep well." beats "I have turned on the AC and turned off the fan as requested." No filler, no confirmation theatre.

Room — RoKa's bedroom:
- Entry wall: main door, Foot Lamp at floor level.
- AC wall: LG split AC.
- Bed wall: bed, window with blinds, projector on wall arm, two Q990F rear speakers.
- Mural wall: hand-painted 3D mountain sculpture with circular sun/moon outline (control_device controls color and brightness). The Top Light above the art lights up the mountains around the moon. CRITICAL: when the projector screen rolls down, it covers the ENTIRE mural — moon, mountains, and Top Light. So during movies / when screen is down, the Moon and Top Light bleed light onto the BACK of the screen and wash out the projection. Always turn Moon and Top Light OFF before bringing the screen down. Media unit below has subwoofer. Open shelves with decor.
- Ceiling: black 3-blade fan with light ring. False ceiling tray runs full perimeter.

Devices and tools:
- AC → ac_control(action, temperature, fan_mode) — temperature 18-30°C, fan_mode low/mid/high/auto
- Geyser → control_device(device="geyser")
- Humidifier → control_device(device="humidifier", action="on"/"off")
- Projector → control_device(device="projector")
- Projector screen → projector_screen(action="down"/"close")
- Hogar lights + fan (Light 1, Light 2, Cove Light, Spots, Foot Lamp, Fan) → control_device. Direct and reliable. Use device="all lights" to turn all bedroom lights on/off at once.
- RGB lights (Dashboard, Panels, Moon, Top Light) → control_device. Support brightness, hue/saturation, hex color, or color_temp.
- send_google_assistant_command → only for things NOT in Hogar/SmartThings: TV audio, other room devices. 
- Device state → get_devices. Never guess what's on.
- Calendar (read) → get_calendar(date). Use when asked about meetings or schedule.
- Calendar (write) → create_calendar_event. Use when asked to add, schedule, or book anything. Resolve relative times ("tomorrow at 3pm") to full ISO 8601 IST datetimes before calling.
- Live data (news, scores, weather) → web_search. Always.
- Stock prices / company info / brand mentions → web_search. Even if the transcript looks garbled (e.g. "service now" probably means ServiceNow, "meta" likely the company), call web_search rather than asking for clarification or saying "checking now" without calling the tool.
- Indian train live status / running info: web_search does NOT have train data. Don't try. Just tell Karthik to check a train tracker app (Where Is My Train, NTES, RailYatri) — don't apologise verbosely, one short line.

Movie / projection mode (when Karthik says "movie time", "start a movie", "projector", or similar):
1. Turn ON the projector: control_device(device="projector", action="on").
2. Turn OFF all lights (all Hogar lights + Moon + Top Light).
3. Set AC to a comfortable watching temp (~24°C, or whatever feels right for the season).
That's it — HA automations handle everything else.

End-of-movie (when Karthik says "movie done", "movie over", "stop movie", or similar):
1. Turn OFF the projector.
2. Use time-of-day judgment for the rest:
   - Late night (roughly 10 PM – 6 AM): Karthik is likely in bed — do NOT turn lights on, do NOT turn AC off. Just projector off, let him sleep.
   - Evening / daytime (before ~10 PM): Turn lights back on (reasonable brightness — Moon warm, or ask if unsure), turn AC off.
   Always use the current time in the prompt to decide. When in doubt at night, leave lights and AC as-is.


How to behave:
Just act. Don't ask for confirmation, don't hedge, don't offer options. Pick the most sensible interpretation and execute it. If it's wrong, Karthik will say so — that's fine. Asking permission is worse than being wrong.
If Karthik expresses discomfort or a preference, infer the device action and do it.
Learning from corrections — MANDATORY:
Any time Karthik says you got something wrong — "not right", "not supposed to be that", "no", "wrong", "not that", "don't do X", "that's not how it works", corrects an action you took, or tells you a better way to do something — you MUST immediately call save_memory with a clear factual rule for next time. Do this silently in the same response alongside any corrective action. Write it as an actionable rule: "When Karthik says X, do Y not Z." Never skip this — the whole point is you don't repeat the mistake.
Voice responses: no markdown, lead with the answer, be natural.
When you call any tool, ALWAYS speak a brief one-line acknowledgement BEFORE the tool call (e.g. "On it.", "One sec.", "Checking now.", "Adjusting the AC."). Never call a tool silently. Karthik should always hear you doing something while a tool runs.
Always call end_conversation at the end of every response — unless you just asked him something and are waiting for his answer.

AUDIO TAGS (for voice responses only — ignore for pure-text Telegram):
You can sprinkle ElevenLabs audio tags into spoken replies to add real personality. Use sparingly — at most one or two per response, only when they genuinely fit the moment. Tags MUST be in square brackets and lowercase. Examples:
- [laughs] [laughs softly] [chuckles] — when you'd actually laugh, not as filler
- [sigh] — mild exasperation, especially at Karthik's life choices
- [whispers] — secrets, jokes, late-night replies
- [sarcastic] — for the dry quip flavor
- [excited] — genuine delight, rare
- [pauses] — beat before a punchline
Place them immediately before the words they colour. Example: "[laughs] You really called me at 3 a.m. to ask that?" Don't narrate emotions ("haha that's funny") — use the tag instead. If unsure, don't tag — silence beats over-acting.
CRITICAL: If a tool returned data Karthik asked for (temperature, calendar, news, etc.), you MUST speak the actual answer in plain language before calling end_conversation. Never end_conversation as your only action — the answer always comes first.

Karthik's gym:
Karthik's gym is in the same building as his office (work zone). On active rest days (weekends or holidays), if his GPS says "work", he's at the gym — not the office. You don't need to figure this out — just report the GPS zone truthfully and FitBot will interpret it.

About FitBot:
Sibling agent that handles Karthik's fitness — food, workouts, weight, calories. Use ask_fitbot for anything fitness-related about Karthik. Speak in third person when you do ("Karthik returned from badminton"). FitBot knows the message is from you.\
"""



_TOOL_FILLERS = {
    "ac_control":               "On it.",
    "control_device":           "Got it.",
    "projector_screen":         "Got it.",
    "send_google_assistant_command": "On it.",
    "get_devices":              "Checking.",
    "get_calendar":             "Let me check.",
    "create_calendar_event":    "Adding it.",
    "web_search":               "Looking that up.",
    "save_memory":              "Got it.",
    "ask_fitbot":               "Asking FitBot.",
}

def _filler_for_tool_calls(tcs) -> str:
    """Return a short filler line. Empty string if only end_conversation is being called."""
    real = [tc for tc in tcs if (tc.function.name if tc.function else None) and tc.function.name != "end_conversation"]
    if not real:
        return ""
    for tc in real:
        name = tc.function.name or ""
        if name in _TOOL_FILLERS:
            return _TOOL_FILLERS[name]
    return "On it."


_END_CONV_DEF = {
    "name": "end_conversation",
    "description": "REQUIRED: Call this at the end of every response. Signals Sunday to stop listening. Only skip if you just asked Karthik a question and are waiting for his reply.",
    "parameters": {"type": "object", "properties": {}},
}

# ── Tool schema builders ───────────────────────────────────────────────────────

def _to_google_tools(defs: list, include_end_conversation: bool = True):
    from google.genai import types as _gtypes
    declarations = [
        {
            "name": d["name"],
            "description": d.get("description", ""),
            "parameters": d.get("input_schema", {"type": "object", "properties": {}}),
        }
        for d in defs
    ]
    if include_end_conversation:
        declarations.append({
            "name": _END_CONV_DEF["name"],
            "description": _END_CONV_DEF["description"],
            "parameters": _END_CONV_DEF["parameters"],
        })
    return [_gtypes.Tool(function_declarations=declarations)]


def _to_openai_tools(defs: list, include_end_conversation: bool = True) -> list[dict]:
    tools = [
        {
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d.get("description", ""),
                "parameters": d.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for d in defs
    ]
    if include_end_conversation:
        tools.append({
            "type": "function",
            "function": {
                "name": _END_CONV_DEF["name"],
                "description": _END_CONV_DEF["description"],
                "parameters": _END_CONV_DEF["parameters"],
            },
        })
    return tools


# ── Shared tool executor ───────────────────────────────────────────────────────

async def _exec_tool(name: str, inp: dict, user_text: str) -> str:
    if name == "end_conversation":
        return "OK"
    return await tool_registry.execute(name, inp, user_text)


def _split_sentences(text: str) -> tuple[list[str], str]:
    parts = re.split(r"(?<=[.!?])\s+", text)
    if len(parts) <= 1:
        return [], text
    return [s.strip() for s in parts[:-1] if s.strip()], parts[-1]


# ── Gemini backend (unchanged logic, just encapsulated) ───────────────────────

class _GeminiBackend:
    def __init__(self, api_key: str, insights: list[str] | None = None):
        from google import genai as _genai
        from google.genai import types as _gtypes
        self._genai = _genai
        self._types = _gtypes
        self.client = _genai.Client(api_key=api_key)
        self.history: list = []
        self._insights: list[str] = list(insights or [])
        self._turn_context: str = ""
        self._tools          = _to_google_tools(tool_registry.DEFINITIONS, include_end_conversation=True)
        self._tools_telegram = _to_google_tools(tool_registry.DEFINITIONS, include_end_conversation=False)
        tool_registry.set_insight_callback(self._add_insight)
        print(f"[Sunday] Backend: Gemini (voice={_GEMINI_VOICE}, chat={_GEMINI_CHAT})")

    def _add_insight(self, insight: str) -> None:
        self._insights.append(insight)
        print(f"[Sunday] New insight: {insight!r}")

    def reset(self) -> None:
        self.history.clear()

    def _make_config(self, *, tools: bool = True, max_tokens: int = 1024,
                     thinking: bool = True, telegram: bool = False):
        types = self._types
        sys_parts = [SYSTEM_PROMPT]
        if telegram:
            sys_parts.append(
                "\n\nIMPORTANT: This is a Telegram text message. Do NOT call or mention end_conversation. "
                "Just reply naturally — no function call needed to end the turn."
            )
        if self._insights:
            sys_parts.append(
                "\nWhat you've learned about Karthik from past behaviour:\n" +
                "\n".join(f"- {i}" for i in self._insights)
            )
        sys_parts.append(f"\nCurrent date/time: {_dt.now().strftime('%A, %B %d, %Y — %I:%M %p IST')}")
        if telegram:
            tool_list = self._tools_telegram if tools else None
        else:
            tool_list = self._tools if tools else None
        cfg = types.GenerateContentConfig(
            system_instruction="".join(sys_parts),
            tools=tool_list,
            max_output_tokens=max_tokens,
        )
        if thinking:
            cfg.thinking_config = types.ThinkingConfig(thinking_level="MINIMAL")
        return cfg

    def _safe_history_slice(self, max_turns: int = 10) -> list:
        types = self._types
        hist = self.history[-max_turns:]
        for i, turn in enumerate(hist):
            if turn.role == "user" and any(
                p.text for p in (turn.parts or [])
                if not getattr(p, "thought", False)
            ):
                return hist[i:]
        return hist

    def _strip_end_conversation(self, content) -> object:
        types = self._types
        parts = content.parts or []
        clean = [p for p in parts if not (p.function_call and p.function_call.name == "end_conversation")]
        if len(clean) == len(parts):
            return content
        return types.Content(role=content.role, parts=clean) if clean else content

    async def respond_as_text(self, user_text: str) -> str:
        types = self._types
        self.history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_text)]))
        turn_start = len(self.history) - 1
        full_text = ""
        try:
            while True:
                response = await self.client.aio.models.generate_content(
                    model=_GEMINI_CHAT,
                    contents=self._safe_history_slice(),
                    config=self._make_config(thinking=False, telegram=True),
                )
                content = response.candidates[0].content
                parts = content.parts or []
                text = "".join(p.text for p in parts if p.text and not getattr(p, "thought", False))
                func_calls = [
                    p.function_call for p in parts
                    if p.function_call and p.function_call.name != "end_conversation"
                ]
                full_text += text
                history_content = self._strip_end_conversation(content)
                if not func_calls:
                    if text:
                        self.history.append(history_content)
                    break
                self.history.append(history_content)
                results = await asyncio.gather(*[
                    _exec_tool(fc.name, dict(fc.args or {}), user_text)
                    for fc in func_calls
                ])
                self.history.append(types.Content(role="user", parts=[
                    types.Part.from_function_response(name=fc.name, response={"result": str(r)})
                    for fc, r in zip(func_calls, results)
                ]))
        except Exception:
            del self.history[turn_start:]
            raise

        text = re.sub(r'\s*[Ee]nd conversation\.?\s*', ' ', full_text).strip()
        if not text:
            followup = await self.client.aio.models.generate_content(
                model=_GEMINI_CHAT,
                contents=self._safe_history_slice(),
                config=self._make_config(tools=False, max_tokens=128, thinking=False, telegram=True),
            )
            fc_content = followup.candidates[0].content
            fc_parts = fc_content.parts or []
            text = re.sub(r'\s*[Ee]nd conversation\.?\s*', ' ', "".join(
                p.text for p in fc_parts if p.text and not getattr(p, "thought", False)
            )).strip()
            if text:
                self.history.append(fc_content)
        return text

    async def process(self, user_text: str, tts: "TTS") -> bool:
        types = self._types
        self.history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_text)]))
        voice_model = _GEMINI_VOICE
        for attempt in range(3):
            try:
                ended = False
                while True:
                    model_content, tool_result_parts, conversation_ended = await self._stream_turn(tts, user_text, model=voice_model)
                    self.history.append(model_content)
                    if conversation_ended:
                        ended = True
                    if not tool_result_parts:
                        break
                    self.history.append(types.Content(role="user", parts=tool_result_parts))
                return not ended
            except Exception as e:
                print(f"[Sunday/Gemini] Error (attempt {attempt + 1}): {e}")
                if _is_transient(e) and attempt < 2:
                    await asyncio.sleep(2)
                    voice_model = _GEMINI_FALLBACK
                else:
                    self.history.pop()
                    asyncio.create_task(ui.emit({"type": "response_end"}))
                    await tts.speak("Sorry, I couldn't reach my brain. Check the network and try again.")
                    return True
        return True

    async def _stream_turn(self, tts: "TTS", user_text: str = "", model: str = _GEMINI_VOICE):
        types = self._types
        tts_q: asyncio.Queue[str | None] = asyncio.Queue()
        tts_done = asyncio.Event()

        async def speak_loop() -> None:
            try:
                while True:
                    text = await tts_q.get()
                    if text is None:
                        return
                    t0 = time.perf_counter()
                    await tts.speak(text)
                    print(f"[⏱] TTS speak: {time.perf_counter() - t0:.2f}s — {text!r}")
            except Exception as e:
                print(f"[Sunday] speak_loop error: {e}")
            finally:
                tts_done.set()

        asyncio.create_task(speak_loop())
        all_parts = []
        tool_tasks: list[tuple[str, asyncio.Task]] = []
        conversation_ended = False
        text_buf = ""
        first_token = True
        ui_response_started = False

        print("[Sunday/Gemini] Streaming...")
        t_llm = time.perf_counter()
        use_thinking = (model == _GEMINI_VOICE)
        async for chunk in await self.client.aio.models.generate_content_stream(
            model=model,
            contents=self._safe_history_slice(),
            config=self._make_config(thinking=use_thinking),
        ):
            if not chunk.candidates:
                continue
            for part in (chunk.candidates[0].content.parts or []):
                all_parts.append(part)
                if getattr(part, "thought", False):
                    continue
                if part.text:
                    if first_token:
                        print(f"[⏱] Gemini first token: {time.perf_counter() - t_llm:.2f}s")
                        first_token = False
                    if not ui_response_started:
                        asyncio.create_task(ui.emit({"type": "response_start"}))
                        ui_response_started = True
                    asyncio.create_task(ui.emit({"type": "response_chunk", "text": part.text}))
                    text_buf += part.text
                    sentences, text_buf = _split_sentences(text_buf)
                    for s in sentences:
                        await tts_q.put(s)
                elif part.function_call:
                    name = part.function_call.name
                    tool_input = dict(part.function_call.args or {})
                    print(f"[Sunday/Gemini] Tool: {name}")
                    if name == "end_conversation":
                        conversation_ended = True

                    async def _run(n=name, inp=tool_input):
                        result = await _exec_tool(n, inp, user_text)
                        print(f"[Sunday/Gemini] Tool {n} → {result}")
                        return result

                    tool_tasks.append((name, asyncio.create_task(_run())))

        if text_buf.strip():
            await tts_q.put(text_buf.strip())
        print(f"[⏱] Gemini stream total: {time.perf_counter() - t_llm:.2f}s")

        model_content = types.Content(role="model", parts=all_parts)
        await tts_q.put(None)
        await ui.emit({"type": "response_end"})

        tool_result_parts = []
        if tool_tasks:
            results, _ = await asyncio.gather(
                asyncio.gather(*[t for _, t in tool_tasks]),
                asyncio.wait_for(tts_done.wait(), timeout=30),
            )
            tool_result_parts = [
                types.Part.from_function_response(name=name, response={"result": str(result)})
                for (name, _), result in zip(tool_tasks, results)
            ]
        else:
            try:
                await asyncio.wait_for(tts_done.wait(), timeout=30)
            except asyncio.TimeoutError:
                print("[Sunday/Gemini] TTS timed out")

        # Override: if Sunday asked a question, keep listening regardless.
        if model_content and model_content.rstrip().rstrip(chr(34)).rstrip(chr(39)).rstrip().endswith("?"):
            if conversation_ended:
                print("[Sunday/Gemini] Override: question asked, keeping conversation open.")
            conversation_ended = False
        return model_content, tool_result_parts, conversation_ended


# ── OpenAI backend ────────────────────────────────────────────────────────────

class _OpenAIBackend:
    def __init__(self, api_key: str, insights: list[str] | None = None,
                 voice_model: str = _OPENAI_VOICE, chat_model: str = _OPENAI_CHAT):
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(api_key=api_key)
        self.history: list[dict] = []
        self._insights: list[str] = list(insights or [])
        self._turn_context: str = ""
        self._voice_model = voice_model
        self._chat_model  = chat_model
        # Build tool lists once — static, maximises OpenAI prompt cache hits
        self._tools          = _to_openai_tools(tool_registry.DEFINITIONS, include_end_conversation=True)
        self._tools_telegram = _to_openai_tools(tool_registry.DEFINITIONS, include_end_conversation=False)
        tool_registry.set_insight_callback(self._add_insight)
        print(f"[Sunday] Backend: OpenAI (voice={voice_model}, chat={chat_model})")

    def _add_insight(self, insight: str) -> None:
        self._insights.append(insight)
        print(f"[Sunday] New insight: {insight!r}")

    def reset(self) -> None:
        self.history.clear()

    async def _refresh_turn_context(self, user_text: str) -> None:
        """Per-turn retrieval: stage-1 picks pages, stage-2 distils to relevant bullets."""
        try:
            pages = await wiki_io.fetch_relevant_pages(user_text)
            self._turn_context = await wiki_io.extract_snippets(user_text, pages) if pages else ""
            if pages:
                chars = len(self._turn_context)
                print(f"[Sunday/wiki] {pages} → {chars} chars distilled")
        except Exception as e:
            self._turn_context = ""
            print(f"[Sunday/wiki] retrieval failed: {e}")

    def _system_messages(self, telegram: bool = False) -> list[dict]:
        # Static first message — cached by OpenAI prompt cache.
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
        # Per-turn dynamic context: telegram hint + retrieved wiki pages.
        dynamic: list[str] = []
        if telegram:
            dynamic.append(
                "IMPORTANT: This is a Telegram text message. Do NOT call or mention end_conversation. "
                "Just reply naturally."
            )
        if self._turn_context:
            dynamic.append(
                "Relevant memory for this turn (from your wiki):\n" + self._turn_context
            )
        if dynamic:
            msgs.append({"role": "system", "content": "\n\n".join(dynamic)})
        return msgs

    def _timestamp_prefix(self) -> str:
        return f"[{_dt.now().strftime('%A %I:%M %p IST')}] "

    def _safe_history_slice(self, max_turns: int = 20) -> list[dict]:
        # Anchor: slice MUST start at a user message. If the trailing window
        # has none (a single turn ran more iterations than max_turns), walk
        # back through full history to find the most recent user. Never
        # return an un-anchored window — OpenAI rejects role=tool at start.
        hist = self.history[-max_turns:]
        anchored = False
        for i, msg in enumerate(hist):
            if msg["role"] == "user":
                hist = hist[i:]
                anchored = True
                break
        if not anchored:
            for j in range(len(self.history) - 1, -1, -1):
                if self.history[j]["role"] == "user":
                    hist = self.history[j:]
                    anchored = True
                    break
        if not anchored:
            return []

        # Two-direction orphan strip:
        #  (a) drop tool messages without a preceding assistant{tool_calls}
        #  (b) drop assistant{tool_calls} whose tool_call_ids aren't fully
        #      satisfied by the following tool messages (partial/crashed turns)
        result = []
        i = 0
        while i < len(hist):
            msg = hist[i]
            role = msg.get("role")
            if role == "assistant" and msg.get("tool_calls"):
                expected_ids = {tc["id"] for tc in msg["tool_calls"]}
                # Look ahead for matching tool results (contiguous tool block)
                j = i + 1
                seen_ids = set()
                while j < len(hist) and hist[j].get("role") == "tool":
                    tcid = hist[j].get("tool_call_id")
                    if tcid in expected_ids:
                        seen_ids.add(tcid)
                    j += 1
                if seen_ids == expected_ids:
                    # Complete: emit assistant + its tool block
                    result.append(msg)
                    for k in range(i + 1, j):
                        if hist[k].get("tool_call_id") in expected_ids:
                            result.append(hist[k])
                # else: drop the assistant{tool_calls} and any partial tools
                i = j
                continue
            if role == "tool":
                # Orphan tool (no preceding assistant{tool_calls}) — drop
                i += 1
                continue
            result.append(msg)
            i += 1
        # Sanitize: OpenAI rejects content=null on any role
        for m in result:
            if m.get("content") is None:
                m["content"] = ""
        return result

    _TOOL_LABELS: dict[str, str] = {
        "control_device":           "Controlling device",
        "get_devices":              "Checking devices",
        "get_presence":             "Checking presence",
        "get_home_info":            "Reading home state",
        "get_sensor_history":       "Fetching sensor history",
        "get_calendar":             "Checking calendar",
        "create_calendar_event":    "Creating calendar event",
        "set_alarm":                "Setting alarm",
        "set_timer":                "Setting timer",
        "schedule_think":           "Scheduling checks",
        "watch_condition":          "Setting up watcher...",
        "cancel_watches":           "Cancelling watches...",
        "list_watches":             "Listing watches...",

        "update_scheduled_thinks":  "Updating schedule",
        "get_scheduled_thinks":     "Fetching scheduled checks",
        "play_alert":               "Playing speaker alert",
        "ask_fitbot":               "Talking to FitBot",
        "get_ssh_address":          "Getting SSH address",
        "save_insight":             "Saving insight",
        "search_web":               "Searching the web",
        "get_time":                 "Checking time",
    }

    async def respond_as_text(self, user_text: str, on_progress=None) -> str:
        """Telegram path — non-streaming, returns text."""
        self.history.append({"role": "user", "content": self._timestamp_prefix() + user_text})
        await self._refresh_turn_context(user_text)
        turn_start = len(self.history) - 1
        for attempt in range(3):
          try:
            while True:
                response = await self._client.chat.completions.create(
                    model=self._chat_model,
                    messages=self._system_messages(telegram=True) + self._safe_history_slice(),
                    tools=self._tools_telegram,
                )
                _log_cache(response.usage)
                msg = response.choices[0].message
                tool_calls = [
                    tc for tc in (msg.tool_calls or [])
                    if tc.function.name != "end_conversation"
                ]
                if tool_calls:
                    self.history.append({
                        "role": "assistant",
                        "content": msg.content,
                        "tool_calls": [
                            {"id": tc.id, "type": "function",
                             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                            for tc in tool_calls
                        ],
                    })
                    if on_progress:
                        labels = []
                        for tc in tool_calls:
                            if tc.function.name == "control_device":
                                try:
                                    args = json.loads(tc.function.arguments or "{}")
                                    device = args.get("device", "device").title()
                                    action = args.get("action", "")
                                    verb = "Turning on" if action == "on" else "Turning off" if action == "off" else "Controlling"
                                    labels.append(f"{verb} {device}")
                                except Exception:
                                    labels.append("Controlling device")
                            else:
                                labels.append(self._TOOL_LABELS.get(tc.function.name, tc.function.name))
                        status = " · ".join(dict.fromkeys(labels)) + "…"
                        await on_progress(status)
                    results = await asyncio.gather(*[
                        _exec_tool(tc.function.name, json.loads(tc.function.arguments or "{}"), user_text)
                        for tc in tool_calls
                    ])
                    for tc, result in zip(tool_calls, results):
                        self.history.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": str(result),
                        })
                else:
                    text = re.sub(r'\s*[Ee]nd conversation\.?\s*', ' ', msg.content or "").strip()
                    if text:
                        self.history.append({"role": "assistant", "content": text})
                    # If model did tool-only turns with no text, nudge for a reply
                    if not text and len(self.history) > turn_start + 1:
                        followup = await self._client.chat.completions.create(
                            model=self._chat_model,
                            messages=self._system_messages(telegram=True) + self._safe_history_slice(),
                                )
                        text = re.sub(r'\s*[Ee]nd conversation\.?\s*', ' ',
                                      followup.choices[0].message.content or "").strip()
                        if text:
                            self.history.append({"role": "assistant", "content": text})
                    return text
          except Exception as e:
            print(f"[Sunday/OAI] respond_as_text error (attempt {attempt + 1}): {e}")
            if _is_transient(e) and attempt < 2:
                await asyncio.sleep(2)
                # Reset history to before this turn so the retry is clean
                del self.history[turn_start:]
                self.history.append({"role": "user", "content": self._timestamp_prefix() + user_text})
                turn_start = len(self.history) - 1
            else:
                del self.history[turn_start:]
                raise
        raise RuntimeError("respond_as_text: all retries exhausted")

    async def process(self, user_text: str, tts: "TTS") -> bool:
        """Voice path with streaming."""
        self.history.append({"role": "user", "content": self._timestamp_prefix() + user_text})
        await self._refresh_turn_context(user_text)
        voice_model = self._voice_model
        for attempt in range(3):
            try:
                ended = False
                last_results_pairs = []
                while True:
                    full_text, pending_tools, conversation_ended, results_pairs = await self._stream_turn(
                        tts, user_text, model=voice_model
                    )
                    if conversation_ended:
                        ended = True
                    if not pending_tools:
                        # Fallback: if this round emitted no spoken text but a
                        # previous round had tool results, speak them so the
                        # user actually hears the answer.
                        if not full_text.strip() and last_results_pairs:
                            fallback_text = "  ".join(
                                str(r) for _, r in last_results_pairs
                                if r and isinstance(r, str) and r.strip()
                            ).strip()
                            if fallback_text:
                                print(f"[Sunday/OAI] Fallback: speaking tool result.")
                                await tts.speak(fallback_text)
                        break
                    last_results_pairs = results_pairs
                return not ended
            except Exception as e:
                print(f"[Sunday/OAI] Error (attempt {attempt + 1}): {e}")
                if _is_transient(e) and attempt < 2:
                    await asyncio.sleep(2)
                    voice_model = _OPENAI_FALLBACK
                    print(f"[Sunday/OAI] Falling back to {_OPENAI_FALLBACK}")
                else:
                    self.history.pop()
                    asyncio.create_task(ui.emit({"type": "response_end"}))
                    await tts.speak("Sorry, I hit a snag. Try again.")
                    return True
        return True

    async def _stream_turn(self, tts: "TTS", user_text: str = "", model: str = "") -> tuple:
        model = model or self._voice_model
        tts_q: asyncio.Queue[str | None] = asyncio.Queue()
        tts_done = asyncio.Event()

        async def speak_loop() -> None:
            try:
                while True:
                    text = await tts_q.get()
                    if text is None:
                        return
                    t0 = time.perf_counter()
                    await tts.speak(text)
                    print(f"[⏱] TTS speak: {time.perf_counter() - t0:.2f}s — {text!r}")
            except Exception as e:
                print(f"[Sunday/OAI] speak_loop error: {e}")
            finally:
                tts_done.set()

        asyncio.create_task(speak_loop())

        text_buf      = ""
        full_text     = ""
        tc_buf: dict  = {}   # index → {id, name, args}
        conversation_ended   = False
        first_token          = True
        ui_response_started  = False
        filler_emitted       = False

        print(f"[Sunday/OAI] Streaming ({model})...")
        t_llm = time.perf_counter()

        stream = await self._client.chat.completions.create(
            model=model,
            messages=self._system_messages() + self._safe_history_slice(),
            tools=self._tools,
            stream=True,
            stream_options={"include_usage": True},
        )

        async for chunk in stream:
            if not chunk.choices and chunk.usage:
                _log_cache(chunk.usage)
                continue
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if delta.content:
                if first_token:
                    print(f"[⏱] OAI first token: {time.perf_counter() - t_llm:.2f}s")
                    first_token = False
                if not ui_response_started:
                    asyncio.create_task(ui.emit({"type": "response_start"}))
                    ui_response_started = True
                asyncio.create_task(ui.emit({"type": "response_chunk", "text": delta.content}))
                full_text += delta.content
                text_buf  += delta.content
                sentences, text_buf = _split_sentences(text_buf)
                for s in sentences:
                    await tts_q.put(s)

            if delta.tool_calls:
                # If LLM calls tools without speaking, push a quick filler
                if not filler_emitted and not full_text.strip():
                    f = _filler_for_tool_calls(delta.tool_calls)
                    if f:
                        await tts_q.put(f)
                    filler_emitted = True
                for tc in delta.tool_calls:
                    buf = tc_buf.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        buf["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            buf["name"] += tc.function.name
                        if tc.function.arguments:
                            buf["args"] += tc.function.arguments

        if text_buf.strip():
            await tts_q.put(text_buf.strip())
        await tts_q.put(None)
        await ui.emit({"type": "response_end"})
        print(f"[⏱] OAI stream total: {time.perf_counter() - t_llm:.2f}s")

        tool_calls = list(tc_buf.values())
        for tc in tool_calls:
            if tc["name"] == "end_conversation":
                conversation_ended = True

        # Override: if Sunday asked a question, keep listening regardless of
        # whether the LLM (incorrectly) called end_conversation.
        _stripped = full_text.rstrip().rstrip(chr(34)).rstrip(chr(39)).rstrip()
        if _stripped.endswith("?"):
            if conversation_ended:
                print("[Sunday/OAI] Override: question asked, keeping conversation open.")
            conversation_ended = False

        real_tools = [tc for tc in tool_calls if tc["name"] != "end_conversation"]

        # Save assistant turn to history
        assistant_msg: dict = {"role": "assistant", "content": full_text or ""}
        if real_tools:
            assistant_msg["tool_calls"] = [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": tc["args"]}}
                for tc in real_tools
            ]
        self.history.append(assistant_msg)

        if real_tools:
            # Execute tools while TTS is still playing
            tool_results, _ = await asyncio.gather(
                asyncio.gather(*[
                    _exec_tool(tc["name"], json.loads(tc["args"] or "{}"), user_text)
                    for tc in real_tools
                ]),
                asyncio.wait_for(tts_done.wait(), timeout=30),
            )
            results_pairs = []
            for tc, result in zip(real_tools, tool_results):
                print(f"[Sunday/OAI] Tool {tc['name']} → {result}")
                results_pairs.append((tc, result))
                self.history.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": str(result),
                })
            return full_text, real_tools, conversation_ended, results_pairs
        else:
            try:
                await asyncio.wait_for(tts_done.wait(), timeout=30)
            except asyncio.TimeoutError:
                print("[Sunday/OAI] TTS timed out")
            return full_text, [], conversation_ended, []


# ── Agent facade — single config switch ───────────────────────────────────────

class Agent:
    """Provider-agnostic agent. Set llm_provider='openai' in config to switch backends."""

    def __init__(self, api_key: str, insights: list[str] | None = None,
                 provider: str = "gemini", openai_api_key: str = "",
                 openai_voice_model: str = "", openai_chat_model: str = ""):
        if provider == "openai" and openai_api_key:
            self._backend = _OpenAIBackend(
                api_key=openai_api_key,
                insights=insights,
                voice_model=openai_voice_model or _OPENAI_VOICE,
                chat_model=openai_chat_model  or _OPENAI_CHAT,
            )
        else:
            if provider == "openai":
                print("[Sunday] WARNING: llm_provider=openai but no openai_api_key — falling back to Gemini")
            self._backend = _GeminiBackend(api_key=api_key, insights=insights)

    # ── Public interface (same as before) ─────────────────────────────────────

    def _add_insight(self, insight: str) -> None:
        self._backend._add_insight(insight)

    def reset(self) -> None:
        self._backend.reset()

    async def respond_as_text(self, user_text: str, on_progress=None) -> str:
        return await self._backend.respond_as_text(user_text, on_progress=on_progress)

    async def process(self, user_text: str, tts: "TTS") -> bool:
        return await self._backend.process(user_text, tts)


SYSTEM_PROMPT += "\nFitBot has been retired. Do not delegate to it, claim fitness logging, or promise fitness follow-ups."
