import asyncio
import colorsys
import os
import json as _json
import subprocess
import urllib.request
from datetime import datetime
from typing import TYPE_CHECKING

import memory
import wiki_io

if TYPE_CHECKING:
    from home import HAClient

DEFINITIONS = [
    {
        "name": "control_device",
        "description": (
            "Turn a device on or off, and optionally set brightness, color, or color temperature. "
            "Works for: TV (HA), dining room speaker, Sian's room speaker, projector, "
            "fan, light 1, light 2, spots, foot lamp, cove, geyser, humidifier, top light, panels, moon, dashboard. "
            "For AC, use ac_control instead — it has dedicated temperature and fan-speed parameters. "
            "Top light, panels, moon, and dashboard also support color (hue 0-360, saturation 0-100), hex color, and color_temp (2700-6500K). "
            "Use action 'on' or 'off'. Optionally set volume 0-100 for speakers, "
            "brightness 0-100 for lights, or speed 1-3 for the ceiling fan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device": {
                    "type": "string",
                    "description": "Device name, e.g. 'TV', 'fan', 'light 1', 'cove', 'AC', 'top light', 'panels'",
                },
                "action": {"type": "string", "enum": ["on", "off"]},
                "volume": {
                    "type": "integer",
                    "description": "Volume 0-100 (speakers/TV only, optional)",
                },
                "brightness": {
                    "type": "integer",
                    "description": "Brightness 0-100 (lights only, optional)",
                },
                "speed": {
                    "type": "integer",
                    "description": "Fan speed 1=low, 2=medium, 3=high (fan only, optional)",
                },
                "hue": {
                    "type": "integer",
                    "description": "Color hue 0-360 (top light and panels only). 0=red, 60=yellow, 120=green, 180=cyan, 240=blue, 300=purple",
                },
                "saturation": {
                    "type": "integer",
                    "description": "Color saturation 0-100 (top light and panels only). 0=white, 100=full color",
                },
                "color_temp": {
                    "type": "integer",
                    "description": "Color temperature in Kelvin 2700-6500 (top light and panels only). 2700=warm white, 4000=neutral, 6500=cool white",
                },
                "color": {
                    "type": "string",
                    "description": "Hex color code e.g. '#FF0000' for red, '#00FFFF' for cyan (top light and panels only). Use this instead of hue/saturation when a specific color is requested.",
                },
            },
            "required": ["device", "action"],
        },
    },
    {
        "name": "ac_control",
        "description": (
            "Control the bedroom AC (LG split). One call can change any combination of: "
            "on/off (action), HVAC mode (cool/heat/dry/fan_only), target temperature (18-30°C), and fan speed (low/mid/high/auto). "
            "Examples: turn on at 24 with fan low; switch to dry mode; just change temperature while running; "
            "fan_only mode for circulation; turn off. At least one parameter must be set. "
            "action='on' defaults to cool mode unless mode is also specified."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["on", "off"],
                    "description": "Turn AC on (cool mode) or off. Omit if just adjusting temperature/fan on a running AC.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["cool", "heat", "dry", "fan_only"],
                    "description": "HVAC mode. 'cool' is normal (default for action='on'); 'dry' is dehumidify (useful in monsoon); 'fan_only' is just circulation, no compressor; 'heat' is heating. Optional. Use action='off' to turn off — don't pass mode='off'.",
                },
                "temperature": {
                    "type": "integer",
                    "description": "Target temperature in Celsius (18-30). Mostly relevant for cool/heat modes. Optional.",
                },
                "fan_mode": {
                    "type": "string",
                    "enum": ["low", "mid", "high", "auto"],
                    "description": "Fan speed. 'low' is quiet, 'auto' lets AC pick. Optional.",
                },
            },
        },
    },
    {
        "name": "get_devices",
        "description": "List all controllable devices and their current state.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_home_info",
        "description": "Get home sensor readings: temperature, humidity, internet speed.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_sensor_history",
        "description": "Get historical readings for temperature or humidity sensor over the last N hours. Use this when asked about history, trends, last night's readings, averages, or how conditions changed over time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sensor": {
                    "type": "string",
                    "enum": ["temperature", "humidity"],
                    "description": "Which sensor to query",
                },
                "hours": {
                    "type": "integer",
                    "description": "How many hours of history to fetch (default 12, max 48)",
                },
            },
            "required": ["sensor"],
        },
    },
    {
        "name": "get_time",
        "description": "Get the current date and time.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_timer",
        "description": "Set a countdown timer that plays a sound when done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "integer", "description": "Duration in seconds"},
            },
            "required": ["seconds"],
        },
    },
    {
        "name": "get_memories",
        "description": "Retrieve insights and patterns learned about Karthik over time. Use this when asked what you've learned, what patterns you've noticed, or anything about past behaviour.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "save_memory",
        "description": (
            "Save something you just learned about Karthik, his preferences, or his home setup. "
            "REQUIRED whenever Karthik: corrects a mistake you made, says 'no'/'wrong'/'not that', "
            "tells you a better way to do something, or reveals a preference you didn't know. "
            "Write the insight as a clear rule for next time — e.g. 'When user says X, do Y not Z'. "
            "Also call for new preferences: 'I prefer Y', 'that device works via Z'. Concise and factual."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "insight": {
                    "type": "string",
                    "description": "The thing you learned, written as a short factual statement.",
                },
            },
            "required": ["insight"],
        },
    },
    {
        "name": "get_presence",
        "description": "Check which of Karthik's devices are on the home network right now. Tells you if he's home (phone) and if he's working from home (macbook).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_ssh_address",
        "description": "Get the current ngrok SSH address for this Pi. Use when asked for the SSH address, remote access address, ngrok address, or how to connect to the Pi remotely.",
        "input_schema": {"type": "object", "properties": {}},
    },
                    {
        "name": "send_google_assistant_command",
        "description": (
            "Send a voice command to Google Assistant via Home Assistant. "
            "Use for: Fan, all lights together (e.g. 'turn off all lights in RoKa\\'s room'), "
            "Light 1, Light 2, Cove Light, Spots, Foot lamp. NOT for projector (use control_device)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Natural language command, e.g. 'turn on projector in RoKa\\'s room'",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "set_alarm",
        "description": (
            "Set a morning wake-up alarm via Google Assistant. "
            "Use when Karthik asks to set an alarm, or when proactively setting his morning alarm. "
            "IMPORTANT: Do NOT speak or write ANY confirmation text before, during, or after calling this tool. "
            "The tool plays its own audio (it speaks 'Hey Google, set an alarm for...') through the speaker, so any "
            "reply text would overlap with that announcement. Just call the tool and stay completely silent. "
            "For voice turns: emit only the tool call, no surrounding text. For Telegram: emit nothing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "time": {
                    "type": "string",
                    "description": "Alarm time, e.g. '7:00 AM', '7:30 AM', '8:00 AM'",
                },
            },
            "required": ["time"],
        },
    },
    {
        "name": "get_calendar",
        "description": (
            "Get Karthik's calendar events for a given day. "
            "Use when asked about meetings, schedule, busy times, or calendar. "
            "Pulls from Outlook work calendar and Google Calendar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date as YYYY-MM-DD. Omit for today.",
                },
            },
        },
    },
    {
        "name": "web_search",
        "description": "Search the web for live data: scores, news, weather, prices, stock, events, anything current. Always use this instead of guessing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "schedule_think",
        "description": (
            "Schedule one or more future reflection checks. "
            "Use when you've made a promise to check on something later — e.g. 'I'll let you know if humidity gets too high', "
            "'I'll check on the geyser in 30 minutes'. "
            "Pass multiple entries to set up recurring checks (e.g. every 30 min for humidity monitoring). "
            "Sunday will wake up at each time, read the current home state, and decide whether to message Karthik. "
            "The context tells Sunday what to focus on during each check."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "thinks": {
                    "type": "array",
                    "description": "List of checks to schedule. Pass multiple for recurring monitoring.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "time": {
                                "type": "string",
                                "description": "Time in HH:MM 24-hour IST. E.g. '23:30', '01:00'.",
                            },
                            "context": {
                                "type": "string",
                                "description": "What to check. E.g. 'Room humidity — alert if above 55% and humidifier is on'.",
                            },
                            "complexity": {
                                "type": "string",
                                "enum": ["simple", "complex"],
                                "description": "simple = basic threshold check (use cheaper model). complex = needs reasoning across context, history, or multiple factors (use smarter model).",
                            },
                        },
                        "required": ["time", "context", "complexity"],
                    },
                },
            },
            "required": ["thinks"],
        },
    },
    {
        "name": "update_scheduled_thinks",
        "description": (
            "Replace all existing scheduled thinks with a new list. "
            "Use when Karthik wants to change the check interval ('make it every 15 mins'), "
            "adjust the thresholds in the context, or cancel monitoring entirely. "
            "Pass an empty list to cancel all scheduled checks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "thinks": {
                    "type": "array",
                    "description": "New list of checks to schedule, replacing all existing ones. Pass [] to cancel all.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "time": {"type": "string", "description": "HH:MM 24-hour IST."},
                            "context": {"type": "string", "description": "What to focus on."},
                            "complexity": {
                                "type": "string",
                                "enum": ["simple", "complex"],
                                "description": "simple = basic threshold check. complex = needs reasoning.",
                            },
                        },
                        "required": ["time", "context", "complexity"],
                    },
                },
            },
            "required": ["thinks"],
        },
    },
    {
        "name": "get_scheduled_thinks",
        "description": "Returns the list of currently scheduled future checks. Use when Karthik asks what checks are pending, what you're monitoring, or what's scheduled.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "watch_condition",
        "description": (
            "Set up a lightweight watcher that monitors a device or sensor condition without LLM polling. "
            "Sunday checks every 30s in pure Python and wakes the agent (one LLM call) when the condition is met — "
            "the agent then decides what to say or do. "
            "Use for: 'tell me if geyser is on more than 20 mins', 'alert me when AC turns off', "
            "'ping me if temperature drops below 24°C'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Device name: geyser, fan, ac, temperature, humidity, light 1, etc."},
                "condition": {
                    "type": "string",
                    "enum": ["on_for_minutes", "off_for_minutes", "turned_on", "turned_off", "above_value", "below_value"],
                    "description": "Condition type. Time-based: on/off_for_minutes. Transition: turned_on/off. Value: above/below_value.",
                },
                "threshold": {"type": "number", "description": "Minutes for time-based; °C or % for value-based."},
                "context": {"type": "string", "description": "Why this watch was set — passed to the agent when it fires so it knows what to say/do."},
                "once": {"type": "boolean", "description": "Remove after firing. Default true. Set false to keep watching indefinitely."},
            },
            "required": ["device", "condition", "threshold", "context"],
        },
    },
    {
        "name": "cancel_watches",
        "description": "Cancel active condition watches. Pass device to cancel only that device's watches, or omit to cancel all.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Device to cancel watches for. Omit to cancel all."},
            },
        },
    },
    {
        "name": "list_watches",
        "description": "Returns currently active condition watches. Use when Karthik asks what you're watching or monitoring.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "play_alert",
        "description": (
            "Play an audio alert through the room speaker — speaks a message out loud via TTS. "
            "Use when you need to get Karthik's attention immediately: a threshold has been crossed, "
            "an important event happened, or he explicitly asked to be alerted. "
            "Always pair with a Telegram message too so he gets it even if away from the room. "
            "Keep the spoken message short (under 20 words)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Short message to speak aloud, e.g. 'Humidity is at 72 percent — you might want to turn off the AC.'",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "ask_fitbot",
        "description": (
            "Send a message to FitBot, your sibling fitness agent. "
            "Speak in third person about Karthik "
            "(e.g. 'Karthik returned from badminton', "
            "'how many calories has Karthik had today'). "
            "FitBot knows the message is from you — not a human — and will log, query, or confirm accordingly. "
            "Use for any fitness event you observed or any question about Karthik's food/workout data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Third-person message to FitBot, e.g. 'Karthik returned from badminton', 'how many calories has Karthik had today', 'Karthik week summary'",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "create_calendar_event",
        "description": (
            "Create an event on Karthik's Google Calendar. "
            "Use when asked to add, schedule, book, or create a meeting/event/reminder. "
            "start_time and end_time are ISO 8601 in IST (e.g. '2026-04-25T15:00:00+05:30'). "
            "If only a date is given with no time, treat as all-day event using 'start_date'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Event title / summary",
                },
                "start_time": {
                    "type": "string",
                    "description": "Start datetime in ISO 8601 IST, e.g. '2026-04-25T15:00:00+05:30'. Omit for all-day events.",
                },
                "end_time": {
                    "type": "string",
                    "description": "End datetime in ISO 8601 IST, e.g. '2026-04-25T16:00:00+05:30'. Defaults to 1 hour after start. Omit for all-day events.",
                },
                "start_date": {
                    "type": "string",
                    "description": "Date for all-day event, YYYY-MM-DD format. Use this instead of start_time for all-day events.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional event description / notes.",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "get_works",
        "description": (
            "Get items from the work tracker — feedback, feature requests, bugs, enhancements. "
            "Call when asked what to work on, any pending feedback, open issues, user-reported problems, "
            "or what needs attention. Returns a list with id, type, app, user, title, state, timestamp."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": ["new", "confirmed", "review_later", "done", "all"],
                    "description": "Filter by state. Default: new",
                },
                "type": {
                    "type": "string",
                    "enum": ["feedback", "new_feature", "enhancement", "bug", "all"],
                    "description": "Filter by type. Default: all",
                },
            },
        },
    },
    {
        "name": "add_work",
        "description": (
            "Add a new item to the work tracker. Use when Karthik asks to log a feature idea, "
            "note a bug, track an enhancement, or record any work item for fitbot, sunday, or other apps."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["feedback", "new_feature", "enhancement", "bug"],
                    "description": "Type of work item",
                },
                "app": {
                    "type": "string",
                    "description": "Which app this is for: fitbot, sunday, or other",
                },
                "title": {
                    "type": "string",
                    "description": "Short description of the work item",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional extra detail or context",
                },
            },
            "required": ["type", "app", "title"],
        },
    },
    {
        "name": "update_work",
        "description": (
            "Change the state of a work tracker item (mark done, confirmed, etc.). "
            "Use when Karthik says things like 'mark work 8 as done', 'close that ticket', "
            "'I fixed the food logging issue, mark those done', or any closure/state change. "
            "Call get_works first if you need to find the id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "work_id": {
                    "type": "integer",
                    "description": "The id of the work item to update",
                },
                "state": {
                    "type": "string",
                    "enum": ["new", "confirmed", "review_later", "done"],
                    "description": "New state for the work item",
                },
            },
            "required": ["work_id", "state"],
        },
    },
]

