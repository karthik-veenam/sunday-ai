#!/usr/bin/python3
"""sunday-audio: choose which audio backend Sunday uses. Exactly one is ever active.

    sunday-audio status [--json]
    sunday-audio stackchan [--dry-run]     Stack-chan microphone + speaker (via the local gateway)
    sunday-audio airhug    [--dry-run]     original AIRHUG USB microphone + Pi audio output (the fallback)
    sunday-audio guard                     boot-time check (used by the systemd drop-in); never blocks sunday.service
    sunday-audio init-key                  create the device HMAC key (if missing)

A switch is a transaction: validate -> stop the current voice service -> confirm audio is released -> start the new
backend -> verify it is healthy. Any failure rolls back to the previous backend automatically.
Exit codes: 0 ok | 1 refused (nothing was touched) | 2 failed, rolled back to the previous backend | 3 failed AND rollback
failed (run ~/sunday-audio/emergency-airhug.sh) | 4 another switch is running."""
import fcntl
import json
import os
import secrets as pysecrets
import subprocess
import sys
import time
import urllib.request

HOME = os.environ.get("SUNDAY_AUDIO_HOME", os.path.expanduser("~/sunday-audio"))
STATE_DIR = os.path.join(HOME, "state")
BACKEND_FILE = os.path.join(STATE_DIR, "backend")
LOCK_FILE = os.path.join(STATE_DIR, "switch.lock")
LOG_FILE = os.path.join(HOME, "logs", "switch.log")
KEY_FILE = os.environ.get("SA_KEY_FILE", os.path.join(HOME, "secrets", "device.key"))
BACKENDS = ("airhug", "stackchan")
PROD_UNIT = os.environ.get("SUNDAY_AUDIO_PROD_UNIT", "sunday.service")
GW_UNIT = os.environ.get("SUNDAY_AUDIO_GW_UNIT", "sunday-stackchan-gateway.service")
DROPIN = f"/etc/systemd/system/{PROD_UNIT}.d/audio-backend.conf"
DROPIN_SRC = os.path.join(HOME, "systemd", "audio-backend.conf")
SHIM_DIR = os.path.join(HOME, "shim")
HEALTH_URL = "http://127.0.0.1:%s/health" % os.environ.get("SA_HEALTH_PORT", "8773")
MARKERS = ("[Mic] Stream started", "[Wake] Listening for Sunday")   # printed by production once mic + wake word are up
DEFAULTS = {"airhug_match": "AIRHUG", "manage_lva": False, "lva_container": "linux-voice-assistant",
            "start_timeout_s": 150, "stop_timeout_s": 60, "gateway_timeout_s": 20, "device_timeout_s": 30, "settle_s": 4}


class SwitchError(Exception):
    pass


def classify(comm, exe):
    """A script's process takes the SCRIPT's name as its comm, so our python `arecord` shim also shows up as 'arecord'.
    Tell them apart by the executable: the real arecord is a symlink to /usr/bin/aplay, the shim runs under python."""
    if comm in ("arecord", "aplay") and os.path.basename(exe or "").startswith("python"):
        return comm + "-shim"
    return comm


