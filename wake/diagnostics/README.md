# Rejected wake transcript toggle

Home Assistant: Sunday Inputs dashboard → Voice configs → Rejected wake transcripts.
Helper: input_boolean.sunday_rejected_wakes_to_telegram (initially off).

The independent sunday-verifier-notifications.service tails new verdict records. Only enforce-mode rejected candidates are eligible; accepted wakes, shadow verdicts and fail-open errors are ignored. When enabled, sends Whisper's exact short transcript (or [no speech recognized]) and the verdict timestamp to the configured primary Telegram chat. It does not upload audio. A first-layer non-trigger has no Whisper transcript and is not reported.

No wake/mic/verifier code or configuration changes; no existing service restarted. Diagnostics never influence the acceptance decision. Starts at end of the log, so old rejects are not replayed; duplicate event IDs are suppressed during a run. Diagnostic events while the observer is stopped are not replayed on restart. Delivery errors do not cause blind retries. Five offline tests passed with mocked senders; no synthetic Telegram messages or audio/device tests sent.

Disable notifications with the helper. Disable observer entirely: sudo systemctl disable --now sunday-verifier-notifications.service. Dashboard pre-change backup: dashboard.before.json. Existing voice-transcript toggle is unchanged.

## Alias dashboard

Sunday Inputs → Voice configs → Wake aliases shows the current live aliases and a text field/Add alias button. Enter submits. Values use the same lowercase/punctuation-insensitive whole-word contains normalization. Duplicate additions do not restart anything. New additions atomically update wake_verifier.json and wake_verifier.env, then reload only sunday-verifier.service and check its health. A failed apply restores previous settings. Wake detector code, mic code, main.py and threshold are unchanged.

The notification observer also serves an authenticated loopback-only POST /v1/wake-aliases on 127.0.0.1:8097, reached through HA rest_command.sunday_wake_aliases. The frontend receives aliases only, no API token. The verifier's systemd aliases.conf drop-in loads /home/djpi/sunday/wake_verifier.env, which overrides the base unit's static SUNDAY_ALIASES. Use the UI/API for future additions rather than editing the base unit's alias string alone.

10 offline tests passed: forwarding unchanged; auth/validation, preserved verifier settings, concurrent additions, duplicate idempotence and failed-apply rollback. Live HA REST list/Add duplicate path and mobile UI passed. Managed verifier environment was checked against the JSON list after a settings-only reload. No test alias added and no synthetic Telegram/audio/device effects sent. Backup: alias-dashboard-backup. API addition requires the notifications service to remain running even when the rejected-wake Telegram toggle is off; the toggle only controls notifications.

Whisper audio_ctx is now shown below aliases with Save context. API action set_context accepts whole numbers 32–1500, writes only the JSON audio_ctx field and applies on the next trigger without any verifier or mic restart. Alias list, capture window, timeout and acceptance mode are preserved. Current value left at 128. The card also shows the existing timeout; larger contexts may take longer. Twelve offline tests cover the alias/notification paths and numeric validation/preservation. Live API no-change save and mobile context input/save verified.