# Hogar Z-wave devices (direct LAN control + real-time state)
_HOGAR_DEVICES = {
    "fan", "light 1", "light 2", "spots", "foot lamp", "cove",
}

# Map friendly names → HA media_player entity
_HA_DEVICE_MAP = {
    "tv":                    "media_player.tv",
    "soundbar":              "media_player.soundbar_2",
    "dining room speaker":   "media_player.googlehome5093",
    "dining speaker":        "media_player.googlehome5093",
    "sian speaker":          "media_player.googlehome9588",
    "sians room speaker":    "media_player.googlehome9588",
    "display":               "media_player.display",
}

_ha: "HAClient | None" = None
_hogar = None  # HogarClient | None
_geyser = None  # GeyserClient | None
_kasa = None  # KasaClient | None
_firetv = None  # FireTVClient | None
_st = None  # SmartThingsClient | None
_on_insight: "callable | None" = None
_reflector = None  # Reflector | None
_outlook_ics_url: str = ""
_gcal_ics_url: str = ""
_gcal_token_path: str = ""


def set_ha_client(client: "HAClient") -> None:
    global _ha
    _ha = client


def set_hogar_client(client) -> None:
    global _hogar
    _hogar = client


def set_geyser_client(client) -> None:
    global _geyser
    _geyser = client


