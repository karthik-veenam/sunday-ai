# Sunday 2 brain

The rebuilt brain is the conversational and policy owner behind the existing `:8090` interface. Start point: `main.py`; service reference: `../deploy/systemd/sunday2.service`.

## Setup

Use a dedicated virtual environment, install `requirements.txt`, copy `config.example.json` to a local ignored `config.json`, fill provider/HA/Telegram credentials and review state paths. Do not start it against production state from a second checkout.

## Features

Conversation/tool orchestration, Fast Mode, Telegram, HA query/action receipts, automation creation, phrase triggers, historical evidence, factual world/presence, memory, device patterns, proactive delivery, mood/body context, cost telemetry and async Codex jobs are included. See `../docs/FEATURES.md` and `tests/`.

`memory_v1`, `memory_v2`, `proactivity_v1` and `proactivity_v2` coexist because this is the actual evolved implementation. Their presence does not mean all versions are enabled. Local config and runtime wiring determine the active path.

Private state from the deployed `state/` directory is excluded. The separate `codex_worker.py` service handles delegated work asynchronously. Reply-model selection is distinct from memory/proactivity configuration.
