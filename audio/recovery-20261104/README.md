# Sunday body reconnection repair — 2026-11-04

Incident: on the 11:47 boot, audio guard timed out before readiness and persisted backend=airhug, then stopped/disabled the body gateway. At 23:29 gateway was restored; body absent from Wi-Fi/router but present on its registered USB. Normal USB reset restored authenticated connection. Existing switch transaction restored selected route, AIRHUG capture and body output. Exact original firmware/Wi-Fi stall cause is not established.

Prevention deployed:
- sunday_audio.guard no longer changes the selected backend or stops/disables gateway after transient boot unreadiness. Gateway and microphone already retry connection. Explicit manual airhug selection still works.
- sunday-body-recovery.timer checks every 30 seconds, starts a stopped gateway only when stackchan is selected, and restarts gateway after three failed health checks (five-minute restart cooldown).
- Healthy gateway + disconnected body for at least 180 seconds permits a normal USB reset, only for fixed by-id CoreS3 MAC 7C:4F:AD:AF:C9:C8. No flash/NVS writes or stub upload. Waits while conversation/playback is active. Minimum ten minutes between reset attempts, maximum three per rolling hour. Budget persisted before action, including failures.
- Shared backend-switch lock prevents recovery during explicit switches. Explicit airhug disables recovery behavior. A connected or non-USB body is never reset.

Verification: 31 simulated policy/audio-switch tests pass, unit files verified, initial real timer run reports connected with zero resets. Production voice PID101306 and gateway PID101278 unchanged during prevention deployment; authenticated body connected, AIRHUG active and one capture subscriber. No failure injection/reset of healthy production body. No firmware changes. No hardware/network uptime guarantee; exhausted retries remain visible in state/body_recovery.json and systemd journal.

Files: sunday_audio.py, body_recovery.py, tests/test_cli.py, tests/test_body_recovery.py; units in this directory installed under /etc/systemd/system. Prior CLI saved as sunday_audio.before.py. Rollback: disable --now sunday-body-recovery.timer, restore saved CLI. Do not disable the gateway as part of rollback.
