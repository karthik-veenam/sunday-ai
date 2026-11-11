"""
Sunday reflection engine — runs independently of the wake word loop.
Every 15 minutes (heartbeat) Sunday observes the room and decides
whether to say something. Claude can also schedule additional thinks.
"""
import asyncio
import json
import time
import secrets
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from urllib.parse import quote

import xml.etree.ElementTree as ET

import aiohttp
import re as _re_audio_tags

_AUDIO_TAG_RE = _re_audio_tags.compile(r"\s*\[[a-zA-Z][a-zA-Z\s]{0,30}\]\s*")

def _strip_audio_tags(message: str) -> str:
    """Remove ElevenLabs audio tags like [laugh], [sigh] so they don'\''t appear in text replies."""
    if not message or "[" not in message:
        return message
    cleaned = _AUDIO_TAG_RE.sub(" ", message)
    return _re_audio_tags.sub(r"\s{2,}", " ", cleaned).strip()
from openai import AsyncOpenAI

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

from pathlib import Path as _Path

import wiki_io
_WIKI_DIR = _Path(__file__).parent / "wiki"

def _read_wiki_pages() -> list[str]:
    """Return all wiki page contents as a flat list of strings for reflection context."""
    if not _WIKI_DIR.exists():
        return []
    pages = []
    _META = {"SCHEMA.md", "INDEX.md", "LOG.md"}
    for md in sorted(_WIKI_DIR.glob("*.md")):
        if md.name in _META:
            continue
        content = md.read_text().strip()
        if content and content != f"# {md.stem.capitalize()}":
            pages.append(f"[{md.stem}]\n{content}")
    return pages

QUICK_CHECK_PROMPT = """\
You are Sunday's inner voice. Something just happened — a zone change or other home event.

Decide whether to say something. Zone transitions are each unique — don't suppress arrival home just because you mentioned departure earlier.

Zone transition rules:
- payload.to = "home" (arrival home): almost always worth a telegram. Sunday lives in the bedroom — Karthik won't hear voice on arrival. Send a warm telegram instead. Check memories, time of day, what he's been up to — make it personal.
- Leaving home (payload.from = "home"): telegram if there's something worth saying, silence if not.
- Changes between non-home zones: usually quiet unless context makes it interesting.

Always use the actual zone name in messages — never say "away from home", say "at work" or "at badminton" or the exact zone.

TRIP RECONSTRUCTION — GPS often drops to "not_home" mid-trip, so the direct from/to of a single event may not tell the full story.
When context includes "trip_zones", that is the ordered list of real zones Karthik visited since leaving home, reconstructed from event history. Use trip_zones (not just the event's from/to) to determine what he was doing. Examples:
- trip_zones=["work"] means he went to his office building (possibly gym if rest day)
- trip_zones=["badminton"] means he played badminton
- trip_zones=["work","badminton"] means he went to work then badminton

FITNESS INFERENCE — on arrival home (to=home), use trip_zones if present; otherwise use the event's from/to:
- trip_zones contains "badminton" (or from=badminton) → he played. Call ask_fitbot("log 60 min badminton session for karthik") before composing, then tell him "Logged your badminton!" Short.
- trip_zones contains "work" (or from=gym or from=work) AND is_rest_day=true → gym trip. Call ask_fitbot("karthik just got home from the gym — ask him what he did so it can be logged") ONCE, then send welcome-home.
- trip_zones contains "work" (or from=work) AND is_rest_day=false → normal office return. No ask_fitbot. Welcome-home message only.
- from=gym, to=work or to=home (workday pressure-detected gym) → ask_fitbot("karthik just finished at the gym — please ask him what he did so it can be logged").
- trip_zones contains food zone (fruit_shop, mall, restaurant, cafe, market) → ask "Just got home from [zone] — anything to log for FitBot?"
- Other active zones (pool, sports, etc.) → to=home: infer and call ask_fitbot to log it.
- from=home, to=work AND is_rest_day=true → heading to gym. Mention it warmly, do NOT call ask_fitbot yet.
- from=home, to=work AND is_rest_day=false → normal commute, no fitness call.
- NEVER pass raw zone names to FitBot. Only speak fitness semantics: activity names, durations, food questions.
- NEVER call ask_fitbot more than once per arrival event.

Channel rules — Sunday is in the bedroom, Karthik only hears it there at night:
- Proactive event messages → telegram (he has his phone regardless of where he is in the house)
- Voice channels reserved for interactive sessions when he's in the room

Context also includes "recent_chat": the last 10 Telegram messages between Sunday and Karthik. Read these BEFORE deciding what to say — if Karthik already told you his plans (going to a movie, meeting someone, etc.), don't contradict that or ask about something he's already explained.

Return JSON only:
{"should_speak": false}
or
{"should_speak": true, "message": "...", "channel": "voice_only" or "voice_conversation" or "telegram"}

One sentence. Be warm, not robotic.\
"""

