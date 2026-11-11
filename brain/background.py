"""Adapters around copied reflection logic; no microphone or PCM interface."""
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import time
import web


class Outbound:
    def __init__(self, config, ha, busy):
        self.config = config
        self.ha = ha
        self.busy = busy

    async def speak(self, message):
        return await self.deliver(message)

    async def deliver(self, message, interactive=False, priority="notification", target=None):
        target = target or self.config.default_satellite
        await web.emit({"type": "notification", "text": message, "priority": priority})
        reason = None
        if not self.config.outbound_speech_enabled:
            reason = "Outbound speech disabled; message retained in the dashboard."
        elif self.busy():
            reason = "Endpoint is busy; no active voice session was interrupted."
        else:
            hour = datetime.now(ZoneInfo(self.config.timezone)).hour
            quiet = (self.config.quiet_start <= hour < self.config.quiet_end
                     if self.config.quiet_start < self.config.quiet_end
                     else hour >= self.config.quiet_start or hour < self.config.quiet_end)
            if quiet and priority not in ("alarm", "timer"):
                reason = "Quiet hours; message retained in the dashboard."
        if reason:
            return reason
        state = await self.ha.get_state(target)
        if state.get("state") != "idle":
            return "Satellite is unavailable or busy; no speech sent."
        return await self.ha.call_service("assist_satellite", "start_conversation" if interactive else "announce",
                    {"entity_id": target, "start_message" if interactive else "message": message,
                     "preannounce": priority in ("alarm", "timer")})
