"""Tests for the Sunday prompt editor (prompt_editor.py). Sandboxed: temp prompt dir, fake brain, fake restart command.
    python -m unittest test_prompt_editor
"""
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
import prompt_editor as P  # noqa: E402

H = {"X-Requested-With": "sunday-editor"}


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        (t / "docs").mkdir()
        (t / "docs" / "SYSTEM_PROMPT.md").write_text("You are Sunday.\nBe kind.\n")
        (t / "docs" / "VOICE_PROMPT.md").write_text("Speak briefly.\n")
        os.chmod(t / "docs" / "SYSTEM_PROMPT.md", 0o640)
        self.brain = {"status": "ready", "active_turns": 0}
        brain_app = web.Application()
        async def health(r): return web.json_response(self.brain)
        brain_app.router.add_get("/health", health)
        self.brain_srv = TestServer(brain_app); await self.brain_srv.start_server()
        self.restarts = []
        cfg = {"prompt_dir": t / "docs", "history_dir": t / "hist", "token_file": t / "token", "host": "127.0.0.1", "port": 0,
               "brain_health": "http://127.0.0.1:%d/health" % self.brain_srv.port, "brain_unit": "x.service",
               "restart_cmd": ["true"], "brain_started_override": None, "auth": "token", "allowed_hosts": {"localhost"}}
        self.cfg = cfg
        self.token = P.ensure_token(cfg["token_file"])
        self.srv = TestServer(P.make_app(cfg)); await self.srv.start_server()
        self.c = TestClient(self.srv); await self.c.start_server()

    async def asyncTearDown(self):
        await self.c.close(); await self.brain_srv.close(); self.tmp.cleanup()

    async def login(self):
        r = await self.c.post("/login", json={"token": self.token}, headers=H)
        self.assertEqual(r.status, 200)

    def sysfile(self):
        return self.cfg["prompt_dir"] / "SYSTEM_PROMPT.md"

    async def etag(self, name="SYSTEM_PROMPT.md"):
        return (await (await self.c.get("/api/file", params={"name": name})).json())["etag"]


class NoLogin(unittest.IsolatedAsyncioTestCase):
    """Default mode: no login, but only the home network, only this machine's own host names, and no cross-site requests."""
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); t = Path(self.tmp.name); (t / "docs").mkdir()
        (t / "docs" / "SYSTEM_PROMPT.md").write_text("A\n"); (t / "docs" / "VOICE_PROMPT.md").write_text("B\n")
        cfg = {"prompt_dir": t / "docs", "history_dir": t / "hist", "token_file": t / "token", "host": "127.0.0.1", "port": 0,
               "brain_health": "http://127.0.0.1:1/health", "brain_unit": "x", "restart_cmd": ["true"], "brain_started_override": None,
               "auth": "none", "allowed_hosts": {"localhost", "djpi", "djpi.local"}}
        self.cfg = cfg; self.srv = TestServer(P.make_app(cfg)); await self.srv.start_server(); self.c = TestClient(self.srv); await self.c.start_server()

    async def asyncTearDown(self):
        await self.c.close(); self.tmp.cleanup()

    async def test_api_works_without_a_login_and_no_token_file_is_created(self):
        self.assertEqual((await self.c.get("/api/files")).status, 200)
        self.assertFalse(self.cfg["token_file"].exists())

    async def test_edit_and_save_without_a_login(self):
        j = await (await self.c.get("/api/file", params={"name": "SYSTEM_PROMPT.md"})).json()
        r = await self.c.post("/api/save", json={"name": "SYSTEM_PROMPT.md", "content": "A2\n", "etag": j["etag"]}, headers=H)
        self.assertEqual(r.status, 200); self.assertEqual((Path(self.tmp.name) / "docs" / "SYSTEM_PROMPT.md").read_text(), "A2\n")

    async def test_dns_rebinding_host_names_are_refused(self):
        for host in ["evil.example.com", "evil.example.com:8095", "8.8.8.8", "djpi.evil.com"]:
            r = await self.c.get("/api/files", headers={"Host": host}); self.assertEqual(r.status, 403, host)

    async def test_home_network_names_and_addresses_are_accepted(self):
        for host in ["192.168.88.108:8095", "djpi.local:8095", "djpi", "localhost:8095", "10.0.0.5"]:
            r = await self.c.get("/api/files", headers={"Host": host}); self.assertEqual(r.status, 200, host)

    async def test_cross_site_and_headerless_posts_are_refused(self):
        body = {"name": "SYSTEM_PROMPT.md", "content": "x", "etag": "e"}
        self.assertEqual((await self.c.post("/api/save", json=body)).status, 403)                                       # no custom header
        self.assertEqual((await self.c.post("/api/save", json=body, headers=dict(H, Origin="http://evil.example.com"))).status, 403)

    async def test_traversal_is_still_refused(self):
        self.assertEqual((await self.c.get("/api/file", params={"name": "../token"})).status, 400)

    def test_only_private_addresses_may_connect(self):
        for ok in ["192.168.88.8", "10.1.2.3", "172.16.0.9", "127.0.0.1", "::1", "::ffff:192.168.1.2", "fe80::1"]:
            self.assertTrue(P.remote_allowed(ok), ok)
        for bad in ["8.8.8.8", "93.184.216.34", "1.1.1.1", "2001:4860:4860::8888", "", None, "not-an-ip"]:
            self.assertFalse(P.remote_allowed(bad), bad)


