"""Transactional-switch tests for sunday_audio.py against a SIMULATED system (fake systemd, fake clock, fake processes).
Nothing here can touch real services.   ~/sunday-audio/venv/bin/python -W ignore -m unittest -v tests.test_cli"""
import fcntl
import importlib
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class FakeSys:
    def __init__(self, m, backend_file):
        self.m, self.bf = m, backend_file
        self.t = 1000.0
        self.active = {m.PROD_UNIT: True, m.GW_UNIT: False}
        self.enabled = {m.GW_UNIT: False}
        self.calls = []
        self.procs = {m.PROD_UNIT: [(100, "arecord", "arecord -D pulse ...")]}   # current state: production on the AIRHUG
        self.started_at = {}
        self.docker_state = {"linux-voice-assistant": True}
        self.frames = 0
        # behaviour knobs
        self.device_connects = True
        self.prod_ready = True
        self.stop_releases = True
        self.kill_releases = True
        self.dropin_src = True
        self.dropin_on = False
        self.airhug = True
        self.unit_present = True
        self.fail_start_once = set()
        self.fail_start_always = set()
        self.real_arecord_leak = False
        self.shim_path = True
        self.gw_mic_connects = True
        self.frames_advance = True

    def backend(self):
        try: return open(self.bf).read().strip()
        except OSError: return "airhug"

    def now(self): return self.t
    def sleep(self, s): self.t += s
    def is_active(self, u): return self.active.get(u, False)
    def unit_exists(self, u): return self.unit_present
    def dropin_installed(self): return self.dropin_on
    def dropin_source_exists(self): return self.dropin_src
    def dropin_install(self): self.calls.append(("dropin", "install")); self.dropin_on = True
    def dropin_remove(self): self.calls.append(("dropin", "remove")); self.dropin_on = False
    def airhug_present(self, match): return self.airhug
    def enable(self, u): self.calls.append(("enable", u)); self.enabled[u] = True
    def disable(self, u): self.calls.append(("disable", u)); self.enabled[u] = False

    def start(self, u):
        self.calls.append(("start", u))
        if u in self.fail_start_always or u in self.fail_start_once:
            self.fail_start_once.discard(u); raise self.m.SwitchError("simulated start failure of " + u)
        self.active[u] = True
        if u == self.m.PROD_UNIT:
            self.started_at[u] = self.t
            if self.backend() == "stackchan":
                self.procs[u] = [(200, "python", "python main.py"), (201, "python", "python /x/shim/arecord -D pulse")]
                if self.real_arecord_leak: self.procs[u].append((202, "arecord", "arecord"))
            else:
                self.procs[u] = [(200, "python", "python main.py"), (202, "arecord", "arecord -D pulse")]

    def stop(self, u):
        self.calls.append(("stop", u))
        self.active[u] = False
        if u == self.m.PROD_UNIT and self.stop_releases: self.procs[u] = []

    def kill(self, u):
        self.calls.append(("kill", u))
        if self.kill_releases: self.procs[u] = []

    def main_pid(self, u): return 200
    def unit_procs(self, u): return list(self.procs.get(u, []))
    def proc_path(self, pid):
        return (self.m.SHIM_DIR + ":/usr/bin") if (self.backend() == "stackchan" and self.shim_path) else "/usr/bin"

    def journal(self, u, since):
        if u == self.m.PROD_UNIT and self.prod_ready and self.started_at.get(u, -1) >= since:
            return "[Mic] Stream started (pulse)\n[Wake] Listening for Sunday (x)\n"
        return ""

    def health(self):
        if not self.active.get(self.m.GW_UNIT): return None
        if self.frames_advance: self.frames += 25
        return {"device_connected": self.device_connects, "mic_subscribers": 1 if (self.gw_mic_connects and self.active[self.m.PROD_UNIT]) else 0,
                "frames_out": self.frames, "frames_in": self.frames, "silence_frames": 0, "gated_frames": 0, "plays_ok": 0,
                "plays_failed": 0, "playing": False, "device": {"fw": "fake"}}

    def docker(self, action, name):
        self.calls.append(("docker", action, name)); self.docker_state[name] = (action == "start"); return True
    def docker_running(self, name): return self.docker_state.get(name, False)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sa-cli-")
        os.environ["SUNDAY_AUDIO_HOME"] = self.dir
        os.makedirs(self.dir + "/secrets")
        with open(self.dir + "/secrets/device.key", "w") as f: f.write("k" * 40)
        import sunday_audio
        self.m = importlib.reload(sunday_audio)
        self.S = FakeSys(self.m, self.m.BACKEND_FILE)

    def set_backend(self, b):
        self.m.write_backend(b)
        if b == "stackchan":                     # make the simulated world consistent with a running stackchan setup
            self.S.active[self.m.GW_UNIT] = True; self.S.enabled[self.m.GW_UNIT] = True
            self.S.dropin_on = True
            self.S.procs[self.m.PROD_UNIT] = [(200, "python", "main"), (201, "python", "shim")]

    def run_cli(self, *a): return self.m.main(list(a), self.S)

    def idx(self, call): return self.S.calls.index(call)

    # ------------------------------------------------------------------ happy paths
    def test_airhug_to_stackchan(self):
        self.assertEqual(self.run_cli("stackchan"), 0)
        self.assertEqual(self.m.read_backend(), "stackchan")
        S, m = self.S, self.m
        self.assertTrue(S.active[m.PROD_UNIT] and S.active[m.GW_UNIT] and S.enabled[m.GW_UNIT])
        self.assertLess(self.idx(("stop", m.PROD_UNIT)), self.idx(("start", m.GW_UNIT)), "production stops before anything starts")
        self.assertLess(self.idx(("start", m.GW_UNIT)), self.idx(("start", m.PROD_UNIT)), "gateway is up before production")
        self.assertFalse([p for p in S.procs[m.PROD_UNIT] if p[1] == "arecord"], "AIRHUG must not be open")
        self.assertTrue(S.dropin_on, "stackchan backend installs the PATH-shim drop-in")

    def test_stackchan_to_airhug(self):
        self.set_backend("stackchan")
        self.assertEqual(self.run_cli("airhug"), 0)
        S, m = self.S, self.m
        self.assertEqual(m.read_backend(), "airhug")
        self.assertFalse(S.active[m.GW_UNIT]); self.assertFalse(S.enabled[m.GW_UNIT])
        self.assertTrue([p for p in S.procs[m.PROD_UNIT] if p[1] == "arecord"], "real arecord (AIRHUG) is back")
        self.assertFalse(S.dropin_on, "airhug == pristine original config: drop-in removed")
        self.assertLess(self.idx(("stop", m.PROD_UNIT)), self.idx(("stop", m.GW_UNIT)))

    def test_switch_to_current_backend_is_a_clean_restart_and_verify(self):
        self.assertEqual(self.run_cli("airhug"), 0)
        self.assertEqual(self.m.read_backend(), "airhug")

    # ------------------------------------------------------------------ refusals touch nothing
    def test_refused_without_dropin_never_stops_production(self):
        self.S.dropin_src = False
        self.assertEqual(self.run_cli("stackchan"), 1)
        self.assertEqual(self.S.calls, [], "preflight failures must not touch any service")
        self.assertEqual(self.m.read_backend(), "airhug")

    def test_refused_when_gateway_unit_missing_or_key_missing(self):
        self.S.unit_present = False
        self.assertEqual(self.run_cli("stackchan"), 1)
        self.S.unit_present = True; os.remove(self.dir + "/secrets/device.key")
        self.assertEqual(self.run_cli("stackchan"), 1); self.assertEqual(self.S.calls, [])

    def test_refused_when_airhug_unplugged(self):
        self.set_backend("stackchan"); self.S.airhug = False
        self.assertEqual(self.run_cli("airhug"), 1); self.assertEqual(self.S.calls, []); self.assertEqual(self.m.read_backend(), "stackchan")

    def test_unknown_backend_and_dry_run(self):
        self.assertEqual(self.run_cli("bogus"), 1)
        self.assertEqual(self.run_cli("stackchan", "--dry-run"), 0)
        self.assertEqual(self.S.calls, []); self.assertEqual(self.m.read_backend(), "airhug")

    # ------------------------------------------------------------------ failures roll back automatically
    def test_device_never_connects_rolls_back_to_airhug(self):
        self.S.device_connects = False
        self.assertEqual(self.run_cli("stackchan"), 2)
        S, m = self.S, self.m
        self.assertEqual(m.read_backend(), "airhug")
        self.assertTrue(S.active[m.PROD_UNIT]); self.assertFalse(S.active[m.GW_UNIT]); self.assertFalse(S.enabled[m.GW_UNIT])
        self.assertTrue([p for p in S.procs[m.PROD_UNIT] if p[1] == "arecord"], "AIRHUG restored")
        self.assertFalse(S.dropin_on, "failed switch leaves no trace: drop-in removed again")

    def test_production_does_not_come_up_rolls_back(self):
        self.S.prod_ready = True
        orig = self.S.start; n = {"c": 0}
        def flaky(u):
            orig(u)
            if u == self.m.PROD_UNIT:
                n["c"] += 1
                if n["c"] == 1: self.S.prod_ready = False      # first start (stackchan) never reports ready
                else: self.S.prod_ready = True                 # the rollback start is fine
        self.S.start = flaky
        self.assertEqual(self.run_cli("stackchan"), 2); self.assertEqual(self.m.read_backend(), "airhug")

    def test_audio_not_released_is_not_ignored(self):
        self.S.stop_releases = False; self.S.kill_releases = False
        self.assertEqual(self.run_cli("stackchan"), 2)
        self.assertNotIn(("start", self.m.GW_UNIT), self.S.calls, "never start a new backend while the old one still holds the audio")
        self.assertIn(("kill", self.m.PROD_UNIT), self.S.calls, "escalates to SIGKILL first")

    def test_real_arecord_leak_is_caught(self):
        self.S.real_arecord_leak = True                         # drop-in not effective: production still opened the AIRHUG
        self.assertEqual(self.run_cli("stackchan"), 2); self.assertEqual(self.m.read_backend(), "airhug")

    def test_production_path_without_shim_is_caught(self):
        self.S.shim_path = False
        self.assertEqual(self.run_cli("stackchan"), 2); self.assertEqual(self.m.read_backend(), "airhug")

    def test_stalled_microphone_stream_is_caught(self):
        self.S.frames_advance = False
        self.assertEqual(self.run_cli("stackchan"), 2); self.assertEqual(self.m.read_backend(), "airhug")

    def test_rollback_failure_reports_3(self):
        self.set_backend("stackchan")
        self.S.fail_start_always.add(self.m.PROD_UNIT)          # nothing can start production any more
        self.assertEqual(self.run_cli("airhug"), 3)

    # ------------------------------------------------------------------ boot guard
    def test_guard_preserves_selection_and_gateway_when_device_absent_at_boot(self):
        self.set_backend("stackchan"); self.S.device_connects = False
        self.assertEqual(self.run_cli("guard"), 0)
        self.assertEqual(self.m.read_backend(), "stackchan")
        self.assertFalse(os.path.exists(self.dir + "/state/fallback.json"))
        self.assertTrue(self.S.active[self.m.GW_UNIT]); self.assertTrue(self.S.enabled[self.m.GW_UNIT])
        self.assertNotIn(("stop",self.m.GW_UNIT),self.S.calls)
        self.assertNotIn(("disable",self.m.GW_UNIT),self.S.calls)

    def test_guard_keeps_gateway_for_usb_capture_while_body_reconnects(self):
        self.set_backend("stackchan"); self.S.device_connects = False
        self.S.health = lambda: {"device_connected":False,"microphone":{"active":"AIRHUG USB microphone"}} if self.S.is_active(self.m.GW_UNIT) else None
        self.assertEqual(self.run_cli("guard"),0)
        self.assertEqual(self.m.read_backend(),"stackchan")
        self.assertTrue(self.S.is_active(self.m.GW_UNIT))
        self.assertNotIn(("disable",self.m.GW_UNIT),self.S.calls)

    def test_guard_keeps_stackchan_when_ready_and_ignores_airhug(self):
        self.set_backend("stackchan")
        self.assertEqual(self.run_cli("guard"), 0); self.assertEqual(self.m.read_backend(), "stackchan")
        self.set_backend("airhug"); self.S.calls.clear()
        self.assertEqual(self.run_cli("guard"), 0); self.assertEqual(self.S.calls, [])

    # ------------------------------------------------------------------ misc
    def test_concurrent_switch_is_refused(self):
        os.makedirs(self.m.STATE_DIR, exist_ok=True)
        with open(self.m.LOCK_FILE, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            self.assertEqual(self.run_cli("stackchan"), 4)
        self.assertEqual(self.S.calls, [])

    def test_optional_lva_management(self):
        json.dump({"manage_lva": True}, open(self.dir + "/config.json", "w"))
        self.assertEqual(self.run_cli("stackchan"), 0)
        self.assertIn(("docker", "stop", "linux-voice-assistant"), self.S.calls)
        self.assertEqual(self.run_cli("airhug"), 0)
        self.assertIn(("docker", "start", "linux-voice-assistant"), self.S.calls)

    def test_lva_untouched_by_default(self):
        self.assertEqual(self.run_cli("stackchan"), 0)
        self.assertFalse([c for c in self.S.calls if c[0] == "docker"])

    def test_shim_is_not_mistaken_for_the_real_arecord(self):
        # regression: the first live switch rolled back because the python shim ALSO has comm == "arecord"
        c = self.m.classify
        self.assertEqual(c("arecord", "/usr/bin/aplay"), "arecord")              # real binary (arecord is a symlink to aplay)
        self.assertEqual(c("arecord", "/usr/bin/python3.13"), "arecord-shim")    # our shim
        self.assertEqual(c("aplay", "/usr/bin/python3.13"), "aplay-shim")
        self.assertEqual(c("python", "/usr/bin/python3.13"), "python")
        self.assertEqual(c("arecord", ""), "arecord")                            # unreadable exe: stay conservative (treated as real)

    def test_status_runs(self):
        self.assertEqual(self.run_cli("status"), 0); self.assertEqual(self.run_cli("status", "--json"), 0)

    def test_backend_file_is_atomic_and_garbage_means_airhug(self):
        os.makedirs(self.m.STATE_DIR, exist_ok=True)
        open(self.m.BACKEND_FILE, "w").write("nonsense"); self.assertEqual(self.m.read_backend(), "airhug")
        os.remove(self.m.BACKEND_FILE); self.assertEqual(self.m.read_backend(), "airhug")


if __name__ == "__main__":
    unittest.main()
