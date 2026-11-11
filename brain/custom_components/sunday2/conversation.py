"""Forward HA text to Sunday; reuse the initiating Assist TTS stream."""
import asyncio
import json
import uuid
from typing import Literal
from aiohttp import WSMsgType
from homeassistant.components import conversation
from homeassistant.const import MATCH_ALL
from homeassistant.helpers import intent, llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([SundayConversation(entry)])


class SundayConversation(conversation.ConversationEntity):
    _attr_name = "Sunday 2.0"
    _attr_supports_streaming = True
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, entry):
        self.entry = entry
        self._attr_unique_id = entry.entry_id

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return MATCH_ALL

    async def _async_handle_message(self, user_input, chat_log):
        request_id = uuid.uuid4().hex
        session = async_get_clientsession(self.hass)
        final = None
        role = None
        last_sequence = 0
        url = self.entry.data["url"].rstrip("/") + "/v1/turns"
        try:
            async with asyncio.timeout(125):
                async with session.ws_connect(url, heartbeat=30,
                        headers={"Authorization": "Bearer " + self.entry.data["token"]}) as ws:
                    await ws.send_json({"type": "begin_turn", "protocol": 1,
                        "request_id": request_id, "conversation_id": chat_log.conversation_id,
                        "text": user_input.text, "language": user_input.language,
                        "satellite_id": user_input.satellite_id, "device_id": user_input.device_id,
                        "source": "ha", "context": user_input.context.as_dict(),
                        "extra_system_prompt": user_input.extra_system_prompt})

                    async def deltas():
                        nonlocal final, role, last_sequence
                        async for msg in ws:
                            if msg.type != WSMsgType.TEXT:
                                if msg.type in (WSMsgType.ERROR, WSMsgType.CLOSED, WSMsgType.CLOSE):
                                    break
                                continue
                            event = json.loads(msg.data)
                            if event.get("request_id") != request_id or event.get("protocol") != 1:
                                raise RuntimeError("Sunday protocol correlation mismatch")
                            sequence = event.get("sequence", 0)
                            if sequence != last_sequence + 1:
                                raise RuntimeError("Sunday stream sequence mismatch")
                            last_sequence = sequence
                            kind = event.get("type")
                            if kind == "assistant_text":
                                delta = {"content": event["text"]}
                                if role != "assistant":
                                    delta["role"] = "assistant"
                                    role = "assistant"
                                yield delta
                            elif kind == "tool_start":
                                if role != "assistant":
                                    yield {"role": "assistant"}
                                    role = "assistant"
                                yield {"tool_calls": [llm.ToolInput(id=event["call_id"],
                                    tool_name=event["name"], tool_args=event["arguments"], external=True)]}
                            elif kind == "tool_result":
                                role = "tool_result"
                                yield {"role": "tool_result", "tool_call_id": event["call_id"],
                                    "tool_name": event["name"],
                                    "result": llm.ToolResult(data={"result": event["result"]})}
                            elif kind == "final":
                                final = event
                                return
                            elif kind in ("error", "cancelled"):
                                raise RuntimeError(event.get("error", "Sunday turn cancelled"))

                    try:
                        async for _ in chat_log.async_add_delta_content_stream(user_input.agent_id, deltas()):
                            pass
                    finally:
                        if final is None and not ws.closed:
                            await ws.send_json({"type": "cancel", "request_id": request_id})
                    if final is None:
                        raise RuntimeError("Sunday connection ended without a final response")
        except asyncio.CancelledError:
            raise
        except Exception:
            response = intent.IntentResponse(language=user_input.language)
            response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN,
                                    "Sunday couldn't finish that turn. Please try again.")
            return conversation.ConversationResult(response=response,
                conversation_id=chat_log.conversation_id, continue_conversation=False)
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(final["text"])
        return conversation.ConversationResult(response=response,
            conversation_id=chat_log.conversation_id,
            continue_conversation=bool(final.get("continue_conversation")))
