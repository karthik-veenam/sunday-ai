"""Run against the installed HA libraries in an isolated interpreter, silently."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch
from aiohttp import WSMsgType
from homeassistant.components.conversation import ChatLog, ConversationInput
from homeassistant.core import Context
from sunday2.conversation import SundayConversation
import json


class FakeSocket:
    closed = False
    def __init__(self, mode="normal"):
        self.sent = []
        self.mode = mode
    async def send_json(self, data): self.sent.append(data)
    async def __aenter__(self): return self
    async def __aexit__(self, *args): self.closed = True
    def __aiter__(self): return self.events()
    async def events(self):
        rid = self.sent[0]["request_id"]
        events = [
            {"type": "started"},
            {"type": "assistant_text", "text": "Checking. "},
            {"type": "tool_start", "call_id": "call1", "name": "get_devices", "arguments": {}},
            {"type": "tool_result", "call_id": "call1", "name": "get_devices", "result": "Lights off"},
            {"type": "assistant_text", "text": "Lights are off. Anything else? "},
            {"type": "final", "text": "Checking. Lights are off. Anything else?", "continue_conversation": True},
        ]
        if self.mode == "disconnect": events = events[:2]
        for sequence, event in enumerate(events, 1):
            yield SimpleNamespace(type=WSMsgType.TEXT, data=json.dumps({"protocol": 1,
                          "request_id": rid, "sequence": sequence, **event}))


async def run():
    for mode in ("normal", "disconnect"):
        socket = FakeSocket(mode)
        client = SimpleNamespace(ws_connect=lambda *a, **k: socket)
        entity = SundayConversation(SimpleNamespace(entry_id="entry", data={"url": "http://test", "token": "fake"}))
        entity.hass = SimpleNamespace(data={})
        deltas = []
        chat_log = ChatLog(hass=entity.hass, conversation_id="conversation", delta_listener=lambda c,d: deltas.append(d))
        user_input = ConversationInput(text="Check lights", context=Context(), conversation_id="conversation",
                device_id="device", satellite_id="assist_satellite.test", language="en", agent_id="conversation.sunday2")
        with patch("sunday2.conversation.async_get_clientsession", return_value=client):
            result = await entity._async_handle_message(user_input, chat_log)
        assert socket.sent[0]["satellite_id"] == "assist_satellite.test"
        if mode == "normal":
            assert result.continue_conversation is True
            assert [c.role for c in chat_log.content] == ["system", "assistant", "tool_result", "assistant"]
            assert chat_log.content[1].tool_calls[0].external is True
            assert [d["content"] for d in deltas if d.get("content")] == ["Checking. ", "Lights are off. Anything else? "]
            assert len(socket.sent) == 1
        else:
            assert result.continue_conversation is False
            assert socket.sent[-1]["type"] == "cancel"
        print("Installed HA adapter check:", mode, "PASS")


asyncio.run(run())