def set_kasa_client(client) -> None:
    global _kasa
    _kasa = client


def set_firetv_client(client) -> None:
    global _firetv
    _firetv = client


def set_smartthings_client(client) -> None:
    global _st
    _st = client


def set_calendar_urls(outlook_url: str = "", gcal_url: str = "") -> None:
    global _outlook_ics_url, _gcal_ics_url
    _outlook_ics_url = outlook_url or ""
    _gcal_ics_url = gcal_url or ""
    if _outlook_ics_url:
        print("[Calendar] Outlook ICS configured.")
    if _gcal_ics_url:
        print("[Calendar] Google Calendar ICS configured.")


def set_gcal_token_path(path: str) -> None:
    global _gcal_token_path
    _gcal_token_path = path
    print(f"[Calendar] OAuth token path: {path}")


def set_insight_callback(fn: "callable") -> None:
    global _on_insight
    _on_insight = fn


_tavily_api_key: str = ""

def set_tavily_api_key(key: str) -> None:
    global _tavily_api_key
    _tavily_api_key = key or ""


def set_reflector(reflector) -> None:
    global _reflector
    _reflector = reflector


def _notify_insight(insight: str) -> None:
    if _on_insight:
        _on_insight(insight)



async def _soundbar_control(inputs):
    return "Soundbar capability is not yet available through HA in Sunday 2.0; no action was sent."



async def execute(name: str, inputs: dict, user_text: str = "") -> str:
    result = await _execute(name, inputs)
    try:
        memory.get().log_action(name, inputs, result, user_text)
    except Exception:
        pass
    return result


