"""
A2A client for Sunday — calls FitBot's /tasks/send endpoint.
Uses aiohttp (already a Sunday dependency).
"""
import uuid
import aiohttp

FITBOT_URL = "http://localhost:8000"


async def call_fitbot(text: str) -> str:
    """Send a natural-language task to FitBot via A2A. Returns plain text result."""
    payload = {
        "id": str(uuid.uuid4()),
        "message": {
            "role": "user",
            "parts": [{"type": "text", "text": text}],
        },
    }
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as session:
            async with session.post(f"{FITBOT_URL}/tasks/send", json=payload) as resp:
                data = await resp.json()
        parts = data.get("artifacts", [{}])[0].get("parts", [])
        return next(
            (p["text"] for p in parts if p.get("type") == "text"),
            "No response from FitBot.",
        )
    except Exception as e:
        return f"FitBot unavailable: {e}"
