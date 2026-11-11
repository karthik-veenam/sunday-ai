# Audio and Stack-chan gateway

The Pi gateway provides body WebSocket transport, microphone selection, speaker delivery, MQTT discovery, body status, emotion/sleep projection, diagnostics and bounded recovery. Start point: `gateway/gateway.py`; service: `../deploy/systemd/sunday-stackchan-gateway.service`.

Normal body communication is Wi-Fi. USB provides power/programming and optional out-of-band recovery. A body absent from both network and USB cannot be reset remotely by the current host recovery helper.

## Interfaces

- 8770: authenticated body WebSocket `/stackchan`.
- 8771/8772: loopback microphone/speaker adapters.
- 8773: loopback health and gateway APIs.

The live backend/microphone configuration and gateway keys are excluded. Provision local `secrets/` and `state/` according to `sunday_audio.py` and the existing service configuration. Review `sunday-audio --help` before use: backend changes affect live capture and must be deliberate.

`gateway/ha_mqtt.py` uses separate gateway availability for Pi-owned voice settings. Physical body controls require both body and gateway. Never publish a disconnected physical body as online just to make its controls clickable.

Tests are under `tests/`; known legacy test limitations are documented in `../docs/TESTING.md`. No firmware flash is part of gateway setup.
