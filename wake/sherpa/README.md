# Live Sherpa keyword-verifier trial

Selected explicitly by the user, 2026-10-27. V5 first-stage detector → mandatory Sherpa verifier. No V5-only fallback, and no automatic provider fallback: errors retry the same audio once, then block that wake. The independent whisper.cpp service remains running for an explicit revert.

## Fast revert

From HA: **Sunday Inputs → Voice → Wake aliases → Revert to Whisper**.

Or:

```bash
python3 /home/djpi/sunday-sherpa-verifier/switch.py whisper
```

Applies to the next wake; no service restart. The command validates the target's health and atomically changes only provider/url/mode, preserving current aliases, context and audio settings. To explicitly select the trial again:

```bash
python3 /home/djpi/sunday-sherpa-verifier/switch.py sherpa
```

Whisper is 127.0.0.1:8183/inference; Sherpa is 127.0.0.1:8184/inference. Never set mode off as a rollback; verification is mandatory. No need to stop/delete either service to switch. If an inference is already in progress during a switch, it may finish against the previous selected backend; the next wake reads the new config.

## Model and execution

English Gigaspeech Zipformer 3.3M int8, score3/threshold.1, beam4, one CPU thread, same full keyword alias set as the offline 50/57 trial. Current aliases are read for each request and compiled into per-stream keyword paths on change. Canonical Sunday plus the configured aliases, including some day/someday. No model training or threshold/V5 changes. WAV requests match the original 16kHz mono PCM16 window, 1.6s before/.16s after. As in the offline test, .66s digital silence flushes decoding without waiting for extra recorded audio. Returns a keyword decision, not an ASR transcript. Actual audio remains in memory and the server does not write production verdicts or recordings itself.

Unit: `sunday-sherpa-verifier.service`, enabled and bound to loopback, models kept resident and warmed before health becomes available. Uses the isolated Sherpa research venv. Serial inference with busy rejection; client retries/blocks on errors. The original microphone process still owns final alias acceptance, audit and rejected-window dataset capture. Model output is validated against configured aliases. No remote inference or new cloud dependency.

## Existing diagnostics preserved

`wake.py` records provider/model/keyword result, matched alias and latency, with empty transcript for Sherpa (no fabricated “Whisper heard…”). Alias badges still count actual accepted alias matches. Rejected clips still accumulate as original WAVs; metadata correctly identifies Sherpa, with Whisper-only context/prompt unset. HA dataset view and Telegram debug distinguish no-keyword results from failed ASR. Failed requests are excluded from training negatives. Whisper context controls stay stored but are disabled in the aliases card during the Sherpa trial.

## Validation and transition

19 strict verifier/dataset/bridge tests, 18 diagnostic regressions, 2 server API tests and 7 provider-switch tests passed (some shared setup tests overlap). Dataset 7-test rerun also passed after provenance updates. Real HTTP bridge check: silence rejected (~357ms), known saved genuine call accepted through a configured alias (~257ms); no production verdict/audio/dataset writes from those checks. Real HA revert endpoint exercised while Whisper was still selected; unhealthy-target guard tested separately. Source snapshots and original HA config saved under deployment/.

Started Sherpa separately, validated it, published the diagnostics/HA changes, loaded the protocol bridge by restarting only sunday.service while the brain/body were idle, verified mic listening, then selected Sherpa. Brain, whisper.cpp, body gateway, house, MQTT bridge and cron PIDs unchanged. Main.py, mic.py, STT/TTS provider configs, audio backend and V5 detector class AST unchanged. No firmware changes, audible tests or physical HA device actions.

Known limitation: offline trial accepted 50/57 older approved files, recovered 1/2 marked false rejections and accepted 2/36 development negative windows. These are reused/overlapping small datasets, not general accuracy or false-wakes/hour claims. This is a user-requested live trial, not a validated permanent cutover. Easy manual revert remains available.


## Beam 12 deployment — 2026-10-28

User approved the saved-audio candidate: boost 3, threshold 0.02, max_active_paths 12, trailing blanks 1, int8 model. Active aliases pruned to sunday, send it, summit, some days, somebody, something, submit, some very, sander. Health now reports max_active_paths. Only Sherpa service restarted; mandatory verifier policy and voice/body/brain services unchanged.

Development result: 56/57 original approved files, 2/35 negative windows after the user confirmed one previously negative-labelled window genuinely said Sunday. Two additional reviewed current wake clips pass. Reused/overlapping development data, not held-out accuracy.

Checkpoint: /home/djpi/sunday-sherpa-verifier/deployment/beam12-20261028-042734. Previous source, alias config, tuning and unit preserved there. For beam rollback restore checkpoint server.py and wake_verifier.json atomically, then restart only sunday-sherpa-verifier.service. Original Whisper remains running for explicit provider reversion using switch.py whisper.


## Wake detection tuning configuration

The V5 candidate threshold is already configurable as `wakeword_threshold` in
`/home/djpi/sunday/config.json` (currently `0.99`). `main.py` passes it to
`WakeWordDetector`; apply future edits on the next normal Sunday process start.
The generic Config class fallback is 0.5; keep the existing explicit 0.99 entry.

Sherpa `boost` and `threshold` are already configurable in
`/home/djpi/sunday-sherpa-verifier/state/tuning.json` (currently `3.0` and `0.1`).
When that file is absent the defaults are now also 3.0 and 0.1.
The existing authenticated `/settings` endpoint accepts those two fields and
persists them while replacing the running engine; direct file edits load on the
next normal verifier start.

Sherpa beam size is configurable using `SUNDAY_SHERPA_MAX_ACTIVE_PATHS` in the
verifier process environment (a positive integer, default `12`). Set it in the
`sunday-sherpa-verifier.service` environment for a future normal service start.
Invalid values fail initialization. `/health` reports the actual engine value;
boost/threshold updates preserve that engine's beam size. Python callers may
also supply `Engine(..., max_active_paths=12)` explicitly.

This configurability change did not edit either active settings file, set an
environment override, or restart any service. All four selected values remain
unchanged; source changes take effect only when the processes next load them.