class Auth(Base):
    async def test_page_is_served_but_the_api_needs_a_login(self):
        self.assertEqual((await self.c.get("/")).status, 200)
        self.assertEqual((await self.c.get("/api/files")).status, 401)
        self.assertEqual((await self.c.post("/api/save", json={}, headers=H)).status, 401)

    async def test_wrong_token_then_lockout_then_right_token(self):
        for _ in range(5):
            r = await self.c.post("/login", json={"token": "nope"}, headers=H); self.assertEqual(r.status, 401)
        self.assertEqual((await self.c.post("/login", json={"token": self.token}, headers=H)).status, 429)   # locked even for the right token

    async def test_login_gives_an_httponly_strict_cookie(self):
        r = await self.c.post("/login", json={"token": self.token}, headers=H)
        c = r.headers["Set-Cookie"]; self.assertIn("HttpOnly", c); self.assertIn("SameSite=Strict", c)
        self.assertEqual((await self.c.get("/api/files")).status, 200)

    async def test_state_changing_calls_need_the_csrf_header(self):
        await self.login()
        r = await self.c.post("/api/save", json={"name": "SYSTEM_PROMPT.md", "content": "x", "etag": "y"})     # no X-Requested-With
        self.assertEqual(r.status, 403)

    async def test_logout_ends_the_session(self):
        await self.login(); await self.c.post("/logout", headers=H)
        self.assertEqual((await self.c.get("/api/files")).status, 401)

    async def test_token_file_is_private(self):
        self.assertEqual(stat.S_IMODE(self.cfg["token_file"].stat().st_mode), 0o600)


