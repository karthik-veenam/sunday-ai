# Architecture

## Ownership and entry points

`brain/main.py` hosts the rebuilt brain on the existing `:8090` interface. The HA/LVA compatibility architecture is retained. `voice/main.py` owns microphone orchestration; `voice/voice_brain.py` adapts turns to the rebuilt brain. The remaining voice runtime contains transitional background workers, so it cannot simply be deleted after deploying the rebuilt brain.

`brain/telegram_transport.py` implements Telegram ownership/delivery. One poller must own a token at a time; do not run a second development instance with the production bot configuration. `brain/output.py` and delivery helpers keep text/speech/tool outcomes distinct.

## Turn processing

1. The selected microphone supplies PCM to the voice loop. Only one production input pipeline is used.
2. `voice/wake.py` runs a pinned openWakeWord frontend and model. `voice/wake_profile.py` watches the HA model selector and swaps models at a listening boundary.
3. `voice/wake_verifier.json` (local configuration) determines whether the Sherpa second-stage verification is enabled. The previous profile explicitly enables it; errors do not silently bypass verification.
4. STT can stream during speech or process a complete clip. Cleanup, adaptive detection and speech-boundary thresholds are configurable. These are distinct from wake detection.
5. The brain assembles system instructions, conversation, current context and selected memory/world evidence, then performs Fast Mode or model/tool orchestration.
6. Device actions return receipts/observations. A completed service call is different from an observed HA state, which itself may be assumed rather than physically measured.
7. Speech delivery generates audio and sends it to the active playback path; the body gateway coordinates playback, state and expressions.

## Brain subsystems

- `home_tools.py`, `ha_client.py`, `automation_tools.py`: home query/action access and native automation creation.
- `fast_mode*`: deterministic supported-command plans.
- `world.py`, `world_tools.py`, `presence.py`, `historical.py`: current facts, presence and bounded historical evidence.
- `memory_v1/`, `memory_v2/`, `wiki/`, `wiki_io.py`: retained memory implementations and retrieval/write paths. Local flags determine the enabled paths; personal content is absent.
- `device_patterns/`: selected input sessions, durations, coverage and pattern analysis.
- `proactivity_v1/`, `proactivity_v2/`, `state_notifications/`: event evaluation, constraints, notifications and delivery.
- `alive/`, `body_context.py`, `body_commands.py`: mood, sleep and body-aware conversational context.
- `cost_telemetry/`: recorded model usage/cost classification. Runtime pricing/configuration must be checked before financial conclusions.
- `codex_tools.py`, `codex_worker.py`, `codex_delivery.py`: asynchronous delegated jobs, status and result delivery.

## Body and audio gateway

`audio/gateway/gateway.py` exposes authenticated body WebSocket transport on port 8770, Pi microphone/speaker bridges on loopback 8771/8772 and health on loopback 8773. Health distinguishes selected/active microphone, connected body, playback and diagnostic state.

`audio/gateway/ha_mqtt.py` publishes HA MQTT discovery. Pi-owned model/microphone/STT controls use gateway availability. Physical body controls require both gateway and body availability. A disconnected body must not disable independent Pi settings or be presented as connected.

The firmware uses an ESP32-S3/CoreS3 Stack-chan with M5 libraries. Wi-Fi carries normal control/audio. USB is useful for power, initial programming and out-of-band recovery, but is not required for normal wireless operation.

## Persistence

The live installation uses local JSON/SQLite stores for conversations, receipts, jobs, telemetry, memory, patterns and proactive delivery. The repository contains schemas and code, not those stores. Backup databases with a consistent SQLite backup procedure; copying an active database alone without its WAL is unsafe.

## Network findings

Several devices experienced incorrect/proxy MAC mappings and network timeouts. Narrow Pi neighbor bindings were installed for verified owned devices. These are house-specific mitigations, not a proven universal extender fix. Hogar's socket reconnect loop also has a known stuck `Already connected` condition. See [operations](OPERATIONS.md).