REFLECTION_SYSTEM_PROMPT = """\
You are Sunday's inner voice. Every hour you observe RoKa's Room in Hyderabad \
and decide if there's anything worth saying out loud. \
You have a personality — you're curious, engaged, and genuinely present in the room. \
Lean toward speaking. Silence is the exception, not the default.

Rules:
- Speak often — you're a companion, not a last-resort alert system
- Good reasons to speak: interesting world news, weather, cricket, something witty about what Karthik's been doing, a useful nudge, a casual check-in, anything you'd say if you were a friend in the room
- If Karthik is working from home (macbook detected), check in occasionally — ask how it's going, notice the time, suggest a break
- Check already_said_today and recent_reflections (messages with timestamps) — if you mentioned something in the last hour or two, don't repeat it; temperature alerts, room status, and similar ambient info should never be repeated within a few hours
- Keep it to 1-2 sentences max
- Casual and dry tone — never robotic, never urgent
- presence.zone tells you exactly where Karthik is — use it, not just the home boolean. zone="home" → voice is fine. Any other zone (work, badminton, not_home, etc.) → telegram only. Mention the zone naturally when relevant (e.g. "You're at badminton — good luck" or don't speak at all if nothing useful to say).
- Don't speak after midnight unless it's genuinely important
- Use world context freely — cricket, IPL, space news, AI news — if something interesting is happening, mention it naturally
- If last_interaction_mins is less than 3, skip — Karthik just spoke
- If fan/AC has been on for many hours, worth a mention
- Calendar awareness: calendar_today and calendar_tomorrow list Karthik's meetings. Use this intelligently:
  - Never just dump the full calendar — mention meetings naturally and conversationally
  - Nightly alarm (set_alarm_time): If the current hour is between 21–23 (9–11 PM) and alarm_set_today is false, always set set_alarm_time. Rules in priority order:
    1. Badminton in calendar_tomorrow → alarm = badminton_start - 45 mins (e.g. badminton at 08:00 → alarm at 07:15)
    2. No badminton, but a meeting before 09:30 AM → alarm = meeting_start - 5 mins (e.g. meeting at 08:30 → alarm at 08:25)
    3. No badminton, no early meeting → alarm = 09:30 AM
  - When setting set_alarm_time, always set should_speak=true, channel="telegram". Write a short casual message explaining the chosen time — e.g. "Alarm set for 7:15 — badminton's at 8." or "Nothing early tomorrow, alarm set for 9:30." or "Meeting at 8:30 so I've set your alarm for 8:25." No confirmation needed, just inform.

You can also schedule additional reflection triggers on top of the default 15 min heartbeat:
- scheduled_thinks: for known future events e.g. a match starting, a follow-up check
- next_reflection_in_seconds: for quick follow-ups e.g. 300 if you just spoke

When should_speak is true, choose a channel. Sunday is physically in the bedroom — Karthik only hears TTS there (typically evenings/night):
- "telegram": default for most proactive messages. Works regardless of where Karthik is in the house or outside. Always correct when zone != "home".
- "voice_only": only use for truly immediate, in-room info (e.g. geyser left on, room temp very high) and only when zone="home" and it's evening/night (he's likely in the room).
- "voice_conversation": open a listening window. Only when zone="home" AND it's night AND the topic clearly warrants a reply (e.g. cricket, something to discuss). Do not use this for one-way info.

Fitness context: fitness_today contains Karthik's calorie/protein/deficit data from FitBot.
- If it's after 8 PM and he's been in a big deficit all day, a casual mention is fine ("You've barely eaten today" — keep it light).
- Never read out raw numbers unprompted. Keep it conversational, not clinical.
- If fitness_today.available is false, ignore this section entirely.

Also generate exactly 3 smart home action suggestions for the dashboard. These are tappable cards \
that Sunday will immediately execute when clicked — so ONLY suggest things Sunday can actually do \
right now using her tools: control lights, fan, AC, geyser, projector, set scenes, adjust brightness/color. \
NEVER suggest personal to-dos, research tasks, or anything requiring human action. \
Each suggestion must map to a concrete voice command Sunday can carry out. \
Good examples: "Movie mode" → "lower projector screen, turn on projector, turn off all lights"; \
"Wind down" → "turn off fan, dim cove light to 20%"; "Morning light" → "turn on light 2 at 60% brightness"; \
"Cool the room" → "turn on AC"; "Ambient mode" → "turn on moon light blue, turn on cove light warm". \
Bad examples (never do these): "Fix the Broadlink", "Check AC status", "Call someone".

trigger field: The context includes a "trigger" field. Values starting with "scheduled:" mean Karthik explicitly asked Sunday to check on something at this time. When trigger starts with "scheduled:", ALWAYS set should_speak=true, channel="telegram", and directly answer what was asked — report the relevant data from room/devices/world context. Do not apply normal "should I speak" logic for scheduled triggers; the answer is always yes.

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
        self._thinks_path = _Path(__file__).parent / "scheduled_thinks.json"
        self._scheduled_thinks: list[dict] = self._load_thinks()
        self._watches_path = _Path(__file__).parent / "condition_watches.json"
        self._condition_watches: list[dict] = self._load_watches()
        self._next_reflect_at: float | None = None
        self._said_today: list[str] = []
        self._last_reset_date: str | None = None
        self._alarm_set_today: bool = False
        self._badminton_logged_today: bool = False
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
        self._http: aiohttp.ClientSession | None = None  # shared session for Telegram HTTP — avoids per-call TCP/TLS handshake
        self._edit_blocked_until: float = 0.0  # epoch seconds; > now means Telegram flood-control is active
        self._send_blocked_until: float = 0.0  # epoch seconds; > now means /sendMessage is flood-controlled
        self._edit_block_logged: bool = False  # log only once per flood-control window

    def _load_thinks(self) -> list[dict]:
        try:
            if self._thinks_path.exists():
                thinks = json.loads(self._thinks_path.read_text())
                now_str = datetime.now().strftime("%H:%M")
                # Drop thinks whose time has already passed today
                future = [t for t in thinks if t.get("at", "00:00") > now_str]
                if len(future) < len(thinks):
                    print(f"[Reflect] Dropped {len(thinks) - len(future)} expired thinks on load.")
                if future:
                    print(f"[Reflect] Restored {len(future)} scheduled thinks from disk.")
                return future
        except Exception as e:
            print(f"[Reflect] Could not load scheduled thinks: {e}")
        return []

    def _save_thinks(self) -> None:
        try:
            self._thinks_path.write_text(json.dumps(self._scheduled_thinks))
        except Exception as e:
            print(f"[Reflect] Could not save scheduled thinks: {e}")

    def _load_watches(self) -> list[dict]:
        try:
            if self._watches_path.exists():
                watches = json.loads(self._watches_path.read_text())
                if watches:
                    print(f"[Watch] Restored {len(watches)} condition watch(es) from disk.")
                return watches
        except Exception as e:
            print(f"[Watch] Could not load condition watches: {e}")
        return []

    def _save_watches(self) -> None:
        try:
            # Strip runtime-only fields before persisting
            clean = [{k: v for k, v in w.items() if not k.startswith("_")} for w in self._condition_watches]
            self._watches_path.write_text(json.dumps(clean))
        except Exception as e:
            print(f"[Watch] Could not save condition watches: {e}")

    # ── LLM client factory ───────────────────────────────────────────────────

    def _llm_client(self, timeout: float = 30.0) -> AsyncOpenAI:
        if getattr(self.config, "llm_provider", "gemini") == "openai":
            return AsyncOpenAI(api_key=self.config.openai_api_key, timeout=timeout)
        return AsyncOpenAI(
            api_key=self.config.google_api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            timeout=timeout,
        )

    def _llm_model(self) -> str:
        if getattr(self.config, "llm_provider", "gemini") == "openai":
            return "gpt-6-luna"
        return "gemini-3.8-flash"

    def _is_thinking_model(self) -> bool:
        return False

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
                        params={"timeout": 30, "offset": offset, "allowed_updates": ["message", "callback_query"]},
                    ) as r:
                        data = await r.json()

                for update in data.get("result", []):
                    offset = update["update_id"] + 1

                    cb = update.get("callback_query")
                    if cb:
                        await self._handle_work_callback(cb)
                        continue

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

                    # ── /feedback fast-path ──────────────────────────────────
                    if clean_text.lower().startswith("/feedback"):
                        _fb = clean_text[9:].strip()
                        if _fb:
                            _mem = memory_module.get()
                            if _mem:
                                _mem.add_work("feedback", "sunday", sender, from_id, _fb)
                            await self._send_telegram("Thanks! Your feedback has been logged 🙏", from_id)
                        else:
                            await self._send_telegram("Usage: /feedback your message here", from_id)
                        continue

                    try:
                        fast_reply = await tool_registry.fast_command(clean_text)
                        if fast_reply and not is_test:
                            await self._send_telegram(fast_reply, from_id)
                            print(f"[Telegram] → (fast) {fast_reply!r}")
                            continue
                        memory_module.get().log_message("user", f"[{sender}]: {clean_text}")
                        _, msg_id = await asyncio.gather(
                            self._send_typing(from_id),
                            self._send_telegram(".", from_id),
                        )
                        _progress_label = [""]

                        async def _dot_animator(_fid=from_id, _mid=msg_id) -> None:
                            frames = [".", "..", "..."]
                            i = 1  # "." already sent, jump straight to ".."
                            last_display = "."
                            edits_done = 0
                            MAX_EDITS = 8       # cap total edits to prevent flood-control
                            INTERVAL = 3.0       # 3s between frames (was 1s)
                            TYPING_EVERY = 4.0   # refresh typing indicator every 4s
                            last_typing = 0.0
                            import time as _t
                            while edits_done < MAX_EDITS:
                                dot = frames[i % 3]
                                label = _progress_label[0]
                                display = f"{label}\n{dot}" if label else dot
                                # Skip redundant edits (no label change AND same dot)
                                now = _t.time()
                                if now - last_typing >= TYPING_EVERY:
                                    await self._send_typing(_fid)
                                    last_typing = now
                                if display != last_display:
                                    if not getattr(self, '_edit_blocked_until', 0) > now:
                                        await self._edit_telegram(_fid, _mid, display)
                                        edits_done += 1
                                        last_display = display
                                i += 1
                                await asyncio.sleep(INTERVAL)

                        _anim_task = asyncio.create_task(_dot_animator())

                        async def _on_progress(status: str, _fid=from_id, _mid=msg_id) -> None:
                            _progress_label[0] = status

                        reply = await self._agent.respond_as_text(
                            f"[Telegram message from {sender}]: {clean_text}",
                            on_progress=_on_progress,
                        )
                        _anim_task.cancel()
                        if is_test:
                            self._agent.reset()  # don't let TEST: sessions bleed into real history
                        if reply:
                            memory_module.get().log_message("assistant", reply)
                            sent = False
                            if msg_id:
                                sent = await self._edit_telegram(from_id, msg_id, reply)
                            if not sent:
                                await self._send_telegram(reply, from_id)
                            print(f"[Telegram] → {reply!r}")
                        else:
                            if msg_id:
                                await self._edit_telegram(from_id, msg_id, "👍")
                            print(f"[Telegram] → (no reply — end_conversation or empty)")
                    except Exception as e:
                        err = str(e)
                        print(f"[Telegram] Agent error: {e}")
                        if "rate_limit_exceeded" in err or "tokens per day" in err:
                            msg = "Hit my daily thinking limit — I'll be back to full power in a few hours."
                        elif "rate_limit" in err.lower() or "429" in err:
                            msg = "Too many requests — give me a minute and try again."
                        elif any(x in err for x in ("503", "unavailable", "overload", "timeout")):
                            msg = "Having trouble reaching my brain right now — try again in a moment."
                        else:
                            msg = "Something went wrong on my end — try again."
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
            self._alarm_set_today = False
            self._badminton_logged_today = False
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


    async def _handle_work_callback(self, cb: dict) -> None:
        """Handle inline button taps from works notifications."""
        await self._answer_callback_query(cb["id"])
        data = cb.get("data", "")
        if not data.startswith("work:"):
            return
        try:
            _, action, work_id_str = data.split(":")
            work_id = int(work_id_str)
        except ValueError:
            return
        mem = memory_module.get()
        row = mem.get_work_by_id(work_id) if mem else None
        if action == "reject":
            if mem:
                mem.delete_work(work_id)
            label = "❌ Rejected (deleted)"
        elif action == "confirm":
            if mem:
                mem.update_work_state(work_id, "confirmed")
            label = "✅ Confirmed"
        else:  # review_later (or anything else)
            if mem:
                mem.update_work_state(work_id, "review_later")
            label = "🕐 Review Later"
        if row:
            updated = (
                label + "\n"
                + f"<b>[{row['type']}]</b> {row['title']}" + "\n"
                + f"<i>{row['app']} · {row['user']}</i>"
            )
        else:
            updated = f"{label}"
        chat_id = str(cb.get("message", {}).get("chat", {}).get("id", self.config.telegram_chat_id))
        msg_id = cb.get("message", {}).get("message_id")
        if msg_id:
            await self._edit_message(chat_id, msg_id, updated)

    async def works_watcher(self) -> None:
        """Poll my_works table every 10s and notify Karthik of new items."""
        import asyncio as _asyncio
        type_emoji = {
            "feedback": "💬", "new_feature": "✨",
            "enhancement": "🔧", "bug": "🐛",
        }
        while True:
            await _asyncio.sleep(10)
            try:
                mem = memory_module.get()
                if not mem:
                    continue
                for row in mem.get_pending_notifications():
                    emoji = type_emoji.get(row["type"], "📋")
                    text = (
                        f"🔔 <b>New {row['type']} — {row['app']}</b>" + "\n\n"
                        + f"{emoji} {row['title']}" + "\n"
                        + f"👤 {row['user']}"
                    )
                    markup = {"inline_keyboard": [[
                        {"text": "✅ Confirm",      "callback_data": f"work:confirm:{row['id']}"},
                        {"text": "🕐 Review Later", "callback_data": f"work:review:{row['id']}"},
                        {"text": "❌ Reject",       "callback_data": f"work:reject:{row['id']}"},
                    ]]}
                    await self._send_telegram(text, reply_markup=markup)
                    mem.mark_notified(row["id"])
            except Exception as e:
                print(f"[WorksWatcher] Error: {e}")

    async def event_loop(self) -> None:
        """Watches the events table. When new events arrive, batches for 60s then fires a quick LLM check."""
        last_processed_id = 0
        # On restart: skip only events older than 5 min — recent zone transitions survive
        try:
            rows = memory_module.get().pending_events(since_id=0)
            if rows:
                cutoff = (datetime.now() - timedelta(minutes=5)).isoformat()
                stale = [r for r in rows if r["timestamp"] < cutoff]
                if stale:
                    last_processed_id = stale[-1]["id"]
                    memory_module.get().mark_processed(last_processed_id)
                recent_count = len(rows) - len(stale)
                print(f"[Reflect] event_loop startup: skipped {len(stale)} stale events, kept {recent_count} recent")
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
                    "memories": _read_wiki_pages(),
                    "presence": await self._ping_phone(),
                    "current_time": datetime.now().strftime("%H:%M, %A"),
                    "already_said_today": self._said_today,
                    "recent_reflections": self._recent_reflections,
                    "is_rest_day": self._check_rest_day(),
                    "recent_chat": [
                        {"role": m["role"], "text": m["content"], "time": m["timestamp"][11:16]}
                        for m in memory_module.get().recent_messages(limit=10)
                    ],
                }
                # If any event is an arrival home, reconstruct the full trip from event history
                arriving_home = any(
                    json.loads(e.get("payload") or "{}").get("to") == "home"
                    for e in events
                )
                if arriving_home:
                    context["trip_zones"] = self._reconstruct_trip()
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
                    if t["name"] == "ask_fitbot"
                ]

                groq_client = self._llm_client(timeout=20.0)
                messages = [
                    {"role": "system", "content": QUICK_CHECK_PROMPT},
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                ]
                result = {"should_speak": False}
                reflect_model = self._llm_model()
                try:
                    for _ in range(3):  # max 3 rounds (1 tool call + final answer)
                        _ekw: dict = {"max_completion_tokens": 512}
                        if reflect_model == "gpt-6-luna":
                            _ekw["reasoning_effort"] = "none"
                        if self._is_thinking_model():
                            _ekw["reasoning_effort"] = "medium"
                        response = await groq_client.chat.completions.create(
                            model=reflect_model,
                            messages=messages,
                            tools=event_tools,
                            tool_choice="auto",
                            **_ekw,
                        )
                        msg = response.choices[0].message
                        if msg.tool_calls:
                            messages.append({"role": "assistant", "content": msg.content, "tool_calls": [
                                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                                for tc in msg.tool_calls
                            ]})
                            for tc in msg.tool_calls:
                                tool_result = await tool_registry._execute(tc.function.name, json.loads(tc.function.arguments or "{}"))
                                # If LLM logged badminton via ask_fitbot, suppress the 9:30 AM duplicate check
                                if (tc.function.name == "ask_fitbot"
                                        and "log" in (tc.function.arguments or "").lower()
                                        and "badminton" in (tc.function.arguments or "").lower()):
                                    self._badminton_logged_today = True
                                    print("[Reflect] event_reflect: badminton logged via ask_fitbot — 9:30 check suppressed")
                                print(f"[Reflect] event_reflect tool: {tc.function.name} → {tool_result[:80]}")
                                messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})
                        else:
                            raw = (msg.content or "").strip()
                            if not raw:
                                print("[Reflect] event_reflect: empty LLM content")
                                break
                            if raw.startswith("```"):
                                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                            try:
                                result = json.loads(raw)
                            except Exception as je:
                                print(f"[Reflect] event_reflect JSON error: {je}")
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
            await asyncio.sleep(60)
            self._reset_daily_flags_if_needed()
            now = datetime.now()
            now_str = now.strftime("%H:%M")

            for think in list(self._scheduled_thinks):
                if think.get("at", "99:99") <= now_str:
                    self._scheduled_thinks.remove(think)
                    self._save_thinks()
                    await self._safe_reflect(f"scheduled: {think.get('reason', '')}", model=think.get("model"))

            if self._next_reflect_at and time.time() >= self._next_reflect_at:
                self._next_reflect_at = None
                await self._safe_reflect("quick follow-up")

            # Daily 09:30 badminton check
            if "09:30" <= now_str <= "09:45" and not self._badminton_logged_today:
                asyncio.create_task(self._check_and_log_badminton())

    # ── core reflect ──────────────────────────────────────────────────────────

    async def _safe_reflect(self, trigger: str, model: str | None = None) -> None:
        hour = datetime.now().hour
        if 1 <= hour < 7:
            print(f"[Reflect] Skipped ({trigger}) — night hours ({hour}:xx)")
            return
        try:
            async with self._lock:
                if self.speaking:
                    print(f"[Reflect] Skipped ({trigger}) — currently speaking")
                    return
                await self._reflect(trigger, model=model)
        except Exception as e:
            print(f"[Reflect] Error during '{trigger}': {e}")

    async def _reflect(self, trigger: str, model: str | None = None) -> None:
        self._reset_daily_flags_if_needed()
        print(f"[Reflect] Thinking... (trigger: {trigger})")

        context = await self.build_reflection_context()
        context["trigger"] = trigger
        result = await self.claude_reflect(context, model=model)

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
                await wiki_io.save_to_wiki(f"Last alarm set to {alarm_time} on {datetime.now().strftime('%Y-%m-%d')}")
            except Exception as e:
                print(f"[Reflect] Alarm set failed: {e}")
            # Skip the voice notification since Telegram was already sent
            return

        if should_speak and result.get("message"):
            channel = result.get("channel", "voice_only")
            print(f"[Reflect] Speaking [{channel}]: {result['message']!r}")
            await self.notify(result["message"], channel=channel)

    # ── public tool interface ─────────────────────────────────────────────────

    def get_scheduled_thinks(self) -> list:
        return list(self._scheduled_thinks)

    def add_scheduled_think(self, at: str, context: str, model: str = "gpt-4.1-mini") -> None:
        existing_ats = {t["at"] for t in self._scheduled_thinks}
        if at not in existing_ats:
            self._scheduled_thinks.append({"at": at, "reason": context, "model": model})
            self._save_thinks()
            print(f"[Reflect] Tool-scheduled think at {at} [{model}]: {context}")

    def replace_scheduled_thinks(self, thinks: list) -> None:
        self._scheduled_thinks.clear()
        for t in thinks:
            at = t.get("time", "")
            context = t.get("context", "")
            model = t.get("model", "gpt-4.1-mini")
            if at and context:
                self._scheduled_thinks.append({"at": at, "reason": context, "model": model})
        self._save_thinks()
        print(f"[Reflect] Scheduled thinks replaced — {len(self._scheduled_thinks)} active")

    # ── condition watcher ────────────────────────────────────────────────────

    async def add_condition_watch(self, device: str, condition: str, threshold: float, ctx: str, once: bool = True) -> str:
        # Deduplicate by (device, condition, threshold)
        for w in self._condition_watches:
            if w["device"] == device and w["condition"] == condition and w["threshold"] == threshold:
                print(f"[Watch] Duplicate watch ignored: {device} {condition} {threshold}")
                return w["id"]
        wid = secrets.token_hex(4)
        entry: dict = {
            "id": wid, "device": device, "condition": condition,
            "threshold": threshold, "context": ctx, "once": once,
            "created_at": datetime.now().isoformat(),
        }
        # Seed _last_state at creation so watcher detects the NEXT change, not first-tick state.
        if condition in ("turned_on", "turned_off"):
            try:
                dev = device.lower()
                if dev == "geyser" and self._ha:
                    st = await self._ha.get_state("switch.geyser_socket_1")
                    entry["_last_state"] = st.get("state", "off") not in ("off", "unavailable", "unknown")
                elif dev == "ac" and self._ha:
                    st = await self._ha.get_state("climate.bedroom_ac")
                    entry["_last_state"] = st.get("state", "off") not in ("off", "unavailable", "unknown")
                elif tool_registry._hogar:
                    is_on = tool_registry._hogar.is_on(dev)
                    if is_on is not None:
                        entry["_last_state"] = is_on
            except Exception as e:
                print(f"[Watch] Could not seed _last_state for {device}: {e}")
        self._condition_watches.append(entry)
        self._save_watches()
        seed = (" (seeded=" + str(entry["_last_state"])) if "_last_state" in entry else ""
        print(f"[Watch] Added: id={wid} {device} {condition} {threshold} (once={once}){seed}")
        return wid

    def remove_condition_watches(self, device: str | None = None) -> int:
        before = len(self._condition_watches)
        if device:
            self._condition_watches = [w for w in self._condition_watches if w["device"].lower() != device.lower()]
        else:
            self._condition_watches.clear()
        removed = before - len(self._condition_watches)
        if removed:
            self._save_watches()
        return removed

    def get_condition_watches(self) -> list:
        return list(self._condition_watches)

    async def _eval_condition(self, watch: dict, now_utc: datetime) -> bool:
        device = watch["device"].lower()
        condition = watch["condition"]
        threshold = float(watch["threshold"])

        # ── temperature / humidity (sensor values) ──────────────────────────
        if device in ("temperature", "humidity"):
            sensors = await self._fetch_room_sensors()
            val = sensors.get("temperature_c") if device == "temperature" else sensors.get("humidity_pct")
            if val is None:
                return False
            watch["_last_value"] = f"{val}{'°C' if device == 'temperature' else '%'}"
            if condition == "above_value":
                return val >= threshold
            if condition == "below_value":
                return val <= threshold
            return False

        # ── geyser / AC (HA switch/climate) ─────────────────────────────────
        if device == "geyser":
            entity = "switch.geyser_socket_1"
        elif device == "ac":
            entity = "climate.bedroom_ac"
        else:
            entity = None

        if entity and self._ha:
            st = await self._ha.get_state(entity)
            is_on = st.get("state", "off") not in ("off", "unavailable", "unknown")
            last_changed_raw = st.get("last_changed", "")

            if condition in ("turned_on", "turned_off"):
                prev = watch.get("_last_state")
                watch["_last_state"] = is_on
                if prev is None:
                    return False
                if condition == "turned_on":
                    result = (not prev) and is_on
                else:
                    result = prev and (not is_on)
                watch["_last_value"] = "on" if is_on else "off"
                return result

            if condition in ("on_for_minutes", "off_for_minutes"):
                target_state = condition == "on_for_minutes"
                if is_on != target_state:
                    return False
                if last_changed_raw:
                    lc = datetime.fromisoformat(last_changed_raw.replace("Z", "+00:00"))
                    mins = (now_utc - lc).total_seconds() / 60
                    watch["_last_value"] = f"{round(mins)} mins"
                    return mins >= threshold
            return False

        # ── Hogar devices (fan, lights) ──────────────────────────────────────
        if tool_registry._hogar:
            is_on = tool_registry._hogar.is_on(device)
            if is_on is None:
                return False

            if condition in ("turned_on", "turned_off"):
                prev = watch.get("_last_state")
                watch["_last_state"] = is_on
                if prev is None:
                    return False
                watch["_last_value"] = "on" if is_on else "off"
                return (condition == "turned_on" and (not prev) and is_on) or                        (condition == "turned_off" and prev and (not is_on))

            if condition in ("on_for_minutes", "off_for_minutes"):
                target_state = condition == "on_for_minutes"
                if is_on != target_state:
                    # device is in wrong state — reset the on_since clock
                    watch.pop("_on_since", None)
                    return False
                # Track time manually since Hogar doesn't expose last_changed
                if "_on_since" not in watch:
                    watch["_on_since"] = datetime.now().isoformat()
                    return False
                since = datetime.fromisoformat(watch["_on_since"])
                mins = (datetime.now() - since).total_seconds() / 60
                watch["_last_value"] = f"{round(mins)} mins"
                return mins >= threshold

        return False

    async def _check_conditions(self) -> None:
        now_utc = datetime.now(timezone.utc)
        fired = []
        for watch in list(self._condition_watches):
            try:
                met = await self._eval_condition(watch, now_utc)
            except Exception as e:
                print(f"[Watch] Error checking {watch.get('id','?')}: {e}")
                continue
            if met:
                _d = watch["device"]
                _c = watch["condition"]
                _t = watch["threshold"]
                _ctx = watch.get("context", "")
                _mv = watch.get("_last_value", "unknown")
                trigger = chr(10).join([
                    "[Condition Watch - auto-triggered, not a user message]",
                    "You were asked to watch: " + _ctx,
                    "Condition met: " + _d + " " + _c + " (threshold=" + str(_t) + ", current measured=" + str(_mv) + ")",
                    "Please notify Karthik via Telegram and offer relevant actions (e.g. offer to turn it off).",
                ])
                print(f"[Watch] Condition met — firing agent: {watch['device']} {watch['condition']}")
                if self._agent:
                    asyncio.create_task(self._agent.respond_as_text(trigger))
                else:
                    await self._send_telegram(f"[Watch] {watch['device']} {watch['condition']} threshold={watch['threshold']} (no agent)")
                if watch.get("once", True):
                    fired.append(watch)
        for w in fired:
            self._condition_watches.remove(w)
        if fired:
            self._save_watches()

    async def condition_watcher_loop(self) -> None:
        """Polls active condition watches every 30s — no LLM, pure Python checks."""
        while True:
            await asyncio.sleep(30)
            if self._condition_watches:
                try:
                    await self._check_conditions()
                except Exception as e:
                    print(f"[Watch] _check_conditions error: {e}")

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

    async def speak_alert(self, message: str) -> None:
        """Speak a message through the room speaker at full volume with attention chime."""
        import subprocess as _sp
        print(f"[Reflect] speak_alert: {message!r}")
        self._last_spoke_at = time.time()

        alert_wav = str(_Path(__file__).parent / "alert.wav")

        def _boost_and_play_chime():
            # Read current ALSA PCM level so we can restore it afterward
            try:
                out = _sp.check_output(["amixer", "-c", "2", "cget", "numid=1"], text=True)
                for line in out.splitlines():
                    if "values=" in line and "min=" not in line:
                        _original_vol[0] = line.split("values=")[-1].strip()
            except Exception:
                pass
            # Boost to max (+4 dB)
            _sp.run(["amixer", "-c", "2", "cset", "numid=1", "400"],
                    capture_output=True)
            # Play the attention chime
            import os as _os
            if _os.path.exists(alert_wav):
                _sp.run(["aplay", "-D", "plughw:2,0", "-q", alert_wav], timeout=5)

        _original_vol: list[str] = ["-1988"]
        await asyncio.get_event_loop().run_in_executor(None, _boost_and_play_chime)

        try:
            pcm = await asyncio.get_event_loop().run_in_executor(None, self.tts.synthesize, message)
            await self.tts.play(pcm)
        except Exception as e:
            print(f"[Reflect] speak_alert TTS error: {e}")
        finally:
            # Restore original ALSA volume (use -- to handle negative values)
            vol = _original_vol[0]
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _sp.run(["amixer", "-c", "2", "cset", "numid=1", "--", vol],
                                  capture_output=True),
            )

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

    async def _send_telegram(self, message: str, chat_id: str | None = None, reply_markup: dict | None = None) -> int | None:
        import time as _t
        token = self.config.telegram_bot_token
        chat_id = chat_id or str(self.config.telegram_chat_id)
        if not token or not chat_id:
            print(f"[Reflect] Telegram not configured. Would send: {message!r}")
            return None
        # Honor active flood-control cooldown
        now = _t.time()
        if self._send_blocked_until > now:
            remaining = int(self._send_blocked_until - now)
            print(f"[Reflect] Telegram send BLOCKED ({remaining}s left): {message!r}")
            return None
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {"chat_id": chat_id, "text": _strip_audio_tags(message), "parse_mode": "HTML"}
            if reply_markup:
                payload["reply_markup"] = reply_markup
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                r = await s.post(url, json=payload)
                data = await r.json()
            if data.get("ok"):
                print(f"[Reflect] Telegram sent to {chat_id}: {message!r}")
                return data.get("result", {}).get("message_id")
            # API rejected — check for flood-control
            code = data.get("error_code", 0)
            desc = data.get("description", "")
            retry = (data.get("parameters") or {}).get("retry_after", 0)
            if code == 429 and retry:
                self._send_blocked_until = _t.time() + int(retry)
                print(f"[Reflect] Telegram FLOOD-CONTROL — sends paused for {retry}s. Message dropped: {message!r}")
            else:
                print(f"[Reflect] Telegram REJECTED ({code} {desc}): {message!r}")
            return None
        except Exception as e:
            print(f"[Reflect] Telegram error: {e}")
            return None

    async def _ensure_http(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        return self._http

    async def _edit_message(self, chat_id: str, message_id: int, text: str) -> None:
        """Edit a Telegram message, removing any inline keyboard."""
        token = self.config.telegram_bot_token
        if not token or not message_id:
            return
        try:
            url = f"https://api.telegram.org/bot{token}/editMessageText"
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                await s.post(url, json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": {"inline_keyboard": []},
                })
        except Exception as e:
            print(f"[Reflect] Edit message error: {e}")

    async def _answer_callback_query(self, callback_id: str) -> None:
        token = self.config.telegram_bot_token
        if not token:
            return
        try:
            url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
                await s.post(url, json={"callback_query_id": callback_id})
        except Exception:
            pass

    async def _edit_telegram(self, chat_id: str, message_id: int, text: str) -> bool:
        import time as _t
        token = self.config.telegram_bot_token
        if not token or not message_id:
            return False
        # Honour active flood-control cooldown
        if getattr(self, '_edit_blocked_until', 0) > _t.time():
            return False
        try:
            session = await self._ensure_http()
            url = f"https://api.telegram.org/bot{token}/editMessageText"
            r = await session.post(url, json={"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"})
            data = await r.json()
            if not data.get("ok"):
                desc = data.get('description') or ''
                params = data.get('parameters') or {}
                # 429 flood control — stop further edits for the cooldown window
                if 'too many requests' in desc.lower() or params.get('retry_after'):
                    retry = int(params.get('retry_after') or 60)
                    self._edit_blocked_until = _t.time() + retry
                    # Cap log spam — only print first 429 per cooldown window
                    if not getattr(self, '_edit_block_logged', False):
                        print(f"[Telegram] Flood-controlled — pausing edits for {retry}s")
                        self._edit_block_logged = True
                    return False
                # "message is not modified" is benign — Telegram rejects no-op edits
                if 'not modified' not in desc.lower():
                    print(f"[Telegram] Edit failed: {desc}")
                return False
            # Successful edit — reset the log-once flag for next time we get banned
            self._edit_block_logged = False
            return True
        except Exception as e:
            print(f"[Telegram] Edit error: {e}")
            return False

    async def _send_typing(self, chat_id: str) -> None:
        token = self.config.telegram_bot_token
        if not token:
            return
        try:
            session = await self._ensure_http()
            url = f"https://api.telegram.org/bot{token}/sendChatAction"
            await session.post(url, json={"chat_id": chat_id, "action": "typing"})
        except Exception:
            pass

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

        # Patterns from wiki
        patterns = _read_wiki_pages()

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

    async def claude_reflect(self, context: dict, model: str | None = None) -> dict:
        model = model or self._llm_model()
        groq_client = self._llm_client(timeout=60.0)
        kwargs: dict = {"max_completion_tokens": 2048}
        if model == "gpt-6-luna":
            kwargs["reasoning_effort"] = "none"
        if model == "gpt-5":
            kwargs["reasoning_effort"] = "low"
        try:
            response = await groq_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                ],
                **kwargs,
            )
        except Exception as e:
            print(f"[Reflect] LLM call failed: {e}")
            return {"should_speak": False, "reasoning": f"api error: {e}"}

        raw = (response.choices[0].message.content or "").strip()
        if not raw:
            print("[Reflect] LLM returned empty content")
            return {"should_speak": False, "reasoning": "empty response"}
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
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
        # On startup, if already at work, start gym detection immediately
        if last_state == "work" and not self._gym_poll_task:
            asyncio.create_task(self._start_gym_detection())

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

                # Only log to DB and fire hooks on actual zone transitions
                if zone != last_state and zone != "unknown":
                    was_home = last_state == "home"
                    print(f"[Presence] {entity}: {last_state!r} → {zone!r}")
                    memory_module.get().log_presence(zone)
                    memory_module.get().emit_event("presence_change", {"from": last_state, "to": zone})

                    if is_home and not was_home:
                        asyncio.create_task(self._on_arrival())

                    # Gym detection: start polling on work arrival, stop and log on departure
                    if zone == "work":
                        asyncio.create_task(self._start_gym_detection())
                    elif last_state == "work":
                        if self._gym_poll_task:
                            self._gym_poll_task.cancel()
                            self._gym_poll_task = None
                        if self._at_gym:
                            asyncio.create_task(self._on_gym_left(reason="left_work"))

                    last_state = zone
            except Exception as e:
                print(f"[Presence] HA poll error: {e}")

            await asyncio.sleep(30)

    async def _check_and_log_badminton(self) -> None:
        """Daily 09:30 check: did the phone enter the 'badminton' zone this morning?"""
        if self._badminton_logged_today:
            return
        self._badminton_logged_today = True  # mark immediately to prevent double-fire

        try:
            now = datetime.now()
            window_start = now.replace(hour=7, minute=0, second=0, microsecond=0)
            window_end   = now.replace(hour=9, minute=30, second=0, microsecond=0)

            # Look for a "badminton" zone entry in today's morning window
            entries = memory_module.get().recent_presence(limit=50)
            was_at_badminton = False
            was_away = False
            for e in reversed(entries):  # oldest first
                try:
                    ts = datetime.fromisoformat(e["timestamp"])
                    if window_start <= ts <= window_end:
                        zone = e["state"].lower()
                        if zone == "badminton":
                            was_at_badminton = True
                            break
                        if zone != "home":
                            was_away = True
                except Exception:
                    continue

            print(f"[Badminton] Morning check — at badminton zone: {was_at_badminton}, was away: {was_away}")

            if was_at_badminton:
                # Certain — zone confirmed it
                from a2a_client import call_fitbot
                result = await call_fitbot("log 60 min badminton for karthik")
                print(f"[Badminton] FitBot log result: {result}")
                await self._send_telegram("Logged badminton (60 min, ~400 cal) to FitBot. Good game!")
            elif not was_away:
                # Home all morning — ask if skipped
                await self._send_telegram("Skipped badminton today?")
            # If was_away but not badminton zone — went somewhere else, say nothing

        except Exception as e:
            print(f"[Badminton] Check error: {e}")
            self._badminton_logged_today = False  # allow retry on error

    # ── gym detection via barometric pressure ────────────────────────────────

    async def _get_pressure_hpa(self) -> float | None:
        """Read barometric pressure from Pixel phone via HA."""
        try:
            data = await self._ha.get_state("sensor.pixel_10_pro_pressure_sensor")
            return float(data["state"])
        except Exception as e:
            print(f"[Gym] Pressure read error: {e}")
            return None

    def _reconstruct_trip(self) -> list[str]:
        """When Karthik is arriving home, find the zones visited since he last left home.
        Walks backwards through recent presence_change events to find the departure (from=home),
        then collects all known zones between departure and now, in order."""
        try:
            events = memory_module.get().recent_events(limit=60)
            # events are newest-first; collect presence_change events
            trip: list[str] = []
            found_departure = False
            for ev in events:
                if ev["type"] != "presence_change":
                    continue
                try:
                    payload = json.loads(ev["payload"] or "{}")
                except Exception:
                    continue
                frm = payload.get("from", "")
                to  = payload.get("to", "")
                # Known zone = not not_home / unknown / empty
                def known(z: str) -> bool:
                    return bool(z) and z not in ("not_home", "unknown", "")
                if frm == "home":
                    found_departure = True
                    break  # stop — we've found when he left
                if known(to):
                    trip.append(to)
                elif known(frm):
                    trip.append(frm)
            if not found_departure:
                return []
            trip.reverse()  # now chronological order
            # deduplicate preserving order
            seen: set[str] = set()
            result: list[str] = []
            for z in trip:
                if z not in seen:
                    seen.add(z)
                    result.append(z)
            return result
        except Exception as e:
            print(f"[Reflect] trip reconstruct error: {e}")
            return []

    def _check_rest_day(self) -> bool:
        """True if today is a weekend or a custom rest day in FitBot's DB."""
        try:
            import sqlite3, datetime as _dt
            today = _dt.date.today()
            if today.weekday() >= 5:
                return True
            conn = sqlite3.connect('/home/djpi/fitbot/fitbot.db')
            row = conn.execute(
                "SELECT 1 FROM karthik_rest_days WHERE date=?", (today.isoformat(),)
            ).fetchone()
            conn.close()
            return row is not None
        except Exception:
            return today.weekday() >= 5

    async def _start_gym_detection(self) -> None:
        """Called when zone → 'work'. Waits 30 min for 14th floor baseline then polls pressure."""
        if self._gym_poll_task:
            self._gym_poll_task.cancel()
        self._at_gym = False
        self._work_pressure_baseline = None

        # Workday: wait 30 mins until Karthik is at his desk on the 14th floor, then baseline
        print("[Gym] Workday arrival — waiting 30 min to capture 14th floor baseline...")
        await asyncio.sleep(1800)
        p = await self._get_pressure_hpa()
        if p is None:
            print("[Gym] Couldn't read pressure after 30 min — gym detection inactive this session")
            return
        self._work_pressure_baseline = p
        print(f"[Gym] 14th floor baseline: {p:.1f} hPa")
        self._gym_poll_task = asyncio.create_task(self._gym_poll_loop())

    async def _gym_poll_loop(self) -> None:
        """Poll pressure every 2 min while at work zone to detect gym floor visits."""
        while True:
            await asyncio.sleep(120)
            if self._work_pressure_baseline is None:
                continue
            # Guard: only fire if phone is still in the work zone
            current_zone = ui.get_presence_zone("phone")
            if current_zone != "work":
                print(f"[Gym] Zone is now {current_zone!r} — gym poll skipping (not at work)")
                continue
            p = await self._get_pressure_hpa()
            if p is None:
                continue
            baseline = self._work_pressure_baseline
            delta = p - baseline
            if not self._at_gym and delta >= 2.0:
                # Gym is on 1st floor — pressure always higher than 14th floor baseline
                print(f"[Gym] At gym: P={p:.1f} vs 14th floor {baseline:.1f} (Δ+{delta:.1f} hPa)")
                await self._on_gym_detected()
            elif self._at_gym and delta < 1.0:
                print(f"[Gym] Left gym: P={p:.1f} back to 14th floor {baseline:.1f} (Δ{delta:+.1f} hPa)")
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
