"""
Kasa/Tapo client — discovery-based, no static IPs required.

Config format (tapo_devices in config.json):
  {
    "friendly_name": {
      "alias":    "<exact name set in Tapo app>",
      "username": "<tapo account email>",
      "password": "<tapo account password>"
    }
  }

At startup, discovers all devices on the LAN per unique account and matches
them to config entries by alias. Retries offline devices in the background.
"""
import asyncio
from kasa import Discover, Credentials, Device


RETRY_INTERVAL = 60
MAX_RETRIES    = 20


class KasaClient:
    def __init__(self, username: str, password: str, devices: dict):
        self._default_username = username
        self._default_password = password

        # Normalise config entries
        self._configs: dict[str, dict] = {}
        for k, v in (devices or {}).items():
            name = k.lower()
            if isinstance(v, str):
                # Legacy: value is just an IP — treat alias = friendly name
                self._configs[name] = {
                    "alias":    name,
                    "username": username,
                    "password": password,
                }
            else:
                self._configs[name] = {
                    "alias":    v.get("alias", name),
                    "username": v.get("username", username),
                    "password": v.get("password", password),
                }

        self._devices: dict[str, Device] = {}
        self._retry_counts: dict[str, int] = {}
        self._bad_creds: set[str] = set()

    # ── Discovery ─────────────────────────────────────────────────────────────

    def _cred_groups(self) -> dict[tuple, list[str]]:
        """Group config entries by (username, password) for batch discovery."""
        groups: dict[tuple, list[str]] = {}
        for name, cfg in self._configs.items():
            key = (cfg["username"], cfg["password"])
            groups.setdefault(key, []).append(name)
        return groups

    async def _discover_for_creds(self, username: str, password: str, names: list[str]) -> None:
        """Run one discovery sweep for a set of credentials and match results."""
        alias_to_name = {self._configs[n]["alias"].lower().strip(): n for n in names}
        try:
            found = await Discover.discover(
                credentials=Credentials(username, password),
                timeout=6,
            )
        except Exception as e:
            print(f"[Kasa] Discovery error ({username}): {e}")
            return

        for ip, dev in found.items():
            try:
                await dev.update()
            except Exception:
                continue
            alias_key = (dev.alias or "").lower().strip()
            name = alias_to_name.get(alias_key)
            if name and name not in self._devices:
                self._devices[name] = dev
                self._retry_counts.pop(name, None)
                print(f"[Kasa] Connected: {name} — '{dev.alias}' ({ip})")

        # Mark anything still unconnected for retry
        for name in names:
            if name not in self._devices and name not in self._bad_creds:
                attempts = self._retry_counts.get(name, 0) + 1
                self._retry_counts[name] = attempts
                if attempts == 1:
                    alias = self._configs[name]["alias"]
                    print(f"[Kasa] '{alias}' not found on network, will retry in background")

    async def start(self) -> None:
        """Initial discovery sweep across all credential groups."""
        await asyncio.gather(*[
            self._discover_for_creds(u, p, names)
            for (u, p), names in self._cred_groups().items()
        ])

    async def retry_loop(self) -> None:
        """Background task — re-runs discovery for unconnected devices."""
        while True:
            await asyncio.sleep(RETRY_INTERVAL)

            # Group only the pending ones
            pending_groups: dict[tuple, list[str]] = {}
            for name, attempts in list(self._retry_counts.items()):
                if name in self._devices or name in self._bad_creds:
                    del self._retry_counts[name]
                    continue
                if attempts >= MAX_RETRIES:
                    alias = self._configs[name]["alias"]
                    print(f"[Kasa] Gave up on '{alias}' after {MAX_RETRIES} attempts")
                    del self._retry_counts[name]
                    continue
                cfg = self._configs[name]
                key = (cfg["username"], cfg["password"])
                pending_groups.setdefault(key, []).append(name)

            for (u, p), names in pending_groups.items():
                await self._discover_for_creds(u, p, names)

    # ── Device access ─────────────────────────────────────────────────────────

    async def _reconnect(self, name: str) -> bool:
        """Re-discover a stale device after a command timeout."""
        cfg = self._configs.get(name)
        if not cfg:
            return False
        self._devices.pop(name, None)
        print(f"[Kasa] Reconnecting '{name}'...")
        await self._discover_for_creds(cfg["username"], cfg["password"], [name])
        return name in self._devices

    async def _run_command(self, name: str, fn) -> bool:
        """Run a device command, reconnecting once on failure."""
        dev = self._devices.get(name.lower())
        if not dev:
            return False
        try:
            await fn(dev)
            return True
        except Exception as e:
            print(f"[Kasa] command error for {name}: {e} — reconnecting")
            if await self._reconnect(name):
                dev = self._devices.get(name.lower())
                if dev:
                    try:
                        await fn(dev)
                        return True
                    except Exception as e2:
                        print(f"[Kasa] retry failed for {name}: {e2}")
            return False

    def get_device(self, name: str) -> Device | None:
        return self._devices.get(name.lower())

    async def get_state(self, name: str) -> dict:
        dev = self.get_device(name)
        if not dev:
            return {}
        await dev.update()
        state = {"on": dev.is_on}
        if hasattr(dev, "brightness"):
            state["brightness"] = dev.brightness
        if hasattr(dev, "color_temp"):
            state["color_temp"] = dev.color_temp
        return state

    async def set_on(self, name: str, on: bool) -> bool:
        async def fn(dev): await dev.turn_on() if on else await dev.turn_off()
        return await self._run_command(name, fn)

    async def set_brightness(self, name: str, brightness: int) -> bool:
        async def fn(dev): await dev.set_brightness(brightness)
        return await self._run_command(name, fn)

    async def set_color(self, name: str, hue: int, saturation: int, brightness: int | None = None) -> bool:
        async def fn(dev): await dev.set_hsv(hue, saturation, brightness if brightness is not None else 100)
        return await self._run_command(name, fn)

    async def set_color_temp(self, name: str, kelvin: int, brightness: int | None = None) -> bool:
        async def fn(dev):
            if brightness is not None:
                await dev.set_brightness(brightness)
            await dev.set_color_temp(kelvin)
        return await self._run_command(name, fn)

    def get_all_states(self) -> dict[str, dict]:
        states = {}
        for name, dev in self._devices.items():
            state = {"on": dev.is_on}
            if hasattr(dev, "brightness"):
                state["brightness"] = dev.brightness
            states[name] = state
        return states

    async def refresh_all_states(self) -> dict[str, dict]:
        await asyncio.gather(
            *[dev.update() for dev in self._devices.values()],
            return_exceptions=True,
        )
        return self.get_all_states()
