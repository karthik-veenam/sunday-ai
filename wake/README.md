# Wake detection and verification

- `runtime/openwakeword/`: the evaluated local frontend/runtime used by the Pi detector.
- `models/sunday_candidate_20261107/`: November7 wake classifier and matching frontend assets.
- `models/sunday_v5_v6_filter/`: immediately preceding deployed model and matching frontend assets.
- `sherpa/`: English keyword verifier service and tuning/configuration code.
- `diagnostics/`: aliases, usage, debug notifications and negative-dataset review services.

Both selectable first-stage models use threshold0.99 in the current implementation. This threshold is unrelated to the Sherpa keyword score/acceptance threshold. The older profile enables the existing verifier; verifier outages never silently fall back to unverified wakes.

`models/SHA256SUMS` records the exported artifacts. Training recordings and rejected-utterance audio are private and excluded. Sherpa model downloads and generic Whisper/Piper assets are not bundled; see service definitions and the Sherpa README for expected locations.

The historical trade-off was better recall on the older first-stage model versus fewer candidate false triggers on the November7 model. These historical evaluations do not guarantee real-room performance.