# ====================================================================== real system interface
class RealSys:
    def now(self): return time.time()
    def sleep(self, s): time.sleep(s)

    def _sc(self, *a, check=False):
        return subprocess.run(["sudo", "-n", "systemctl", *a], capture_output=True, text=True, timeout=120)

    def is_active(self, unit): return self._sc("is-active", "--quiet", unit).returncode == 0
    def unit_exists(self, unit): return self._sc("cat", unit).returncode == 0
    def start(self, unit):
        r = self._sc("start", unit)
        if r.returncode: raise SwitchError("systemctl start %s failed: %s" % (unit, r.stderr.strip()[:200]))
    def stop(self, unit):
        r = self._sc("stop", unit)
        if r.returncode: raise SwitchError("systemctl stop %s failed: %s" % (unit, r.stderr.strip()[:200]))
    def kill(self, unit): self._sc("kill", "--signal=SIGKILL", unit)
    def enable(self, unit): self._sc("enable", unit)
    def disable(self, unit): self._sc("disable", unit)

    def main_pid(self, unit):
        r = self._sc("show", "-p", "MainPID", "--value", unit)
        try: return int(r.stdout.strip() or 0)
        except ValueError: return 0

    def unit_procs(self, unit):
        """[(pid, comm, cmdline)] of every process currently in the unit's cgroup."""
        out = []
        for d in os.listdir("/proc"):
            if not d.isdigit(): continue
            try:
                with open(f"/proc/{d}/cgroup") as f:
                    if ("/" + unit) not in f.read(): continue
                comm = open(f"/proc/{d}/comm").read().strip()
                try: comm = classify(comm, os.readlink(f"/proc/{d}/exe"))
                except OSError: pass
                cmd = open(f"/proc/{d}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace").strip()
                out.append((int(d), comm, cmd))
            except OSError:
                continue
        return out

    def proc_path(self, pid):
        try:
            for item in open(f"/proc/{pid}/environ", "rb").read().split(b"\0"):
                if item.startswith(b"PATH="): return item[5:].decode()
        except OSError:
            pass
        return None

    def journal(self, unit, since_ts):
        r = subprocess.run(["sudo", "-n", "journalctl", "-u", unit, "--since", "@%d" % int(since_ts), "-o", "cat", "--no-pager"],
                           capture_output=True, text=True, timeout=30)
        return r.stdout

    def health(self):
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=2) as r: return json.loads(r.read())
        except Exception:
            return None

    def dropin_installed(self): return os.path.exists(DROPIN)
    def dropin_source_exists(self): return os.path.exists(DROPIN_SRC)
    def dropin_install(self):
        r = subprocess.run(["sudo", "-n", "install", "-D", "-m", "644", DROPIN_SRC, DROPIN], capture_output=True, text=True)
        if r.returncode: raise SwitchError("cannot install drop-in: " + r.stderr.strip()[:150])
        self._sc("daemon-reload")
    def dropin_remove(self):
        subprocess.run(["sudo", "-n", "rm", "-f", DROPIN], capture_output=True, text=True)
        self._sc("daemon-reload")

    def airhug_present(self, match):
        r = subprocess.run(["/usr/bin/arecord", "-l"], capture_output=True, text=True)
        return match in r.stdout

    def docker(self, action, name):
        return subprocess.run(["sudo", "-n", "docker", action, name], capture_output=True, text=True, timeout=60).returncode == 0

    def docker_running(self, name):
        r = subprocess.run(["sudo", "-n", "docker", "inspect", "-f", "{{.State.Running}}", name], capture_output=True, text=True)
        return r.stdout.strip() == "true"


# ====================================================================== helpers
def log(msg, quiet=False):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    if not quiet: print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as f: f.write(line + "\n")
    except OSError:
        pass


def cfg():
    c = dict(DEFAULTS)
    try:
        c.update(json.load(open(os.path.join(HOME, "config.json"))))
    except Exception:
        pass
    return c


def read_backend():
    try:
        b = open(BACKEND_FILE).read().strip()
        return b if b in BACKENDS else "airhug"
    except OSError:
        return "airhug"


def write_backend(b):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = BACKEND_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(b + "\n"); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, BACKEND_FILE)


def wait_for(S, pred, timeout, step=0.5):
    end = S.now() + timeout
    while True:
        v = pred()
        if v: return v
        if S.now() >= end: return False
        S.sleep(step)


