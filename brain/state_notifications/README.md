# Sunday state notifications and unified dashboard — 2026-10-23

Main dashboard: `/sunday-hub/home`, sidebar **Sunday** (admin, preserving the most restrictive
permissions of the combined dashboards). Views: Home, Inputs, Memory inbox,
Memories, Costs, Usage & models, Wake feedback, Network, Local model. Old Sunday, standalone
memory/cost/input/wake/network/local-model dashboard URLs still work, but duplicate sidebar
entries are hidden. Existing room dashboards are unchanged. Copied navigation actions point
to the hub where their old Sunday destinations matched. Backup of all source dashboards and
sidebar metadata: deployment/before-notification-hub-20261023/ha-dashboards.json (private).

Notifications: `/device-automations/notifications`, sidebar **Automations**. This is an explicit deterministic alert system,
not an inference or memory/proactivity enrollment change. Rules operate across any chosen
known device/helper/sensor entity. Automation and script state entities are excluded from
selection to avoid notification-of-notification loops. Basic condition types: any actual
state change, equals a chosen state, crosses above a numeric threshold, crosses below a
numeric threshold. Optional attribute, previous value, held duration, per-rule cooldown,
message, multiple entities, enabled flag. Binary door sensors normally use off=closed,
on=open. Numeric conditions use HA's crossing semantics, not repeated alerts on every reading.

Native Home Assistant automations own triggers, conditions, timing and execution. New IDs
start sunday_state_notify_. Description metadata describes the editable generic rule;
all actions route through script.sunday_announce, channel telegram, preserving Sunday's
existing Telegram owner/destination. No model polling or second Telegram bot/poller is added.
Unknown/startup/unavailable transitions are ignored unless availability is explicitly chosen.
No catch-up alert is emitted for an already-matching state when a rule is created/enabled.
HA's hold timers and running cooldown delays reset if HA restarts; the configuration and its
enabled selection persist. A running Telegram send cannot be recalled after dispatch.

The pre-existing fridge_any_door_open_telegram automation is imported as one editable rule
covering refrigerator and freezer door off->on. It remains on with its original triggers,
message wording, mode and actions. Import/list perform no writes. Enable/disable preserves
its exact actions/trigger wording; editing its settings converts them to the generic builder.
There is no duplicate fridge automation and no test Telegram was sent.

Authenticated GET /v1/notifications, POST /v1/notifications/manage; HA REST bridges
sunday_notification_dashboard and sunday_notification_manage. Edits use revisions and an
async lock; stale requests return 409. Settings are written through HA's validating config
API and targeted automation reload. Older configurations are backed up mode 0600 to
state/notification_backups before edits/deletions. Private backups must not be published.
No HA configuration edit can target an unrelated automation through this service.

Sunday's chat/voice tool manage_device_notifications supports list/create/update/enable/
disable/remove. It uses the same validator/service, resolves IDs through home_query, and
updates only supplied fields. The prompt forbids inventing unsolicited rules. Read-only
turns can list but cannot configure. Ordinary home-device commands remain separate.
Example: "Notify me on Telegram when the bedroom light turns off."
Example: "Tell me if the fridge door stays open for two minutes."
Example: "Disable the fridge notifications."

Validation: 18 new unit/API/tool tests passed; 15 rebuild, 22 input-policy, 60 Telegram tests
passed. Four generated condition configurations passed the actual installed HA automation
validator in a separate isolated HA instance without actions. Live HA API create/update/
delete was exercised using only an explicitly disabled, impossible-state temporary rule;
it was removed and the fridge configuration compared equal before/after. No appliance
commands or test Telegram messages were sent. UI checks are in check_ui.py; browser writes
use a fake adapter while the live fridge rule remains unchanged.

Deployed card: /config/www/sunday-notification-card.js. Publisher: publish_hub.py. The hub
copies existing cards rather than changing their APIs. Sunday2 restart loads the chat tool;
REST commands/resources were reloaded without restarting Home Assistant.

Follow-up: the native HTML datalist did not reliably display partial-name matches in the
mobile HA app. Replaced it with an in-card, visible results list. Matching is case-insensitive
across friendly names, entity IDs and rooms; real fan-domain entities rank above helper
switches when searching fan. Results show name, room, current state and ID and are added by
tapping. Exact IDs remain supported; unknown or ambiguous input shows useful feedback.
Live mobile browser search for Fan returned all six actual fan entities plus relevant helpers;
Roka's Room Roka Fan appeared first and tap-to-select worked without entering an ID. No rule
was saved by this check. State notification tests now 19 passed; 43 Fast/reactive tests also
passed. Configuration terms such as notifications/alerts are excluded from Fast physical
control parsing, so "turn off fridge notifications" cannot be interpreted as fridge power off.

Dashboard separation follow-up: the notification editor was moved out of Sunday into its own
Automations dashboard. Existing rules, HA automations and Telegram routing were not changed.
Sunday chat/voice configuration remains available. publish_hub.py delegates to
publish_automations.py so future hub publishing keeps this separation. Dashboard snapshots
are in deployment/before-automations-dashboard-20261023.