class Files(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp(); await self.login()

    async def test_only_the_two_prompt_files_exist_for_the_editor(self):
        j = await (await self.c.get("/api/files")).json()
        self.assertEqual([f["name"] for f in j["files"]], ["SYSTEM_PROMPT.md", "VOICE_PROMPT.md"])

    async def test_path_traversal_and_unknown_names_are_refused(self):
        for bad in ["../token", "../../etc/passwd", "SYSTEM_PROMPT.md/../VOICE_PROMPT.md", "/etc/passwd", "agent.py", "", "SYSTEM_PROMPT.md\x00.txt"]:
            r = await self.c.get("/api/file", params={"name": bad}); self.assertEqual(r.status, 400, bad)
            r = await self.c.post("/api/save", json={"name": bad, "content": "x", "etag": "e"}, headers=H); self.assertEqual(r.status, 400, bad)
            r = await self.c.get("/api/history", params={"name": bad}); self.assertEqual(r.status, 400, bad)

    async def test_applied_flag_compares_with_when_the_brain_started(self):
        self.cfg["brain_started_override"] = self.sysfile().stat().st_mtime + 100      # brain started AFTER the last edit
        j = await (await self.c.get("/api/files")).json(); self.assertTrue(all(f["applied"] for f in j["files"]))
        self.cfg["brain_started_override"] = self.sysfile().stat().st_mtime - 100      # edited AFTER the brain started
        j = await (await self.c.get("/api/files")).json(); self.assertFalse(j["files"][0]["applied"])


class Saving(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp(); await self.login()

    async def save(self, content, etag=None, name="SYSTEM_PROMPT.md"):
        return await self.c.post("/api/save", json={"name": name, "content": content, "etag": etag or await self.etag(name)}, headers=H)

    async def test_save_writes_the_file_and_keeps_the_previous_version(self):
        r = await self.save("You are Sunday.\nBe kind and brief.\n"); j = await r.json()
        self.assertEqual(r.status, 200); self.assertTrue(j["ok"])
        self.assertEqual(self.sysfile().read_text(), "You are Sunday.\nBe kind and brief.\n")
        v = await (await self.c.get("/api/history", params={"name": "SYSTEM_PROMPT.md"})).json()
        self.assertEqual(len(v["versions"]), 1)
        self.assertEqual((self.cfg["history_dir"] / "SYSTEM_PROMPT.md" / (v["versions"][0]["id"] + ".md")).read_text(), "You are Sunday.\nBe kind.\n")

    async def test_unchanged_save_creates_no_backup(self):
        r = await self.save("You are Sunday.\nBe kind.\n"); self.assertTrue((await r.json())["unchanged"])
        self.assertEqual((await (await self.c.get("/api/history", params={"name": "SYSTEM_PROMPT.md"})).json())["versions"], [])

    async def test_a_file_changed_behind_your_back_is_not_overwritten(self):
        et = await self.etag()
        self.sysfile().write_text("Edited by another agent.\n")                  # someone else saves first
        r = await self.save("My edit.\n", etag=et)
        self.assertEqual(r.status, 409); self.assertIn("changed on disk", (await r.json())["error"])
        self.assertEqual(self.sysfile().read_text(), "Edited by another agent.\n")

    async def test_validation(self):
        for bad in ["", "   \n  ", "a\x00b", "x" * (P.MAX_BYTES + 10)]:
            r = await self.save(bad); self.assertEqual(r.status, 400, repr(bad[:10]))
        self.assertEqual(self.sysfile().read_text(), "You are Sunday.\nBe kind.\n")

    async def test_windows_line_endings_are_normalised_and_mode_is_kept(self):
        await self.save("line one\r\nline two\r\n")
        self.assertEqual(self.sysfile().read_bytes(), b"line one\nline two\n")
        self.assertEqual(stat.S_IMODE(self.sysfile().stat().st_mode), 0o640)

    async def test_no_temp_file_is_left_behind(self):
        await self.save("new\n")
        self.assertEqual(sorted(p.name for p in self.cfg["prompt_dir"].iterdir()), ["SYSTEM_PROMPT.md", "VOICE_PROMPT.md"])

    async def test_history_is_private(self):
        await self.save("new\n"); self.assertEqual(stat.S_IMODE(self.cfg["history_dir"].stat().st_mode), 0o700)


class History(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp(); await self.login()
        et = await self.etag()
        await self.c.post("/api/save", json={"name": "SYSTEM_PROMPT.md", "content": "Version two.\n", "etag": et}, headers=H)
        self.vid = (await (await self.c.get("/api/history", params={"name": "SYSTEM_PROMPT.md"})).json())["versions"][0]["id"]

    async def test_diff_shows_the_changes(self):
        d = (await (await self.c.get("/api/diff", params={"name": "SYSTEM_PROMPT.md", "id": self.vid})).json())["diff"]
        self.assertIn("-Be kind.", d); self.assertIn("+Version two.", d)

    async def test_restore_brings_back_the_old_text_and_backs_up_the_current(self):
        r = await self.c.post("/api/restore", json={"name": "SYSTEM_PROMPT.md", "id": self.vid, "etag": await self.etag()}, headers=H)
        self.assertEqual(r.status, 200); self.assertEqual(self.sysfile().read_text(), "You are Sunday.\nBe kind.\n")
        v = (await (await self.c.get("/api/history", params={"name": "SYSTEM_PROMPT.md"})).json())["versions"]
        self.assertEqual(len(v), 2)                                                       # "Version two" was kept too

    async def test_restore_refuses_when_the_file_changed(self):
        r = await self.c.post("/api/restore", json={"name": "SYSTEM_PROMPT.md", "id": self.vid, "etag": "stale"}, headers=H)
        self.assertEqual(r.status, 409); self.assertEqual(self.sysfile().read_text(), "Version two.\n")

    async def test_bad_version_ids(self):
        for bad in ["../x", "a/b", "nope", ""]:
            r = await self.c.get("/api/diff", params={"name": "SYSTEM_PROMPT.md", "id": bad}); self.assertIn(r.status, (400, 404), bad)


class Apply(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp(); await self.login()
        marker = Path(self.tmp.name) / "restarted"
        self.cfg["restart_cmd"] = ["touch", str(marker)]; self.marker = marker

    async def test_restarts_the_brain_when_idle(self):
        r = await self.c.post("/api/apply", json={}, headers=H); j = await r.json()
        self.assertEqual(r.status, 200); self.assertTrue(j["restarted"]); self.assertTrue(self.marker.exists())

    async def test_never_restarts_during_a_conversation(self):
        self.brain["active_turns"] = 1
        r = await self.c.post("/api/apply", json={}, headers=H)
        self.assertEqual(r.status, 409); self.assertFalse(self.marker.exists())

    async def test_unreachable_brain_means_no_restart(self):
        self.cfg["brain_health"] = "http://127.0.0.1:1/health"
        r = await self.c.post("/api/apply", json={}, headers=H)
        self.assertEqual(r.status, 503); self.assertFalse(self.marker.exists())

    async def test_failed_restart_is_reported(self):
        self.cfg["restart_cmd"] = ["false"]
        r = await self.c.post("/api/apply", json={}, headers=H); self.assertEqual(r.status, 500)

    async def test_status(self):
        j = await (await self.c.get("/api/status")).json()
        self.assertEqual((j["reachable"], j["brain"], j["active_turns"]), (True, "ready", 0))


if __name__ == "__main__":
    unittest.main()
