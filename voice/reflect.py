"""
Nightly reflection job — runs once a day, reads yesterday's actions,
asks an LLM to find patterns, routes each new insight through the wiki integrator.

The action_log SQLite table is the raw source. The wiki (~/sunday/wiki/) is the memory.
Run via systemd timer (sunday-reflect.timer).
"""
import asyncio
import json
import sys
from datetime import datetime, timedelta

import memory
import wiki_io
from config import Config

REFLECT_PROMPT = """\
You are analysing yesterday's log for Sunday, a voice assistant used by Karthik Veenam.
Below is the data: device/tool actions and a record of where Karthik went throughout the day.

Your job: extract clear, specific, useful patterns and insights about Karthik's routines,
preferences, and behaviours. Focus on:
- Time-based habits (e.g. "turns on fan around 11 PM before sleeping")
- Device usage patterns (e.g. "always turns on cove light + dashboard together")
- Sequences (e.g. "AC is turned on shortly before geyser — likely getting ready to sleep")
- Preferences (e.g. "prefers fan on medium, not high")
- Movement patterns (e.g. "plays badminton Monday/Wednesday mornings")
- Anything surprising or noteworthy

Output a JSON array of insight objects. Each object must have ONE field:
  "insight": a single clear sentence describing the pattern.

IMPORTANT: Already known insights are provided in the user message as a full memory wiki.
Do NOT repeat, rephrase, or add minor variations of facts already covered.
Only output insights that are genuinely new or add meaningfully new detail.

If there is nothing new to add, return an empty array.
If the data is sparse or does not clearly support a pattern, do not invent — return an empty array.

Output ONLY valid JSON. No explanation, no markdown. Example:
[
  {"insight": "Karthik turns on the fan every night between 10 PM and midnight."},
  {"insight": "Plays badminton on Monday, Wednesday, and Friday mornings."}
]\
"""


async def main() -> None:
    config = Config.load()
    memory.init()
    mem = memory.get()

    if not config.openai_api_key:
        print("[Reflect] FAIL: no openai_api_key in config")
        sys.exit(1)
    wiki_io.init(config.openai_api_key)

    since = (datetime.now() - timedelta(hours=24)).isoformat()
    actions = mem.actions_since(since)
    presence = mem.recent_presence(limit=100)
    presence = [p for p in presence if p["timestamp"] >= since]

    if not actions and not presence:
        print("[Reflect] No actions or zone changes in the last 24 hours. Nothing to analyse.")
        return

    lines = []
    if presence:
        lines.append("--- Where Karthik's been (oldest first) ---")
        for p in reversed(presence):
            lines.append(f"[{p['timestamp']}] went to → {p['state']}")
        lines.append("")
    if actions:
        lines.append("--- Device/tool actions ---")
        lines.extend(
            f"[{a['timestamp']}] {a['tool_name']} | input: {a['inputs']} | result: {a['result']} | user said: {a['user_text']!r}"
            for a in actions
        )
    log_text = "\n".join(lines)

    # Existing knowledge = the wiki itself.
    wiki_text = wiki_io.all_pages_text() or "(empty)"

    print(f"[Reflect] Analysing {len(actions)} actions vs current wiki...")

    user_content = (
        f"Current memory wiki (do not duplicate these facts):\n{wiki_text}\n\n"
        f"Yesterday's data:\n{log_text}"
    )

    provider = getattr(config, "llm_provider", "gemini")
    if provider == "openai":
        from openai import OpenAI as _OAI
        oai = _OAI(api_key=config.openai_api_key)
        model = getattr(config, "openai_chat_model", "") or "gpt-5"
        resp = oai.chat.completions.create(
            model=model,
            max_completion_tokens=4096,
            messages=[
                {"role": "system", "content": REFLECT_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
    else:
        from google import genai as _genai
        from google.genai import types as _gtypes
        client = _genai.Client(api_key=config.google_api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_content,
            config=_gtypes.GenerateContentConfig(
                system_instruction=REFLECT_PROMPT,
                max_output_tokens=4096,
            ),
        )
        parts = response.candidates[0].content.parts or []
        raw = "".join(p.text for p in parts if p.text and not getattr(p, "thought", False)).strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.split("\n")[:-1])
    raw = raw.strip()

    try:
        insights = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[Reflect] Failed to parse response:\n{raw}")
        sys.exit(1)

    if not insights:
        print("[Reflect] No patterns found — nothing new vs the wiki.")
        return

    # Route each insight through the wiki integrator.
    saved = 0
    for item in insights:
        insight = (item.get("insight") or "").strip()
        if not insight:
            continue
        plan = await wiki_io.save_to_wiki(insight)
        action = plan.get("action", "?")
        page = plan.get("page", "-")
        print(f"[Reflect] {action:6s} {page or '-':20s} ← {insight}")
        if action in ("append", "edit", "create"):
            saved += 1

    print(f"[Reflect] Done. {saved}/{len(insights)} insights persisted to wiki.")


if __name__ == "__main__":
    asyncio.run(main())