# ====================================================================== stop / start / verify
def stop_voice(S, c):
    """Stop production first (it owns the audio), then the gateway; confirm everything is released."""
    log("stopping %s" % PROD_UNIT)
    if S.is_active(PROD_UNIT):
        S.stop(PROD_UNIT)
    if not wait_for(S, lambda: not S.is_active(PROD_UNIT), c["stop_timeout_s"]):
        log("%s did not stop in time - SIGKILL" % PROD_UNIT); S.kill(PROD_UNIT)
        if not wait_for(S, lambda: not S.is_active(PROD_UNIT), 15): raise SwitchError("%s will not stop" % PROD_UNIT)
    if not wait_for(S, lambda: not S.unit_procs(PROD_UNIT), 15):
        S.kill(PROD_UNIT)
        if not wait_for(S, lambda: not S.unit_procs(PROD_UNIT), 10):
            raise SwitchError("audio not released: processes still in %s: %s" % (PROD_UNIT, S.unit_procs(PROD_UNIT)))
    log("production stopped; no audio processes left (arecord/aplay released)")
    if S.is_active(GW_UNIT):
        log("stopping %s" % GW_UNIT); S.stop(GW_UNIT)
    if not wait_for(S, lambda: S.health() is None, 15):
        raise SwitchError("gateway still answering after stop")
    S.disable(GW_UNIT)


def preflight(S, target, c):
    """Everything that can be checked WITHOUT touching a running service."""
    if target == "airhug":
        if not S.airhug_present(c["airhug_match"]):
            raise SwitchError("AIRHUG capture device (%s) not found by arecord -l - is it plugged in?" % c["airhug_match"])
    else:
        if not S.unit_exists(GW_UNIT): raise SwitchError("gateway unit %s is not installed (run install.sh --apply)" % GW_UNIT)
        if not S.dropin_source_exists(): raise SwitchError("drop-in template %s missing: production could not be pointed at the shim" % DROPIN_SRC)
        if not os.path.exists(KEY_FILE): raise SwitchError("device key missing (%s): run sunday-audio init-key" % KEY_FILE)


def start_backend(S, target, c):
    t0 = S.now()
    if target == "stackchan":
        S.dropin_install(); log("installed systemd drop-in (PATH shim + boot guard) for %s" % PROD_UNIT)
        S.enable(GW_UNIT)
        log("starting %s" % GW_UNIT); S.start(GW_UNIT)
        if not wait_for(S, lambda: S.health(), c["gateway_timeout_s"]): raise SwitchError("gateway did not come up")
        log("gateway up; waiting for the Stack-chan to connect (up to %ds)" % c["device_timeout_s"])
        if not wait_for(S, lambda: (S.health() or {}).get("device_connected"), c["device_timeout_s"]):
            raise SwitchError("Stack-chan did not connect to the gateway (is it powered, on Wi-Fi, and flashed with the audio client?)")
        if c.get("manage_lva"):
            S.docker("stop", c["lva_container"]); log("stopped %s (AIRHUG now fully idle)" % c["lva_container"])
    else:
        # AIRHUG backend == the original, pristine setup: no drop-in, no shim on PATH, gateway neither running nor enabled.
        S.dropin_remove(); log("removed systemd drop-in: production runs exactly as originally configured")
        if S.is_active(GW_UNIT): S.stop(GW_UNIT)
        S.disable(GW_UNIT)
        if c.get("manage_lva") and not S.docker_running(c["lva_container"]):
            S.docker("start", c["lva_container"]); log("started %s" % c["lva_container"])
    log("starting %s with backend=%s" % (PROD_UNIT, target))
    S.start(PROD_UNIT)
    # --- verify production is genuinely up on the intended backend
    def markers():
        txt = S.journal(PROD_UNIT, t0)
        return all(m in txt for m in MARKERS)
    if not wait_for(S, markers, c["start_timeout_s"], step=2):
        raise SwitchError("%s did not report mic + wake word ready within %ds" % (PROD_UNIT, c["start_timeout_s"]))
    S.sleep(c["settle_s"])
    if not S.is_active(PROD_UNIT): raise SwitchError("%s died right after starting" % PROD_UNIT)
    procs = S.unit_procs(PROD_UNIT)
    real_arecord = [p for p in procs if p[1] == "arecord"]
    if target == "stackchan":
        pid = S.main_pid(PROD_UNIT)
        path = S.proc_path(pid) or ""
        if not path.startswith(SHIM_DIR):
            raise SwitchError("production process PATH does not start with the shim dir (%s): backend would not apply" % path[:80])
        if real_arecord: raise SwitchError("a real arecord is running under %s: the AIRHUG is still open" % PROD_UNIT)
        if not wait_for(S, lambda: (S.health() or {}).get("mic_subscribers", 0) >= 1, 20):
            raise SwitchError("production never connected to the gateway microphone")
        h1 = S.health() or {}; S.sleep(2); h2 = S.health() or {}
        if h2.get("frames_out", 0) <= h1.get("frames_out", 0): raise SwitchError("gateway microphone stream is not advancing")
        if not (h2.get("device_connected")): raise SwitchError("Stack-chan dropped during verification")
    else:
        if not wait_for(S, lambda: [p for p in S.unit_procs(PROD_UNIT) if p[1] == "arecord"], 20):
            raise SwitchError("production did not open the AIRHUG (no real arecord under %s)" % PROD_UNIT)
    log("verified: %s is healthy on backend '%s'" % (PROD_UNIT, target))


