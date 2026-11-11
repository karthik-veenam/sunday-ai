# Deployment reference

The live machine runs components from separate `/home/djpi/...` directories. The repository changes source organization, not the running deployment.

| Repository | Existing runtime path |
|---|---|
| brain | /home/djpi/sunday2-rebuild |
| voice | /home/djpi/sunday |
| audio | /home/djpi/sunday-audio |
| wake/sherpa | /home/djpi/sunday-sherpa-verifier |
| wake/diagnostics | /home/djpi/sunday-verifier-notifications |
| wake/runtime | /home/djpi/sunday-openwakeword-colab/full-suite-v1/runtime |
| wake/models/* | /home/djpi/openwakeword-models/* |
| body/display-worker | /home/djpi/sunday-glance-20261107/host — disabled |

Many modules retain legacy absolute paths. `python3 tools/local-layout.py` prints the expected mappings. With an explicit `--apply` it can create missing symlinks on a fresh machine; it refuses to overwrite any existing live directory. It does not install dependencies, rewrite secrets or start services.

## Python services

Create environments at the interpreter paths named by each service, or adapt the unit files consistently. Brain dependencies are in `brain/requirements.txt` and `requirements.lock`. Gateway/voice/Sherpa examples document additional native/audio/model requirements. Keep development configurations and runtime data outside Git.

Copy `brain/config.example.json` and `voice/config.example.json` to local `config.json` files. The schemas are based on the existing installation but all credentials must be supplied anew. Runtime code may create its own state; establish permissions and directories deliberately.

## systemd

`systemd/` contains reference unit definitions; drop-ins can override ownership, environment or paths and must be reviewed. In particular preserve the production Telegram ownership environment and audio backend settings. Unit files are not automatically enabled by this repository. `sunday-glance.service` is historical and must stay disabled unless the display feature is explicitly redeployed.

Use separate provider credentials and non-production bot tokens for a new installation. Never start old and rebuilt Telegram owners together.

## Home Assistant

Install `ha/custom_components/sunday2` into HA's `custom_components/` and configure its authenticated brain endpoint through its existing integration flow. Import dashboards selectively. Entity IDs, HA helpers, REST commands and local resource URLs must match the installation. See `ha/README.md`.

## Firmware

See `body/README.md`. Copy the example secrets header locally and build only the intended version. No image upload is part of setup. Keep known-good application/flash backups outside Git and verify model/MAC identity before any authorized flash.
