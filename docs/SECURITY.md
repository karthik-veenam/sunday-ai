# Security and private data

This is a private source repository. It intentionally excludes:

- Live API keys, HA tokens, Telegram bot credentials, Wi-Fi passwords, gateway authentication keys and private calendar URLs.
- Personal conversations, memory content, locations/presence histories, costs, receipts, recordings and negative audio datasets.
- Active JSON/SQLite state, WAL/SHM files, deployment backups, request captures and environment files.
- Firmware binaries, downloaded large provider models, Python environments, compiler caches and credentials directories.

Older hardcoded credentials were sanitized in the exported copies only. Production files were not rewritten. `REPLACE_ME` denotes a value requiring local configuration. Never copy a production configuration into Git to make a fresh checkout work.

Use independent tokens for development; never start another bot poller with the live token. HA and gateway APIs require their existing authentication. No credential should be embedded in a remote URL, README, test fixture or screenshot.

Before committing, run `tools/check-export.py` and a redacted gitleaks scan. Gitignore is preventative, not proof of safety: explicitly tracked files and history also need scanning. This repository starts with a fresh sanitized history; it does not import existing local histories.

A private repository does not make a leaked credential safe. If any live secret is ever committed, remove it from history and revoke/rotate it through its provider.