def switch(S, target, dry=False):
    c = cfg()
    if target not in BACKENDS:
        print("unknown backend %r (choose: %s)" % (target, ", ".join(BACKENDS))); return 1
    cur = read_backend()
    os.makedirs(STATE_DIR, exist_ok=True)
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another sunday-audio switch is already running"); return 4
    try:
        try:
            preflight(S, target, c)
        except SwitchError as e:
            log("REFUSED (nothing touched): %s" % e); return 1
        if dry:
            print("dry run: would stop %s%s, set backend %s -> %s, start %s%s, verify, roll back to '%s' on any failure" % (
                PROD_UNIT, " + " + GW_UNIT if cur == "stackchan" else "", cur, target,
                GW_UNIT + " then " if target == "stackchan" else "", PROD_UNIT, cur))
            return 0
        log("switch: %s -> %s" % (cur, target))
        try:
            stop_voice(S, c)
            write_backend(target)
            start_backend(S, target, c)
            log("SUCCESS: backend is now '%s'" % target)
            json.dump({"at": time.time(), "from": cur, "to": target, "result": "ok"}, open(os.path.join(STATE_DIR, "last_switch.json"), "w"))
            return 0
        except SwitchError as e:
            log("FAILED: %s" % e)
        except Exception as e:                                   # never leave production down on an unexpected error
            log("FAILED (unexpected %s): %s" % (type(e).__name__, e))
        # ---------------- automatic rollback to the previous backend
        log("ROLLING BACK to '%s'" % cur)
        try:
            for u in (PROD_UNIT, GW_UNIT):
                try:
                    if S.is_active(u): S.stop(u)
                except Exception: pass
            wait_for(S, lambda: not S.unit_procs(PROD_UNIT), 15)
            write_backend(cur)
            start_backend(S, cur, c)
            log("ROLLED BACK: backend restored to '%s' and verified" % cur)
            json.dump({"at": time.time(), "from": cur, "to": target, "result": "rolled-back"}, open(os.path.join(STATE_DIR, "last_switch.json"), "w"))
            return 2
        except Exception as e:
            write_backend("airhug") if cur == "airhug" else None
            log("ROLLBACK FAILED: %s -- run ~/sunday-audio/emergency-airhug.sh" % e)
            return 3
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN); lock.close()