async def _execute(name: str, inputs: dict) -> str:
    if name == "get_time":
        return datetime.now().strftime("It's %I:%M %p on %A, %B %d, %Y")

    if name == "set_timer":
        seconds = int(inputs["seconds"])
        return _jobs.timer(seconds)
        m, s = divmod(seconds, 60)
        if m and s:
            return f"Timer set for {m} minute{'s' if m != 1 else ''} and {s} second{'s' if s != 1 else ''}."
        if m:
            return f"Timer set for {m} minute{'s' if m != 1 else ''}."
        return f"Timer set for {s} second{'s' if s != 1 else ''}."

    if name == "get_devices":
        return await _get_devices()

    if name == "get_presence":
        return await _get_presence()

    if name == "get_ssh_address":
        return await _get_ssh_address()

    if name == "set_alarm":
        return await _set_alarm(inputs["time"])

    if name == "get_calendar":
        date_str = inputs.get("date")
        return await _get_calendar(date_str)

    if name == "create_calendar_event":
        return await _create_calendar_event(inputs)

    if not _ha:
        return "Home Assistant is not configured."

    if name == "control_device":
        device = inputs["device"].lower().strip()
        action = inputs.get("action", "on")
        volume = inputs.get("volume")
        brightness = inputs.get("brightness")
        speed = inputs.get("speed")
        hue = inputs.get("hue")
        saturation = inputs.get("saturation")
        color_temp = inputs.get("color_temp")
        color_hex = inputs.get("color")
        if color_hex:
            hue, saturation = _hex_to_hs(color_hex)
        temperature = inputs.get("temperature")
        fan_mode = inputs.get("fan_mode")
        return await _control_device(device, action, volume, brightness, speed, hue, saturation, color_temp)

    if name == "ac_control":
        return await _ac_control(
            action=inputs.get("action"),
            mode=inputs.get("mode"),
            temperature=inputs.get("temperature"),
            fan_mode=inputs.get("fan_mode"),
        )

    if name == "get_home_info":
        return await _get_home_info()

    if name == "get_sensor_history":
        return await _get_sensor_history(inputs.get("sensor", "temperature"), inputs.get("hours", 12))

    if name == "get_memories":
        text = wiki_io.all_pages_text()
        return text if text else "No memories stored yet." 

    if name == "save_memory":
        insight = inputs["insight"]
        _notify_insight(insight)
        # Fire-and-forget: LLM router decides drop/append/edit/create on the wiki.
        await wiki_io.save_to_wiki(insight)
        print(f"[Memory] Routing insight to wiki: {insight!r}")
        return "Got it, I'll remember that."

    if name == "send_google_assistant_command":
        command = inputs["command"]
        result = await _ha.call_service(
            "google_assistant_sdk", "send_text_command", {"command": command}
        )
        return "Done." if result == "ok" else f"Google Assistant command failed: {result}"

    if name == "web_search":
        query = inputs.get("query", "")
        # Tavily: AI-friendly search. Returns a pre-summarised "answer" plus
        # top result snippets. Free 1000 q/month at tavily.com. Falls back to
        # "no answer" string on any error (caller deals with it).
        api_key = _tavily_api_key
        if not api_key:
            return "Web search not configured (missing tavily_api_key)."
        try:
            import urllib.parse
            payload = _json.dumps({
                "api_key": api_key,
                "query": query,
                "search_depth": "advanced",
                "include_answer": True,
                "max_results": 5,
            }).encode()
            req = urllib.request.Request(
                "https://api.tavily.com/search",
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "Sunday/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = _json.loads(r.read())
            # Strict: return ONLY Tavily's synthesised answer. Raw web snippets
            # would confuse Sunday — better to say "no answer" than dump links.
            answer = (data.get("answer") or "").strip()
            return answer if answer else f"No quick answer found for: {query}"
        except Exception as e:
            return f"Search failed: {e}"

    if name == "play_alert":
        from output import current_output
        if current_output.get():
            return "Progress speech is already routed through the active HA turn; separate alerts cannot interrupt it."
        if _reflector is None:
            return "Reflector not available — cannot play alert."
        message = inputs.get("message", "")
        if not message:
            return "No message provided."
        await _reflector.speak_alert(message)
        return f"Alert played: {message!r}"

    if name == "ask_fitbot":
        from a2a_client import call_fitbot
        return await call_fitbot(inputs["query"])

    if name == "get_scheduled_thinks":
        if _reflector is None:
            return "Reflector not available."
        thinks = _reflector.get_scheduled_thinks()
        if not thinks:
            return "No scheduled checks."
        lines = [f"{t['at']} — {t['reason']} [{t.get('model', 'gpt-4.1-mini')}]" for t in thinks]
        return "\n".join(lines)

    if name == "update_scheduled_thinks":
        thinks = inputs.get("thinks", [])
        if _reflector is None:
            return "Reflector not available."
        resolved = [dict(t, model="gpt-5" if t.get("complexity") == "complex" else "gpt-4.1-mini") for t in thinks]
        _reflector.replace_scheduled_thinks(resolved)
        if not resolved:
            return "All scheduled checks cancelled."
        added = [t["time"] for t in resolved if t.get("time")]
        return f"Replaced scheduled checks — {len(added)} check(s) at: {', '.join(added)}"

    if name == "watch_condition":
        if _reflector is None:
            return "Reflector not available."
        wid = await _reflector.add_condition_watch(
            inputs["device"], inputs["condition"], float(inputs["threshold"]),
            inputs["context"], inputs.get("once", True),
        )
        return f"Watch set (id={wid}) — checking every 30s, will alert when {inputs['device']} {inputs['condition']} {inputs['threshold']}. Zero LLM calls until it fires."

    if name == "cancel_watches":
        if _reflector is None:
            return "Reflector not available."
        n = _reflector.remove_condition_watches(inputs.get("device"))
        return f"Cancelled {n} watch(es)." if n else "No active watches to cancel."

    if name == "list_watches":
        if _reflector is None:
            return "Reflector not available."
        watches = _reflector.get_condition_watches()
        if not watches:
            return "No active condition watches."
        lines = [
            f"- [{w['id']}] {w['device']} {w['condition']} {w['threshold']} (once={w.get('once', True)}): {w['context']}"
            for w in watches
        ]
        return chr(10).join(lines)

    if name == "schedule_think":
        thinks = inputs.get("thinks", [])
        if not thinks:
            return "No thinks provided."
        if _reflector is None:
            return "Reflector not available."
        added = []
        for t in thinks:
            time_str = t.get("time", "")
            context = t.get("context", "")
            model = "gpt-5" if t.get("complexity") == "complex" else "gpt-4.1-mini"
            if time_str and context:
                _reflector.add_scheduled_think(time_str, context, model=model)
                added.append(time_str)
        return f"Scheduled {len(added)} check(s) at: {', '.join(added)}"

    if name == "end_conversation":
        return "ok"

    if name == "get_works":
        mem = memory.get()
        if not mem:
            return '{"error": "memory not available"}'
        state = inputs.get("state", "new")
        wtype = inputs.get("type", "all")
        rows = mem.get_works(
            state=None if state == "all" else state,
            type=None if wtype == "all" else wtype,
        )
        return _json.dumps(rows)

    if name == "add_work":
        mem = memory.get()
        if not mem:
            return '{"error": "memory not available"}'
        work_id = mem.add_work(
            type=inputs.get("type", "feedback"),
            app=inputs.get("app", "other"),
            user="Karthik",
            chat_id="REPLACE_ME",
            title=inputs["title"],
            notes=inputs.get("notes"),
        )
        return _json.dumps({"id": work_id, "status": "logged"})

    if name == "update_work":
        mem = memory.get()
        if not mem:
            return '{"error": "memory not available"}'
        work_id = int(inputs["work_id"])
        state = inputs["state"]
        row = mem.get_work_by_id(work_id)
        if not row:
            return _json.dumps({"error": f"work item {work_id} not found"})
        mem.update_work_state(work_id, state)
        return _json.dumps({"id": work_id, "state": state, "title": row["title"]})

    return f"Unknown tool: {name}"


def _hex_to_hs(hex_color: str) -> tuple[int, int]:
    """Convert hex color (#RRGGBB) to (hue 0-360, saturation 0-100)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i+2], 16) / 255 for i in (0, 2, 4))
    h, s, _ = colorsys.rgb_to_hsv(r, g, b)
    return round(h * 360), round(s * 100)


async def _ac_control(
    action: str | None = None,
    mode: str | None = None,
    temperature: int | None = None,
    fan_mode: str | None = None,
) -> str:
    """Bedroom AC via SmartIR climate.bedroom_ac (Broadlink IR underneath).

    All four params are optional but at least one must be set. Order of operations:
    hvac_mode (if explicit mode given OR action='on'), then temperature, then fan_mode.

    Mode resolution:
      action='off'                 -> hvac_mode=off, ignore other fields
      mode=<m>                     -> hvac_mode=m (overrides action)
      action='on' (no mode)        -> hvac_mode=cool
      neither                      -> leave hvac_mode alone (partial update)
    """
    if not _ha:
        return "Home Assistant is not configured."
    if action is None and mode is None and temperature is None and fan_mode is None:
        return "Nothing to do — set action, mode, temperature, or fan_mode."

    entity = "climate.bedroom_ac"

    # Off path: short-circuit, ignore other fields
    if action == "off":
        result = await _ha.call_service("climate", "set_hvac_mode",
                                         {"entity_id": entity, "hvac_mode": "off"})
        return "AC off." if result == "ok" else f"AC command failed: {result}"

    parts: list[str] = []

    # Pick target hvac_mode: explicit mode wins, else action='on' implies cool, else don't touch
    target_mode: str | None = None
    if mode is not None:
        m = mode.lower().strip()
        if m not in ("cool", "heat", "dry", "fan_only"):
            return f"Invalid mode '{mode}' (use cool/heat/dry/fan_only, or action='off' to turn off)."
        target_mode = m
    elif action == "on":
        target_mode = "cool"

    if target_mode is not None:
        result = await _ha.call_service("climate", "set_hvac_mode",
                                         {"entity_id": entity, "hvac_mode": target_mode})
        if result != "ok":
            return f"AC command failed: {result}"
        parts.append(target_mode if mode is not None else "on")

    if temperature is not None:
        temp = max(18, min(30, int(temperature)))
        result = await _ha.call_service("climate", "set_temperature",
                                         {"entity_id": entity, "temperature": temp})
        if result != "ok":
            return f"AC temp set failed: {result}"
        parts.append(f"{temp}°C")

    if fan_mode is not None:
        fm = fan_mode.lower().strip()
        if fm not in ("low", "mid", "high", "auto"):
            return f"Invalid fan_mode '{fan_mode}' (use low/mid/high/auto)."
        result = await _ha.call_service("climate", "set_fan_mode",
                                         {"entity_id": entity, "fan_mode": fm})
        if result != "ok":
            return f"AC fan set failed: {result}"
        parts.append(f"fan {fm}")

    return "AC: " + ", ".join(parts) + "."


async def _control_device(device, action, volume=None, brightness=None, speed=None, hue=None, saturation=None, color_temp=None):
    from ha import control
    return await control(_ha, device, action, volume, brightness, speed, hue, saturation, color_temp)



async def _control_firetv(inputs):
    return "FireTV launch/search/key operations are not yet represented in HA; no action was sent."



async def _get_devices():
    from ha import devices
    return await devices(_ha)



async def _ping(ip: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", "1", ip,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode == 0
    except Exception:
        return False


async def _get_presence() -> str:
    import web as _web
    from datetime import datetime as _dt, timedelta as _td

    # Check synthetic presence from memory first (covers gym detection via pressure)
    # Use it if the last log entry is within the last 4 hours and is a synthetic zone
    try:
        recent = memory.get().recent_presence(limit=1)
        if recent:
            last = recent[0]
            age = _dt.now() - _dt.fromisoformat(last["timestamp"])
            if age < _td(hours=4) and last["state"] not in ("home", "away", "not_home"):
                location = last["state"].replace("_", " ").title()
                return f"Karthik is at {location}."
    except Exception:
        pass

    presence = _web.get_presence()
    home = presence.get("phone", False)
    zone = _web.get_presence_zone("phone")

    if zone and zone not in ("unknown", "not_home"):
        location = zone.replace("_", " ").title()
        return f"Karthik is at {location}."
    return "Karthik is " + ("home" if home else "away") + "."


async def _get_ssh_address() -> str:
    try:
        with urllib.request.urlopen("http://localhost:4040/api/tunnels", timeout=3) as r:
            data = _json.loads(r.read())
        tunnels = data.get("tunnels", [])
        if not tunnels:
            return "ngrok is not running — no tunnel active."
        url = tunnels[0]["public_url"]  # e.g. tcp://0.tcp.in.ngrok.io:12345
        host = url.split("//")[1].split(":")[0]
        port = url.split(":")[-1]
        return f"ssh djpi@{host} -p {port}"
    except Exception as e:
        return f"Could not reach ngrok: {e}"


async def _get_home_info() -> str:
    temp = await _ha.get_state("sensor.lumi_lumi_sensor_ht_agl02_temperature")
    humidity = await _ha.get_state("sensor.lumi_lumi_sensor_ht_agl02_humidity")
    down = await _ha.get_state("sensor.hx510_download_speed")
    up = await _ha.get_state("sensor.hx510_upload_speed")

    t = round(float(temp.get("state", 0)), 1)
    h = round(float(humidity.get("state", 0)), 1)
    d = round(float(down.get("state", 0)))
    u = round(float(up.get("state", 0)))

    return (f"Temperature is {t}°C, humidity is {h}%. "
            f"Internet speed: {d} Mbps down, {u} Mbps up.")


async def _get_sensor_history(sensor: str, hours: int) -> str:
    if not _ha:
        return "Home Assistant is not connected."
    hours = min(max(hours, 1), 48)
    entity_id = (
        "sensor.lumi_lumi_sensor_ht_agl02_temperature" if sensor == "temperature"
        else "sensor.lumi_lumi_sensor_ht_agl02_humidity"
    )
    records = await _ha.get_history(entity_id, hours=hours)
    if not records:
        return f"No {sensor} history found for the last {hours} hours."

    from datetime import datetime, timezone
    points = []
    for r in records:
        try:
            val = float(r["state"])
            ts = datetime.fromisoformat(r["last_changed"]).astimezone()
            points.append((ts, val))
        except (ValueError, KeyError):
            continue

    if not points:
        return f"No valid {sensor} readings in the last {hours} hours."

    unit = "°C" if sensor == "temperature" else "%"
    values = [v for _, v in points]
    avg = round(sum(values) / len(values), 1)
    mn, mx = round(min(values), 1), round(max(values), 1)
    first_ts, first_val = points[0]
    last_ts, last_val = points[-1]

    lines = [
        f"{sensor.capitalize()} history for the last {hours}h ({len(points)} readings):",
        f"  Min: {mn}{unit}  Max: {mx}{unit}  Avg: {avg}{unit}",
        f"  Oldest: {first_val}{unit} at {first_ts.strftime('%I:%M %p')}",
        f"  Latest: {last_val}{unit} at {last_ts.strftime('%I:%M %p')}",
    ]
    # Include a condensed timeline (up to 10 evenly-spaced samples)
    step = max(1, len(points) // 10)
    timeline = [f"{ts.strftime('%I:%M %p')} {val}{unit}" for ts, val in points[::step]]
    lines.append("  Timeline: " + "  →  ".join(timeline))
    return "\n".join(lines)


async def _run_timer(seconds):
    return _jobs.timer(seconds)



async def _set_alarm(time_str):
    return _jobs.alarm(time_str)



async def _create_calendar_event(inputs: dict) -> str:
    """Create an event on Karthik's Google Calendar using OAuth2 credentials."""
    import datetime as _dt
    from datetime import timezone, timedelta

    token_path = _gcal_token_path
    if not token_path:
        import os
        token_path = os.path.join(os.path.dirname(__file__), "calendar_token.json")

    if not os.path.exists(token_path):
        return (
            "Google Calendar not authorized. "
            "Run setup_gcal_auth.py on the Pi first to grant access."
        )

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GRequest
        from googleapiclient.discovery import build
    except ImportError:
        return "Missing dependencies. Run: pip3 install google-auth google-api-python-client"

    IST = timezone(timedelta(hours=5, minutes=30))
    SCOPE = "https://www.googleapis.com/auth/calendar"

    # Load and refresh credentials
    creds = Credentials.from_authorized_user_file(token_path, [SCOPE])
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(GRequest())
                import os as _os
                with open(token_path, "w") as f:
                    f.write(creds.to_json())
            except Exception as e:
                return f"Calendar token refresh failed: {e}. Re-run setup_gcal_auth.py."
        else:
            return "Calendar token invalid. Re-run setup_gcal_auth.py."

    title = inputs.get("title", "Event")
    description = inputs.get("description", "")
    start_time = inputs.get("start_time")
    end_time = inputs.get("end_time")
    start_date = inputs.get("start_date")

    try:
        service = build("calendar", "v3", credentials=creds)

        if start_date and not start_time:
            # All-day event
            try:
                d = _dt.date.fromisoformat(start_date)
            except ValueError:
                return f"Invalid date format: {start_date!r}. Use YYYY-MM-DD."
            next_day = (d + _dt.timedelta(days=1)).isoformat()
            body = {
                "summary": title,
                "start": {"date": d.isoformat()},
                "end": {"date": next_day},
            }
            if description:
                body["description"] = description
            event = service.events().insert(calendarId="primary", body=body).execute()
            return f"All-day event '{title}' added on {d.isoformat()}."
        elif start_time:
            # Timed event
            try:
                start_dt = _dt.datetime.fromisoformat(start_time)
            except ValueError:
                return f"Invalid start_time format: {start_time!r}. Use ISO 8601."
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=IST)

            if end_time:
                try:
                    end_dt = _dt.datetime.fromisoformat(end_time)
                except ValueError:
                    return f"Invalid end_time format: {end_time!r}."
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=IST)
            else:
                end_dt = start_dt + _dt.timedelta(hours=1)

            body = {
                "summary": title,
                "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Kolkata"},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Kolkata"},
            }
            if description:
                body["description"] = description
            event = service.events().insert(calendarId="primary", body=body).execute()
            start_label = start_dt.strftime("%b %d at %I:%M %p")
            return f"Event '{title}' created on {start_label} IST."
        else:
            return "Provide either start_time (timed event) or start_date (all-day event)."
    except Exception as e:
        return f"Calendar error: {e}"


