"""
Fire TV client via ADB over WiFi.
Controls the Firestick: power, apps, playback, search.
"""
import asyncio
import subprocess


APPS = {
    "netflix":       "com.netflix.ninja/.MainActivity",
    "prime":         "com.amazon.avod.thirdpartyclient/.LaunchActivity",
    "prime video":   "com.amazon.avod.thirdpartyclient/.LaunchActivity",
    "youtube":       "com.amazon.firetv.youtube/com.amazon.firetv.youtube.app.YouTubeActivity",
    "hotstar":       "in.startv.hotstar/in.startv.hotstar.activities.SplashActivity",
    "spotify":       "com.spotify.music/.MainActivity",
    "plex":          "com.plexapp.android/.SplashActivity",
    "home":          "com.amazon.tv.launcher/.ui.MainSettingsActivity",
}

KEYCODES = {
    "play_pause":   "KEYCODE_MEDIA_PLAY_PAUSE",
    "play":         "KEYCODE_MEDIA_PLAY",
    "pause":        "KEYCODE_MEDIA_PAUSE",
    "stop":         "KEYCODE_MEDIA_STOP",
    "next":         "KEYCODE_MEDIA_NEXT",
    "prev":         "KEYCODE_MEDIA_PREVIOUS",
    "rewind":       "KEYCODE_MEDIA_REWIND",
    "forward":      "KEYCODE_MEDIA_FAST_FORWARD",
    "back":         "KEYCODE_BACK",
    "home":         "KEYCODE_HOME",
    "up":           "KEYCODE_DPAD_UP",
    "down":         "KEYCODE_DPAD_DOWN",
    "left":         "KEYCODE_DPAD_LEFT",
    "right":        "KEYCODE_DPAD_RIGHT",
    "select":       "KEYCODE_DPAD_CENTER",
    "volume_up":    "KEYCODE_VOLUME_UP",
    "volume_down":  "KEYCODE_VOLUME_DOWN",
    "mute":         "KEYCODE_VOLUME_MUTE",
}