def guard(S):
    """ExecStartPre of sunday.service: if stackchan is selected but cannot work right now, boot on the AIRHUG instead of
    leaving Sunday deaf. Always exits 0 (a failing ExecStartPre would keep Sunday from starting at all)."""
    c = cfg()
    if read_backend() != "stackchan":
        return 0
    ok = False
    try:
        if not S.is_active(GW_UNIT): S.start(GW_UNIT)
        def capture_ready():
            health = S.health() or {}
            # AIRHUG capture is independent of the body's Wi-Fi/speaker.
            # Keep its gateway alive so body output can reconnect automatically.
            return bool(health.get("device_connected") or
                        health.get("microphone", {}).get("active") == "AIRHUG USB microphone")
        ok = bool(wait_for(S, lambda: S.health(), 15) and wait_for(S, capture_ready, c["device_timeout_s"]))
    except Exception as e:
        log("guard: gateway error %s" % e)
    if ok:
        log("guard: stackchan backend ready", quiet=True); return 0
    # Readiness is transient at boot (PulseAudio/Wi-Fi may arrive later). Preserve
    # explicit selection and the enabled reconnecting gateway; never strand the body.
    log("guard: audio still starting; keeping Stack-chan selection and gateway for recovery")
    try: S.enable(GW_UNIT)
    except Exception: pass

    return 0


def status(S, as_json=False):
    c = cfg(); b = read_backend(); h = S.health()
    d = {"backend": b, "production_active": S.is_active(PROD_UNIT), "gateway_active": S.is_active(GW_UNIT),
         "dropin_installed": S.dropin_installed(), "airhug_present": S.airhug_present(c["airhug_match"]),
         "gateway": h, "real_arecord_in_production": [p[0] for p in S.unit_procs(PROD_UNIT) if p[1] == "arecord"],
         "lva_running": S.docker_running(c["lva_container"]) if c.get("manage_lva") else "unmanaged"}
    try: d["last_switch"] = json.load(open(os.path.join(STATE_DIR, "last_switch.json")))
    except Exception: pass
    try: d["fallback"] = json.load(open(os.path.join(STATE_DIR, "fallback.json")))
    except Exception: pass
    if as_json: print(json.dumps(d, indent=2)); return 0
    print("selected backend      : %s" % b)
    print("sunday.service        : %s" % ("active" if d["production_active"] else "INACTIVE"))
    print("AIRHUG mic held by prod: %s" % ("yes (pid %s)" % d["real_arecord_in_production"] if d["real_arecord_in_production"] else "no"))
    print("AIRHUG detected       : %s" % ("yes" if d["airhug_present"] else "NO"))
    print("gateway               : %s" % ("active" if d["gateway_active"] else "stopped"))
    if h:
        print("  Stack-chan          : %s  fw=%s" % ("CONNECTED" if h["device_connected"] else "not connected", (h.get("device") or {}).get("fw")))
        print("  mic frames in/out   : %s / %s  (silence %s, echo-gated %s)" % (h["frames_in"], h["frames_out"], h["silence_frames"], h["gated_frames"]))
        print("  speaker             : %d ok, %d failed%s" % (h["plays_ok"], h["plays_failed"], ", PLAYING" if h["playing"] else ""))
    print("shim drop-in installed: %s" % ("yes (production audio goes through the gateway)" if d["dropin_installed"] else "no (pristine original setup)"))
    if "fallback" in d: print("NOTE boot fallback    : %s" % d["fallback"])
    return 0


def init_key():
    if os.path.exists(KEY_FILE):
        print("device key already exists: %s (not changed)" % KEY_FILE); return 0
    os.makedirs(os.path.dirname(KEY_FILE), mode=0o700, exist_ok=True)
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f: f.write(pysecrets.token_hex(32) + "\n")
    print("created %s (mode 600). Copy it into the firmware's secrets.h when flashing the audio client." % KEY_FILE)
    return 0


def main(argv=None, S=None):
    a = list(sys.argv[1:] if argv is None else argv)
    S = S or RealSys()
    dry = "--dry-run" in a; js = "--json" in a
    a = [x for x in a if not x.startswith("--")]
    cmd = a[0] if a else "status"
    if cmd == "status": return status(S, js)
    if cmd in BACKENDS: return switch(S, cmd, dry)
    if cmd == "guard": return guard(S)
    if cmd == "init-key": return init_key()
    print(__doc__); return 1


if __name__ == "__main__":
    sys.exit(main())