async def _get_calendar(date_str: str | None = None) -> str:
    from datetime import date, timedelta
    IST_OFF = timedelta(hours=5, minutes=30)
    import datetime as _dt
    today = (_dt.datetime.utcnow() + IST_OFF).date()

    if date_str:
        ds = date_str.strip().lower()
        if ds in ("today", ""):
            target = today
        elif ds == "tomorrow":
            target = today + _dt.timedelta(days=1)
        else:
            try:
                target = _dt.date.fromisoformat(date_str.strip())
            except ValueError:
                target = today
    else:
        target = today

    events = await _fetch_calendar(target)
    if not events:
        label = "today" if target == today else str(target)
        return f"No meetings on calendar for {label}."

    label = "today" if target == today else ("tomorrow" if target == today + _dt.timedelta(days=1) else str(target))
    lines = [f"Calendar for {label}:"]
    for ev in events:
        status_tag = f" [{ev['status']}]" if ev["status"] != "busy" else ""
        end = f"–{ev['end']}" if ev.get("end") else ""
        lines.append(f"  {ev['start']}{end}{status_tag}  {ev['title']}")
    return "\n".join(lines)


async def _fetch_calendar(target_date=None) -> list:
    """Fetch and expand recurring events for a date. Returns list of dicts."""
    import datetime as _dt
    from datetime import timezone, timedelta

    urls = [u for u in [_outlook_ics_url, _gcal_ics_url] if u]
    if not urls:
        return []

    IST = timezone(timedelta(hours=5, minutes=30))
    if target_date is None:
        target_date = (_dt.datetime.utcnow() + timedelta(hours=5, minutes=30)).date()

    all_events = []

    try:
        import icalendar
        import recurring_ical_events
    except ImportError:
        print("[Calendar] icalendar/recurring_ical_events not installed — run: pip3 install icalendar recurring-ical-events")
        return []

    def to_ist(dt):
        if isinstance(dt, _dt.datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=IST)
            return dt.astimezone(IST)
        # all-day date
        return _dt.datetime(dt.year, dt.month, dt.day, 0, 0, tzinfo=IST)

    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Sunday/1.0"})
            with urllib.request.urlopen(req, timeout=12) as r:
                raw = r.read()

            cal = icalendar.Calendar.from_ical(raw)
            # Use timezone-aware bounds — fixes Google Calendar date matching
            start_dt = _dt.datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=IST)
            end_dt = _dt.datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=IST)
            events = recurring_ical_events.of(cal).between(start_dt, end_dt)

            for ev in events:
                status = str(ev.get('STATUS', 'CONFIRMED')).upper()
                transp = str(ev.get('TRANSP', 'OPAQUE')).upper()
                if status == 'CANCELLED':
                    continue

                summary = str(ev.get('SUMMARY', 'Meeting'))
                dt_start = to_ist(ev['DTSTART'].dt)
                dt_end = to_ist(ev['DTEND'].dt) if ev.get('DTEND') else None

                # Mark as free only for work calendar (TRANSPARENT) — Google personal events keep all
                is_free = transp == 'TRANSPARENT' and url == _outlook_ics_url
                if is_free:
                    continue

                all_events.append({
                    "start": dt_start.strftime("%I:%M %p"),
                    "end": dt_end.strftime("%I:%M %p") if dt_end else None,
                    "start_24": dt_start.strftime("%H:%M"),
                    "title": summary,
                    "status": "tentative" if status == "TENTATIVE" else "busy",
                })
        except Exception as e:
            print(f"[Calendar] Fetch error: {e}")

    # Deduplicate (same start+end across multiple calendars) and sort
    seen = set()
    unique = []
    for ev in sorted(all_events, key=lambda x: x["start_24"]):
        key = (ev["start_24"], ev.get("end", ""), ev["title"])
        if key not in seen:
            seen.add(key)
            unique.append(ev)
    return unique