class FireTVClient:
    def __init__(self, ip: str | None = None):
        self._ip = ip or ""
        self._target = f"{self._ip}:5555" if self._ip else ""

    async def _adb(self, *args) -> str:
        if not self._target:
            raise RuntimeError("FireTV: no target IP")
        cmd = ["adb", "-s", self._target] + list(args)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=8)
        out = stdout.decode().strip()
        err = stderr.decode().strip()
        if proc.returncode != 0 and not out:
            raise RuntimeError(err or f"adb {args[0]} failed")
        return out

    async def _discover_ip(self) -> str | None:
        """Scan subnet for anything listening on port 5555 (Fire TV ADB port)."""
        import socket

        # First check already-connected adb devices
        try:
            proc = await asyncio.create_subprocess_exec(
                "adb", "devices",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            for line in stdout.decode().splitlines():
                if "\tdevice" in line:
                    addr = line.split("\t")[0]
                    if ":" in addr:
                        return addr.split(":")[0]
        except Exception:
            pass

        # Active scan: probe all .1–.254 on port 5555 concurrently
        print("[FireTV] Scanning subnet for port 5555...")

        async def _probe(ip: str) -> str | None:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, 5555), timeout=0.3
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return ip
            except Exception:
                return None

        # Derive subnet from our own IP
        try:
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            prefix = ".".join(local_ip.split(".")[:3])
        except Exception:
            prefix = "192.168.88"

        tasks = [_probe(f"{prefix}.{i}") for i in range(1, 255)]
        results = await asyncio.gather(*tasks)
        candidates = [ip for ip in results if ip]

        for ip in candidates:
            try:
                out = await self._adb_raw_connect(f"{ip}:5555")
                if "connected" in out or "already connected" in out:
                    print(f"[FireTV] Discovered at {ip}")
                    return ip
            except Exception:
                pass
        return None

    async def _adb_raw_connect(self, target: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "adb", "connect", target,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8)
        return stdout.decode().strip()

    async def connect(self) -> bool:
        try:
            if self._ip:
                out = await self._adb_connect()
                if "connected" in out or "already connected" in out:
                    if await self._is_authorized():
                        return True
                    print("[FireTV] connected but unauthorized — check Fire TV developer settings")
                    return False
            # IP missing or connect failed — discover
            ip = await self._discover_ip()
            if ip:
                self._ip = ip
                self._target = f"{ip}:5555"
                return await self._is_authorized()
            return False
        except Exception as e:
            print(f"[FireTV] connect failed: {e}")
            return False

    async def reconnect(self) -> bool:
        """Re-run discovery and connect. Called after sustained auth failures."""
        self._ip = None
        self._target = None
        return await self.connect()

    async def _is_authorized(self) -> bool:
        """Return True if device is connected and authorized (not unauthorized)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "adb", "devices",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            text = out.decode()
            for line in text.splitlines():
                if self._target and self._target in line:
                    return "unauthorized" not in line
            return False
        except Exception:
            return False

    async def _adb_connect(self) -> str:
        return await self._adb_raw_connect(self._target)

    async def get_state(self) -> dict:
        """Returns {on: bool, app: str}"""
        try:
            power = await self._adb("shell", "dumpsys", "power")
            on = "mWakefulness=Awake" in power
            app = ""
            if on:
                focus = await self._adb("shell", "dumpsys", "window")
                for line in focus.splitlines():
                    if "mCurrentFocus" in line:
                        # extract package name
                        parts = line.strip().split()
                        if len(parts) >= 3:
                            app = parts[-1].split("/")[0]
                        break
            return {"on": on, "app": app}
        except Exception as e:
            print(f"[FireTV] get_state error: {e}")
            return {"on": False, "app": ""}

    async def get_playback_state(self) -> str:
        """Return 'playing' | 'paused' | 'stopped' | 'none'.
        Reads `dumpsys media_session` and parses the active session's PlaybackState.
        Works for any app with a registered media session (Prime, Netflix, YouTube,
        Apple TV+, Plex, Disney+, Hotstar, Spotify, etc.)."""
        try:
            out = await self._adb("shell", "dumpsys", "media_session")
        except Exception as e:
            print(f"[FireTV] media_session read failed: {e}")
            return "none"
        import re as _re
        # Each PlaybackState entry has state=N and updated=T. Multiple sessions
        # may be reported; we want the one with the highest `updated` value
        # (most recently active). Filter out updated=0 (stale/dormant sessions).
        entries = _re.findall(
            r"state=PlaybackState \{state=(\d+),[^}]*?updated=(\d+)",
            out or "",
        )
        if not entries:
            return "none"
        # Sort by `updated` desc, ignore entries with updated=0 if any non-zero exists
        parsed = [(int(s), int(u)) for s, u in entries]
        non_zero = [p for p in parsed if p[1] > 0]
        candidates = non_zero if non_zero else parsed
        candidates.sort(key=lambda x: -x[1])
        latest_code = candidates[0][0]
        return {3: "playing", 2: "paused", 1: "stopped"}.get(latest_code, "none")

    async def wake(self) -> bool:
        """Wake Fire TV reliably — WAKEUP alone often doesn't trigger HDMI handshake.

        Sequence:
          1. Check current wakefulness via dumpsys.
          2. If asleep: send KEYCODE_WAKEUP, wait, then KEYCODE_HOME to force the
             home screen (which forces HDMI signal output on most TVs).
          3. Verify with dumpsys; if still asleep, retry once.
        Returns True once mWakefulness=Awake, False if it never wakes.
        """
        async def _is_awake() -> bool:
            try:
                out = await self._adb("shell", "dumpsys", "power")
                return "mWakefulness=Awake" in out
            except Exception:
                return False

        try:
            if await _is_awake():
                # Already on — just nudge to home so HDMI re-handshakes if TV slept
                await self._adb("shell", "input", "keyevent", "KEYCODE_HOME")
                return True

            for attempt in range(2):
                await self._adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
                await asyncio.sleep(0.6)
                # HOME forces home screen + HDMI signal output
                await self._adb("shell", "input", "keyevent", "KEYCODE_HOME")
                await asyncio.sleep(0.4)
                if await _is_awake():
                    return True

            print("[FireTV] wake: still asleep after 2 attempts")
            return False
        except Exception as e:
            print(f"[FireTV] wake error: {e}")
            return False

    async def sleep(self) -> bool:
        try:
            await self._adb("shell", "input", "keyevent", "KEYCODE_SLEEP")
            return True
        except Exception as e:
            print(f"[FireTV] sleep error: {e}")
            return False

    async def launch_app(self, app_name: str) -> bool:
        activity = APPS.get(app_name.lower())
        if not activity:
            print(f"[FireTV] Unknown app: {app_name}")
            return False
        try:
            pkg, act = activity.split("/")
            await self._adb("shell", "am", "start", "-n", activity)
            return True
        except Exception as e:
            print(f"[FireTV] launch_app error: {e}")
            return False

    async def search(self, app_name: str, query: str) -> bool:
        """Search for content on a specific app."""
        app_name = app_name.lower()
        try:
            if app_name == "netflix":
                await self._adb("shell", "am", "start", "-a", "android.intent.action.VIEW",
                                 "-d", f"netflix://search?q={query.replace(' ', '+')}")
            elif app_name in ("prime", "prime video"):
                await self._adb("shell", "am", "start", "-a", "android.intent.action.VIEW",
                                 "-d", f"amzn://apps/android?s={query.replace(' ', '+')}")
            elif app_name == "youtube":
                await self._adb("shell", "am", "start", "-a", "android.intent.action.SEARCH",
                                 "--es", "query", query,
                                 "-n", "com.amazon.firetv.youtube/com.amazon.firetv.youtube.app.YouTubeActivity")
            else:
                return False
            return True
        except Exception as e:
            print(f"[FireTV] search error: {e}")
            return False

    async def keypress(self, key: str) -> bool:
        keycode = KEYCODES.get(key.lower())
        if not keycode:
            print(f"[FireTV] Unknown key: {key}")
            return False
        try:
            await self._adb("shell", "input", "keyevent", keycode)
            return True
        except Exception as e:
            print(f"[FireTV] keypress error: {e}")
            return False

    async def _ensure_connected(self) -> bool:
        """Make sure ADB is connected, reconnect if needed."""
        if self._target:
            try:
                out = await self._adb("get-state")
                if "device" in out:
                    return True
            except Exception:
                pass
        return await self.connect()

    async def global_play(self, query: str) -> bool:
        """
        Trigger Alexa cross-app search — same as pressing the voice button and saying the query.
        Opens VoiceAssistantActivity, types the query into the text input, submits.
        Alexa resolves it and launches the right app (JioHotstar, Netflix, etc.) automatically.
        """
        if not await self._ensure_connected():
            return False
        try:
            state = await self.get_state()
            if not state["on"]:
                await self.wake()
                await asyncio.sleep(2)

            # Open Alexa VoiceAssistantActivity (same as pressing mic on remote)
            await self._adb("shell", "input", "keyevent", "KEYCODE_SEARCH")
            await asyncio.sleep(2)  # let activity fully load + keyboard appear

            # Type the query into Alexa's text input field
            # Escape spaces with %s for ADB input text
            escaped = query.replace(" ", "%s")
            await self._adb("shell", "input", "text", escaped)
            await asyncio.sleep(0.5)

            # Submit — Enter triggers Alexa NLU → cross-app launch
            await self._adb("shell", "input", "keyevent", "KEYCODE_ENTER")
            return True
        except Exception as e:
            print(f"[FireTV] global_play error: {e}")
            return False
