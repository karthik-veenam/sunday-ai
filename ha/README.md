# Home Assistant integration and dashboards

`custom_components/sunday2/` contains the conversation integration for the rebuilt brain. Configure it using its config flow and a locally provisioned daemon token.

`dashboards/` exports Sunday Inputs/Voice/Debug, Memories, Costs, Body, Feelings, Device Patterns, Automations and the RoKa Sunday view. These are configuration snapshots, not a complete HA backup. They refer to helper entities, REST commands, integration entities and custom frontend resources from the live installation.

`examples/automations.example.yaml` contains selected Sunday-created/managed automations and the bedroom Auto Climate example. Review before importing: these use house-specific entity IDs and may execute physical actions when enabled. Do not blindly merge with existing automation IDs.

The Auto Climate master boolean and underlying automation are distinct. The engine is configured to load enabled; the dashboard master determines whether it acts. “Cool to target, then stop” completes and disarms the master, whereas “Maintain temperature” can resume cooling. Deferred follow-up target changes belong after completion.

Dashboard JavaScript is in component directories and `www/`. Configure resource URLs for your HA installation. Private HA `.storage`, credentials and entity-state history are excluded.
