# Feature implementation map

| Feature | Main implementation |
|---|---|
| Brain HTTP/HA interface | `brain/main.py`, `brain/web.py`, `ha/custom_components/sunday2/` |
| Telegram replies and delivery | `brain/telegram_transport.py`, `brain/output.py` |
| Voice bridge | `voice/main.py`, `voice/voice_brain.py` |
| Fast Mode | `brain/fast_mode*`, tests with `fast` in their names |
| Query/action receipts | `brain/home_tools.py`, `brain/ha_client.py`, `brain/home_model*` |
| Automation creation | `brain/automation_tools.py` |
| When-I-say triggers | `brain/phrase_automations.py` |
| Historical evidence | `brain/historical.py`, `brain/history_tools.py` |
| Presence and factual world | `brain/presence.py`, `brain/world.py`, `brain/world_tools.py` |
| Memory | `brain/memory_v1/`, `brain/memory_v2/`, `brain/wiki/`, `brain/wiki_io.py` |
| Device sessions and patterns | `brain/device_patterns/` |
| Proactivity and notifications | `brain/proactivity_v1/`, `brain/proactivity_v2/`, `brain/state_notifications/` |
| Feelings, sleep and body context | `brain/alive/`, `brain/body_context.py`, `audio/gateway/` |
| Cost dashboard | `brain/cost_telemetry/`, `ha/dashboards/sunday-costs.json` |
| Ask Codex | `brain/codex_tools.py`, `brain/codex_worker.py`, `brain/codex_delivery.py` |
| Reply-model switch | `brain/reply_models.py`, gateway MQTT controls |
| Wake-model switch | `voice/wake_profile.py`, `wake/models/` |
| Wake verifier/aliases | `wake/sherpa/`, `wake/diagnostics/`, `voice/wake.py` |
| Negative wake dataset/review | `voice/negative_dataset.py`, `wake/diagnostics/`, `extras/clip-review/` |
| Live/batch STT | `voice/eleven_realtime_stt.py`, STT modules and gateway settings |
| Audio cleanup and adaptive speech boundaries | `voice/adaptive_detection.py`, `voice/stt_processing.py`, `voice/stt_thresholds.py` |
| Microphone selection | `audio/gateway/`, `audio/sunday_audio.py` |
| Display captions/cards | `body/experimental-display/`, `body/display-worker/` — reverted/disabled |
| Physical body firmware | `body/firmware-2.7.5/` |
| Home-specific Auto Climate example | `ha/examples/automations.example.yaml` |

Names describe implementation locations, not guarantees that every historical module is enabled simultaneously. The installation contains transitional/legacy modules, retained deliberately.