# ── Fast-path command resolution ─────────────────────────────────────────────
import re as _re

_GA_FAST_DEVICES = {
    "fan light": "fan light",
    "all lights": "all lights", "lights": "all lights", "light": "all lights",
}
# Aliases: spoken variants -> canonical Hogar device name (handled via _RGB_FAST_DEVICES)
_HOGAR_FAST_ALIASES = {
    "cove": "cove", "cove light": "cove",
    "spots": "spots", "spot lights": "spots", "spotlights": "spots",
    "foot lamp": "foot lamp", "foot light": "foot lamp",
    "light 1": "light 1", "light one": "light 1",
    "light 2": "light 2", "light two": "light 2",
}
_DIRECT_FAST_DEVICES = {"ac", "geyser", "projector", "fan", "humidifier", "humidifer"}

# RGB lights routed through control_device (Kasa)
_RGB_FAST_DEVICES = {"top light", "panels", "moon", "dashboard", "cove", "spots", "foot lamp", "light 1", "light 2"}

# Aliases that map to canonical device names
_DEVICE_ALIASES = {
    "humidifer": "humidifier",
    "humid": "humidifier",
    "vapouriser": "humidifier", "vaporizer": "humidifier",
    "ac": "ac", "a c": "ac", "air conditioner": "ac", "aircon": "ac",
    "geyser": "geyser", "water heater": "geyser",
    "fan": "fan", "ceiling fan": "fan",
    "projector": "projector",
    "moon": "moon",
    "top light": "top light", "top lights": "top light",
    "panels": "panels",
    "dashboard": "dashboard",
}

