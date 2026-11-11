"""SmartThings client — uses HA's stored OAuth tokens, auto-refreshes via refresh_token."""
import asyncio
import json
import time
import aiohttp

ST_BASE = "https://api.smartthings.com/v1"
HA_CONFIG_PATH = "/config/.storage/core.config_entries"
ST_REFRESH_URL = "https://auth-global.api.smartthings.com/oauth/token"

# Docker exec helper to read/write HA internal config
_DOCKER_CONTAINER = "homeassistant"


def _read_ha_config() -> dict:
    import subprocess
    result = subprocess.run(
        ["docker", "exec", _DOCKER_CONTAINER, "cat", HA_CONFIG_PATH],
        capture_output=True, text=True
    )
    return json.loads(result.stdout)


def _write_ha_config(data: dict) -> None:
    import subprocess, tempfile, os
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, tmp, indent=2)
    tmp.close()
    subprocess.run(["docker", "cp", tmp.name, f"{_DOCKER_CONTAINER}:{HA_CONFIG_PATH}"], check=True)
    os.unlink(tmp.name)


def _get_st_entry(cfg: dict) -> dict | None:
    for e in cfg["data"]["entries"]:
        if e.get("domain") == "smartthings":
            return e
    return None


async def _get_fresh_token() -> str:
    """Return a valid SmartThings access token, refreshing if expired."""
    cfg = _read_ha_config()
    entry = _get_st_entry(cfg)
    if not entry:
        raise RuntimeError("SmartThings entry not found in HA config")

    token_data = entry["data"]["token"]
    expires_at = token_data.get("expires_at", 0)

    # If token is still valid (with 60s buffer), use it
    if time.time() < expires_at - 60:
        return token_data["access_token"]

    # Refresh it
    refresh_token = token_data["refresh_token"]
    async with aiohttp.ClientSession() as s:
        async with s.post(
            ST_REFRESH_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status != 200:
                text = await r.text()
                raise RuntimeError(f"Token refresh failed {r.status}: {text[:200]}")
            new_tokens = await r.json()

    # Update HA's config with new tokens
    token_data["access_token"] = new_tokens["access_token"]
    token_data["refresh_token"] = new_tokens.get("refresh_token", refresh_token)
    token_data["expires_in"] = new_tokens.get("expires_in", 86400)
    token_data["expires_at"] = time.time() + new_tokens.get("expires_in", 86400)
    _write_ha_config(cfg)

    return new_tokens["access_token"]


class SmartThingsClient:
    def __init__(self, device_id: str):
        self._device_id = device_id

    async def _headers(self) -> dict:
        token = await _get_fresh_token()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def get_status(self) -> dict:
        headers = await self._headers()
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{ST_BASE}/devices/{self._device_id}/status",
                headers=headers, timeout=aiohttp.ClientTimeout(total=8)
            ) as r:
                data = await r.json()
        main = data.get("components", {}).get("main", {})
        def val(cap, attr):
            return main.get(cap, {}).get(attr, {}).get("value")
        return {
            "power":  val("switch", "switch") or "unknown",
            "volume": val("audioVolume", "volume"),
            "mute":   val("audioMute", "mute") or "unknown",
            "input":  val("samsungvd.audioInputSource", "inputSource"),
            "supported_inputs": val("samsungvd.audioInputSource", "supportedInputSources") or [],
        }

    async def _execute(self, capability: str, command: str, args: list = None) -> bool:
        headers = await self._headers()
        body = {"commands": [{"component": "main", "capability": capability,
                               "command": command, "arguments": args or []}]}
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{ST_BASE}/devices/{self._device_id}/commands",
                headers=headers, json=body, timeout=aiohttp.ClientTimeout(total=8)
            ) as r:
                return r.status in (200, 202)

    async def power(self, on: bool) -> bool:
        return await self._execute("switch", "on" if on else "off")

    async def set_volume(self, volume: int) -> bool:
        return await self._execute("audioVolume", "setVolume", [max(0, min(100, volume))])

    async def volume_up(self, step: int = 5) -> bool:
        s = await self.get_status()
        return await self.set_volume((s.get("volume") or 0) + step)

    async def volume_down(self, step: int = 5) -> bool:
        s = await self.get_status()
        return await self.set_volume((s.get("volume") or 0) - step)

    async def mute(self, muted: bool) -> bool:
        return await self._execute("audioMute", "mute" if muted else "unmute")

    # Known input cycle order (empirically confirmed on HW-Q990F)
    _INPUT_ORDER = ["D.IN", "HDMI1", "BT", "TV ARC", "WIFI", "HDMI2"]

    async def set_input(self, source: str) -> bool:
        source = source.upper()
        status = await self.get_status()
        supported = [s.upper() for s in (status.get("supported_inputs") or self._INPUT_ORDER)]
        if source not in supported:
            return False
        if status.get("power") in ("off", "unknown", None):
            await self._execute("switch", "on")
            await asyncio.sleep(4)
        for _ in range(len(supported) + 1):
            status = await self.get_status()
            if (status.get("input") or "").upper() == source:
                return True
            await self._execute("samsungvd.audioInputSource", "setNextInputSource")
            await asyncio.sleep(2.5)
        return False
