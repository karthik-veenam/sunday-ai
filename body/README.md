# Stack-chan firmware

## Version status at export

| Tree | Status |
|---|---|
| `firmware-2.7.5/` | Source tree whose firmware tag matches the running pre-display `sunday-face-2.7.5-dual-mic-test` image |
| `experimental-display/` | Later display code plus built2.8.4 Wi-Fi-recovery changes; **not the deployed firmware** |
| `display-worker/` | Contextual display host worker; disabled after user-requested rollback |

The retained production rollback binary was hash-verified and flashed before this export. Binaries and original flash dumps remain outside Git. The copied2.7.5 source tag matches the runtime, but this export does not claim a newly reproduced byte-identical build.

## Build

```bash
cd body/firmware-2.7.5
cp src/secrets.h.example src/secrets.h
# Fill local Wi-Fi, gateway and diagnostic credentials.
pio run -e stackchan
```

PlatformIO build configuration currently contains Pi-specific tooling paths. Adapt `core_dir` to your machine as needed. Dependencies are M5Unified, M5GFX, StackChan BSP, Arduino ESP32, arduinoWebSockets and the listed library dependencies. Native toolchain/download caches are intentionally absent.

The experimental2.8.4 source fixes a confirmed Wi-Fi fallback bug: AP-only setup mode disabled station retries indefinitely. The staged change uses AP+STA, retries saved Wi-Fi every30 seconds, and closes recovery AP after reconnect. The native policy test is under `../tests/regressions/` and requires an adjusted include path or the documented compiler flag.

Do not flash by choosing the highest version number. Deployment and physical acceptance are separate steps. Preserve NVS/Wi-Fi configuration and known-good application backups; do not erase flash as a routine update.