_ON_W  = r"(?:turn\s+on|switch\s+on|put\s+on|on)"
_OFF_W = r"(?:turn\s+off|switch\s+off|put\s+off|off)"
_FAST_DEVICE_PAT = _re.compile(
    r"^(?:" + _ON_W + r"\s+(?P<on_dev>.+?)|"
    + r"(?P<on2_dev>.+?)\s+" + _ON_W + r"|"
    + _OFF_W + r"\s+(?P<off_dev>.+?)|"
    + r"(?P<off2_dev>.+?)\s+" + _OFF_W + r")$",
    _re.IGNORECASE,
)
_FAST_TOGGLE_PAT = _re.compile(
    r"^(?:toggle\s+(?P<dev1>.+?)|(?P<dev2>.+?)\s+toggle)$",
    _re.IGNORECASE,
)
_FAST_FAN_SPEED_PAT = _re.compile(
    r"^(?:set\s+)?fan\s+(?:speed\s+)?(?:to\s+)?(?P<speed>high|medium|low|fast|slow)$",
    _re.IGNORECASE,
)

# AC temperature: "ac to 24", "set ac to 22", "ac 24", "ac 24 degrees"
_FAST_AC_TEMP_PAT = _re.compile(
    r"^(?:set\s+(?:the\s+)?)?(?:ac|a\s*c|air\s*con(?:ditioner)?)\s+(?:to\s+)?(?P<temp>\d{2})(?:\s*(?:degrees?|deg|c))?$",
    _re.IGNORECASE,
)


# Tier-1 state queries
_HUMIDITY_PAT = _re.compile(
    r"^(?:what'?s?|what\s+is|how'?s?|how|tell\s+me)?\s*(?:the\s+)?(?:humidity|humid|moisture)(?:\s+(?:level|now|here|is\s+it|in\s+(?:the\s+)?room))?\??$",
    _re.IGNORECASE,
)
_TEMP_PAT = _re.compile(
    r"^(?:what'?s?|what\s+is|how'?s?|tell\s+me)?\s*(?:the\s+)?(?:temperature|temp)(?:\s+(?:now|here|in\s+(?:the\s+)?room))?\??$",
    _re.IGNORECASE,
)
_TIME_PAT = _re.compile(
    r"^(?:what'?s?|what\s+is|tell\s+me)?\s*(?:the\s+)?(?:current\s+)?time(?:\s+(?:now|is\s+it))?\??$",
    _re.IGNORECASE,
)
_DATE_PAT = _re.compile(
    r"^(?:what'?s?|what\s+is|tell\s+me)?\s*(?:the\s+|today'?s?\s+)?date\??$",
    _re.IGNORECASE,
)
_IS_ON_PAT = _re.compile(
    r"^is\s+(?:the\s+)?(?P<dev>.+?)\s+(?P<state>on|off)\??$",
    _re.IGNORECASE,
)

# Trip-wires: any of these word patterns → defer to LLM (compound/conditional/negation/reasoning)
_TRIP_WIRE_PAT = _re.compile(
    r"\b(?:and|but|or|if|then|because|should|could|would|can\s+you|don'?t|do\s+not|not|never|why|how\s+come|explain|tell\s+me\s+about|what\s+about|else|also|too)\b",
    _re.IGNORECASE,
)


def _has_trip_wire(text: str) -> bool:
    """Return True if the text contains words that signal complex intent (skip bypass)."""
    return bool(_TRIP_WIRE_PAT.search(text))


def _bypass_log(matched: str, action: str) -> None:
    print(f"[Fast] matched: {matched} → {action}")


async def _read_humidity() -> str | None:
    if not _ha:
        return None
    try:
        s = await _ha.get_state("sensor.lumi_lumi_sensor_ht_agl02_humidity")
        h = round(float(s.get("state", 0)), 1)
        return f"Humidity is {h}%."
    except Exception:
        return None


async def _read_temperature() -> str | None:
    if not _ha:
        return None
    try:
        s = await _ha.get_state("sensor.lumi_lumi_sensor_ht_agl02_temperature")
        t = round(float(s.get("state", 0)), 1)
        return f"Temperature is {t}°C."
    except Exception:
        return None


def _read_time() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return "It's " + datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%I:%M %p").lstrip("0") + "."


def _read_date() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%A, %B %-d, %Y") + "."


# Map device name → (entity_id, label) for is-on queries
_IS_ON_ENTITIES = {
    "fan":         ("light.fan_light", "fan"),  # placeholder; we'll use Hogar state
    "ac":          (None, "ac"),
    "geyser":      ("switch.geyser_socket_1", "geyser"),
    "humidifier":  ("switch.wifi_socket_socket_3", "humidifier"),
    "humidifer":   ("switch.wifi_socket_socket_3", "humidifier"),
    "projector":   ("switch.16amp_smart_plug_2_socket_1", "projector"),
}


