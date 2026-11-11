import aiohttp


class HAClient:
    """Async Home Assistant REST API client."""

    def __init__(self, url: str, token: str):
        self._url = url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def get_states(self) -> list[dict]:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{self._url}/api/states", headers=self._headers) as r:
                return await r.json()

    async def get_state(self, entity_id: str) -> dict:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{self._url}/api/states/{entity_id}", headers=self._headers) as r:
                return await r.json()

    async def get_history(self, entity_id: str, hours: int = 24) -> list[dict]:
        from datetime import datetime, timedelta, timezone
        start = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        url = f"{self._url}/api/history/period/{start}?filter_entity_id={entity_id}&minimal_response=true"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=self._headers) as r:
                data = await r.json()
                return data[0] if data else []

    async def call_service(self, domain: str, service: str, data: dict = {}) -> str:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{self._url}/api/services/{domain}/{service}",
                headers=self._headers,
                json=data,
            ) as r:
                body = await r.text()
                if r.status not in (200, 201):
                    return f"HA returned {r.status}: {body[:200]}"
                return "ok"

    async def get_config_entries(self, domain: str) -> list[dict]:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{self._url}/api/config/config_entries/entry", headers=self._headers) as r:
                entries = await r.json()
                return [e for e in entries if e.get("domain") == domain]

    async def reload_config_entry(self, entry_id: str) -> bool:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{self._url}/api/config/config_entries/entry/{entry_id}/reload",
                headers=self._headers,
            ) as r:
                return r.status == 200
