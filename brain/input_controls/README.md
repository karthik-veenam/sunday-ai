# Sunday input selection — 2026-10-23

HA dashboard: `/sunday-inputs/inputs`, sidebar **Sunday Inputs** (admin).
Source card: input-card.js; deployed /config/www/sunday-input-card.js.

Separate memory/proactivity selections cover 11 actual source types: device states/supported
activity sessions, personal presence/zones, conversations, historical evidence, HA automations,
scripts/scenes, Sunday action outcomes, Sunday conclusions, learned memory/expectations,
household profile and service-health degradation. Unsupported combinations show a dash.
Automatic device memory currently supports light/switch/fan/media_player/humidifier/vacuum
complete sessions. Other entity types can be selected for proactivity. Configured location
is a separate source and does not depend on a room device being selected.

Devices are grouped by HA device, with individual entity controls beneath; search and room
filter are provided. Default enrollment is a snapshot of current room-scoped capabilities,
not a rule to enroll every future room device. New entity IDs start unchecked. Recreating
an entity under a different ID requires new enrollment; an IP change retaining HA IDs does
not change selection. Master source switches pause, rather than clear, device checkmarks.
A paused linked program cannot introduce a device that is not selected.

Choices persist atomically, mode 0600, in state/input_policy.json. Authenticated GET
/v1/inputs shows current selection/capability; POST /v1/inputs/manage applies validated
batches with a revision check (409 on stale saves). HA rest_command.sunday_input_dashboard
and sunday_input_manage use existing local secret authorization; the card contains no token.
HA resources/REST commands were reloaded, and Sunday2 restarted. HA itself was not restarted
for this dashboard. Selections apply immediately without a restart or model call/device action.

Memory scope applies to recorder reads, captured candidates, old evidence reduction,
consolidation, related-memory retrieval, inference intake and reads. Excluded queued candidates
are held, not allowed into a model. Existing canonical memories remain stored and can become
eligible again when reselected; this is not a dashboard removal/tombstone. Earlier deliberately
retired kitchen patterns remain retired. In-flight memory proposals are held on policy changes.

Proactivity scope applies to world selection, events, action receipts, scenes/automations,
context, history investigations, learned expectations, queues and final delivery. A policy
revision guard drops in-flight attention/cognition results and invalidates earlier pending
notification context after changes. Safety/freshness gates continue operating. Explicit
reactive device commands remain available regardless of enrollment. Sunday room-scoping
still applies as fallback to runtimes without this policy.

Selecting learned memories for proactivity explicitly enables that input there, without enabling
learned-memory injection into ordinary chat. It initially remains unchecked, matching prior
runtime flags. Existing master feature switches still apply and are displayed above controls.

Initial live inventory: 196 device groups; 17 checked memory-session entities; 23 checked
proactivity entities (22 effective; one linked program paused). Personal location enabled for
both. No device outside the primary room was automatically enrolled.

Backup: deployment/before-input-controls-20261023. It contains private HA configuration and
must not be published. Publisher: publish.py. Browser verification: check_ui.py (saves exercised
against an isolated fake HA adapter; live enrollment is never modified by that check).

Validation completed: 352 Python tests across memory (55), proactivity/Telegram (180),
world (44), reactive/Fast controls (43), fallback room scope (8), and new input controls (22).
Live HA mobile/desktop rendering, current checkmarks, search, individual-entity expansion,
and source/entity save interactions passed browser checks. Writes in the browser check use
a fake HA adapter. The actual HA save bridge returned 409 for a deliberately stale revision
without changing enrollment. Unauthorized GET returned 401. Choices survived a subsequent
Sunday2 restart byte-for-byte. Memory/cost endpoints returned 200; health ready and Telegram
poller running. Default sources retain the prior automation exclusion and learned-memory-read
exclusion; scripts/scenes are a separate selected source. Enrolled device groups are shown first.

Dashboard consolidation follow-up: Inputs now lives at `/sunday-hub/inputs` under the single
Sunday sidebar entry. The former `/sunday-inputs/inputs` URL is retained, hidden in the sidebar.