async def _read_device_state(device: str) -> str | None:
    """Return 'X is on/off.' for known devices."""
    dev = _DEVICE_ALIASES.get(device, device)
    info = _IS_ON_ENTITIES.get(dev)
    if not info:
        return None
    entity_id, label = info
    if entity_id and _ha:
        try:
            s = await _ha.get_state(entity_id)
            state = s.get("state", "unknown")
            if state in ("on", "off"):
                return f"{label.capitalize()} is {state}."
        except Exception:
            return None
    return None


async def fast_command(text):
    import re
    if _has_trip_wire(text):
        return None
    clean = text.lower().strip().rstrip(".!?")
    if _TIME_PAT.match(clean):
        return _read_time()
    if _DATE_PAT.match(clean):
        return _read_date()
    if _TEMP_PAT.match(clean):
        return await _read_temperature()
    if _HUMIDITY_PAT.match(clean):
        return await _read_humidity()
    match = re.fullmatch(r"(?:turn|switch) (on|off) (?:the )?(.+)", clean)
    if not match:
        match = re.fullmatch(r"(?:turn|switch) (?:the )?(.+?) (on|off)", clean)
        if match:
            device, action = match.groups()
        else:
            return None
    else:
        action, device = match.groups()
    from ha import resolve
    try:
        resolve(device)
    except ValueError:
        if device != "all lights":
            return None
    return await execute("control_device", {"device": device, "action": action}, text)



_jobs = None

def set_jobs(jobs):
    global _jobs
    _jobs = jobs

async def projector_screen(action):
    if action not in ("down", "close"):
        return "Screen action must be down or close."
    if action == "down":
        for device in ("moon", "top light"):
            state = await _ha.get_state(__import__("ha").resolve(device))
            if state.get("state") != "off":
                result = await _control_device(device, "off")
                if "unavailable" in result or "failed" in result:
                    return "Screen was not moved because a mural light could not be verified off: " + result
    result = await _ha.call_service("script", "turn_on", {"entity_id": "script.projector_screen_" + action})
    return "Projector screen command sent." if result == "ok" else result

_original_execute = _execute
async def _execute(name, inputs):
    if name == "projector_screen":
        return await projector_screen(inputs["action"])
    return await _original_execute(name, inputs)

DEFINITIONS.append({"name": "projector_screen", "description": "Move projector screen through HA; mural lights must be off before lowering.", "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["down", "close"]}}, "required": ["action"]}})


_original_execute_logged = execute
async def execute(name, inputs, user_text=""):
    from output import current_output
    output = current_output.get()
    if output and output.read_only and name not in {"get_time", "get_devices", "get_home_info", "get_sensor_history", "get_memories", "get_presence", "get_ssh_address", "get_calendar", "get_scheduled_thinks", "list_watches", "get_works", "web_search"}:
        return "Read-only validation turn: this action was not executed."
    import jsonschema
    schema = next((d["input_schema"] for d in DEFINITIONS if d["name"] == name), None)
    if schema is None:
        return "Unknown or unavailable tool: " + name
    try:
        jsonschema.validate(inputs, {**schema, "additionalProperties": False})
    except jsonschema.ValidationError as error:
        return "Invalid tool arguments: " + error.message
    result = await _original_execute_logged(name, inputs, user_text)
    if name in {"schedule_think", "update_scheduled_thinks", "watch_condition"} and _reflector and not _reflector.config.background_enabled:
        result += " Background checks are currently paused; this record will run only after checks are enabled."
    if name in {"set_timer", "set_alarm"} and _reflector and not _reflector.config.outbound_speech_enabled:
        result += " Room announcements are currently disabled; no alarm sound will play."
    return result


# Rebuild milestone: explicitly exclude copied cognition, wiki and FitBot interfaces.
import home_tools
import world_tools
import history_tools
from state_notifications import tools as notification_tools
_REBUILD_UTILITIES = {'get_time', 'web_search', 'get_calendar', 'create_calendar_event'}
DEFINITIONS = [d for d in DEFINITIONS if d['name'] in _REBUILD_UTILITIES] + home_tools.DEFINITIONS + world_tools.DEFINITIONS + history_tools.DEFINITIONS + notification_tools.DEFINITIONS
import camera_tools
DEFINITIONS = DEFINITIONS + camera_tools.DEFINITIONS
import dev_mode_tools
import speech_mode_tools
import codex_tools
import firetv_voice_tools
import automation_tools
DEFINITIONS = DEFINITIONS + dev_mode_tools.DEFINITIONS + speech_mode_tools.DEFINITIONS + firetv_voice_tools.DEFINITIONS + codex_tools.DEFINITIONS + automation_tools.DEFINITIONS
_legacy_validated_execute = execute
async def execute(name, inputs, user_text=''):
    from output import current_output
    import jsonschema
    definition = next((d for d in DEFINITIONS if d['name'] == name), None)
    if definition is None:
        return 'This capability is unavailable in the rebuild milestone.'
    try:
        jsonschema.validate(inputs, {**definition['input_schema'], 'additionalProperties': False})
    except jsonschema.ValidationError as error:
        return 'Invalid tool arguments: ' + error.message
    output = current_output.get()
    if output and output.read_only and name not in {'home_query','world_read','history_read','get_time','web_search','get_calendar','get_codex_result'} and not (name=='manage_device_notifications' and inputs.get('operation')=='list'):
        return 'Read-only turn: no action executed.'
    if name=='create_automation':
        return await automation_tools.create(home_tools._model.ha, inputs)
    if name=='manage_device_notifications':
        return await notification_tools.execute(inputs)
    if name=='history_read':
        return await history_tools.execute(getattr(home_tools._model,'history',None),inputs)
    if name=='world_read':
        world=getattr(home_tools._model,'world',None)
        return await world_tools.execute(world,inputs) if world else 'World Model unavailable.'
    if name=='take_picture':
        return await camera_tools.execute(inputs)
    if name=='get_codex_result':
        return await codex_tools.lookup(inputs)
    if name=='ask_codex':
        return await codex_tools.execute(inputs)
    if name=='ask_fire_tv':
        return await firetv_voice_tools.execute(inputs)
    if name=='set_telugu_mode':
        return await speech_mode_tools.execute(inputs)
    if name=='dev_mode':
        return await dev_mode_tools.execute(inputs)
    if name.startswith('home_'):
        return await home_tools.execute(name, inputs)
    return await _legacy_validated_execute(name, inputs, user_text)
