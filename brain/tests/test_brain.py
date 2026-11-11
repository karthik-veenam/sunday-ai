"""Meaningful silent checks: capability limits, isolation, cancellation and protocol."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ha import control
from jobs import Jobs
from output import TurnOutput, current_output, spawn_task
from config import Config
from agent import _gemini_failure_message, _OpenAIBackend
from runtime import Runtime
import runtime as runtime_module
import tools
import wiki_io


class FakeHA:
    def __init__(self, state="off", modes=None):
        self.state = state
        self.modes = modes or ["onoff"]
        self.calls = []

    async def get_state(self, eid):
        return {"entity_id": eid, "state": self.state,
                "attributes": {"supported_color_modes": self.modes}}

    async def call_service(self, domain, service, data):
        self.calls.append((domain, service, data))
        return "ok"


class FakeAgent:
    count = 0
    def __init__(self):
        self.seen = []
    def reset(self):
        self.seen.clear()
    async def process(self, text, output):
        FakeAgent.count += 1
        self.seen.append(text)
        await output.speak("I heard " + text + ".")
        return text.endswith("?")


class BrainTests(unittest.IsolatedAsyncioTestCase):
    async def test_historical_wiki_is_not_retrieved(self):
        backend = object.__new__(_OpenAIBackend)
        with patch.object(wiki_io, 'context_snapshot', side_effect=AssertionError('Historical read')):
            await backend._refresh_turn_context('What lighting do I prefer?')
        self.assertEqual('', backend._turn_context)

    async def test_quota_error_is_distinguished_from_credentials_and_busy(self):
        daily = RuntimeError("429 RESOURCE_EXHAUSTED GenerateRequestsPerDayPerProjectPerModel-FreeTier")
        self.assertIn("daily request limit", _gemini_failure_message(daily))
        self.assertIn("credentials", _gemini_failure_message(RuntimeError("403 API_KEY_INVALID")))
        self.assertIn("temporarily busy", _gemini_failure_message(RuntimeError("503 high demand")))

    async def test_unsupported_brightness_sends_no_action(self):
        client = FakeHA()
        result = await control(client, "cove", "on", brightness=50)
        self.assertIn("only on/off", result)
        self.assertEqual([], client.calls)

    async def test_unavailable_device_sends_no_action(self):
        client = FakeHA("unavailable")
        result = await control(client, "panels", "on")
        self.assertIn("unavailable", result)
        self.assertEqual([], client.calls)

    async def test_supported_zero_saturation_preserved(self):
        client = FakeHA(modes=["hs"])
        await control(client, "moon", "on", hue=10, saturation=0)
        self.assertEqual([10, 0], client.calls[0][2]["hs_color"])

    async def test_tool_acknowledgement_precedes_dispatch(self):
        events = []
        async def emit(event): events.append(event)
        async def execute():
            self.assertEqual("tool_start", events[-1]["type"])
            return "done"
        output = TurnOutput("id", emit)
        await output.speak("Checking.")
        await output.tool("get_devices", {}, execute)
        self.assertEqual(["assistant_text", "tool_start", "tool_result"], [e["type"] for e in events])

    async def test_cancelled_output_cleans_detached_tasks(self):
        output = TurnOutput("id", lambda e: None)
        token = current_output.set(output)
        task = spawn_task(asyncio.sleep(100))
        await output.close()
        current_output.reset(token)
        self.assertTrue(task.cancelled())

    async def test_timer_survives_restart_without_replaying_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            async def deliver(*args, **kwargs): return "ok"
            path = Path(d) / "jobs.db"
            job = Jobs(path, deliver)
            job.timer(60)
            job_id = job.list()[0]["id"]
            job.db.execute("UPDATE jobs SET status='delivering'")
            job.db.commit();job.close()
            restarted = Jobs(path, deliver)
            self.assertEqual("unknown", restarted.list()[0]["status"])
            self.assertEqual(job_id, restarted.list()[0]["id"])
            restarted.close()

    async def test_invalid_device_arguments_are_rejected(self):
        result = await tools.execute("control_device", {"device": "moon", "action": "destroy"})
        self.assertIn("unavailable", result)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "state").mkdir()
        (self.root / "household.json").write_text(json.dumps({"primary_area":"bedroom"}))
        self.patch = patch.object(runtime_module, "ROOT", self.root)
        self.patch.start()
        self.runtime = Runtime(Config(openai_api_key="fake", daemon_token="test"), agent_factory=FakeAgent)
        from unittest.mock import AsyncMock
        self.runtime.home.context = AsyncMock(return_value="{}")
        self.events = []
    async def asyncTearDown(self):
        await self.runtime.close()
        self.patch.stop();self.temp.cleanup()
    async def emit(self, event): self.events.append(event)

    async def test_histories_and_followup_are_isolated(self):
        await self.runtime.turn({"request_id": "1", "conversation_id": "a", "text": "hello?"}, self.emit)
        await self.runtime.turn({"request_id": "2", "conversation_id": "b", "text": "hi"}, self.emit)
        self.assertEqual(["hello?"], self.runtime.agents["a"].seen)
        self.assertEqual(["hi"], self.runtime.agents["b"].seen)
        final = [e for e in self.events if e["type"] == "final"]
        self.assertEqual([True, False], [e["continue_conversation"] for e in final])

    async def test_duplicate_request_replays_result_not_brain(self):
        request = {"request_id": "one", "conversation_id": "a", "text": "hello"}
        await self.runtime.turn(request, self.emit)
        before = FakeAgent.count
        await self.runtime.turn(request, self.emit)
        self.assertEqual(before, FakeAgent.count)

        self.assertTrue(self.events[-1]["replayed"])
        self.assertTrue((self.root / "state/receipts.json").exists())

    async def test_phrase_route_skips_model_and_replay_skips_dispatch(self):
        from unittest.mock import AsyncMock
        self.runtime.phrase_automations.attempt = AsyncMock(return_value={
            'id':'phrase-test','handled':True,'decision':'PHRASE_AUTOMATION','reply':'On it.'})
        before = FakeAgent.count
        request={'request_id':'phrase-test','source':'voice','text':'my own phrase'}
        await self.runtime.turn(request,self.emit)
        await self.runtime.turn(request,self.emit)
        self.runtime.phrase_automations.attempt.assert_awaited_once()
        self.assertEqual(before,FakeAgent.count)
        self.assertEqual(self.events[-1]['model_calls'],0)
        self.assertTrue(self.events[-1]['replayed'])

    async def test_reused_id_with_different_input_rejected(self):
        await self.runtime.turn({"request_id": "one", "text": "hello"}, self.emit)
        with self.assertRaises(ValueError):
            await self.runtime.turn({"request_id": "one", "text": "different"}, self.emit)

    async def test_goodbye_resets_without_llm(self):
        before = FakeAgent.count
        await self.runtime.turn({"request_id": "bye", "text": "goodbye"}, self.emit)
        self.assertFalse(self.events[-1]["continue_conversation"])
        self.assertEqual(before, FakeAgent.count)

    async def test_busy_endpoint_rejected(self):
        self.runtime.active["satellite"] = "first"
        with self.assertRaises(RuntimeError):
            await self.runtime.turn({"text": "hello", "satellite_id": "satellite"}, self.emit)
        self.runtime.active.clear()

    async def test_invalid_protocol_and_input_rejected(self):
        for request in ({"protocol": 2, "text": "hello"}, {"text": []},
                        {"text": "hello", "request_id": []}, {"text": "hello", "satellite_id": ["wrong"]}):
            with self.assertRaises(ValueError):
                await self.runtime.turn(request, self.emit)

    async def test_incomplete_receipt_is_not_replayed(self):
        request = {"request_id": "interrupted", "text": "hello"}
        import hashlib
        fingerprint = hashlib.sha256(json.dumps({k: request.get(k) for k in
                    ("text", "conversation_id", "satellite_id", "source")}, sort_keys=True).encode()).hexdigest()
        self.runtime.completed["interrupted"] = {"fingerprint": fingerprint, "status": "started"}
        before = FakeAgent.count
        with self.assertRaisesRegex(ValueError, "outcomes need review"):
            await self.runtime.turn(request, self.emit)
        self.assertEqual(before, FakeAgent.count)


class WikiIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_cannot_escape_new_memory_directory(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wiki = root / "wiki"
            wiki.mkdir()
            (wiki / "SCHEMA.md").write_text("Schema")
            (wiki / "INDEX.md").write_text("Index")
            reference = root / "original.md"
            reference.write_text("untouched")
            async def create(**kwargs):
                plan = {"action": "edit", "page": "../original.md", "patch": "changed"}
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(plan)))])
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            with patch.object(wiki_io, "WIKI_DIR", wiki), patch.object(wiki_io, "_client", client):
                result = await wiki_io.save_to_wiki("A correction")
            self.assertEqual("error", result["action"])
            self.assertEqual("untouched", reference.read_text())

    async def test_memory_save_writes_only_own_snapshot(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            wiki = Path(directory)
            (wiki / "SCHEMA.md").write_text("Schema")
            (wiki / "INDEX.md").write_text("Index")
            async def create(**kwargs):
                plan = {"action": "create", "page": "preferences.md", "patch": "Keep the moon off for movies.", "reason": "Correction"}
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(plan)))])
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            with patch.object(wiki_io, "WIKI_DIR", wiki), patch.object(wiki_io, "_client", client):
                result = await wiki_io.save_to_wiki("Keep the moon off for movies.")
            self.assertEqual("create", result["action"])
            self.assertIn("moon off", (wiki / "preferences.md").read_text())


if __name__ == "__main__":
    unittest.main()
