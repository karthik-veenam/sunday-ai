# Testing

Repository packaging checks are offline and must not send device commands, use live provider keys, write production logs or create bot pollers.

```bash
python3 tools/check-export.py
python3 tools/check-python.py
```

Brain tests live in `brain/tests/`. Prefer targeted modules for a change. Some tests reference the deployed `/home/djpi/...` layout, local state or fixture files; use the documented layout or explicit stubs. Do not point a test environment at production state merely to make it pass.

Gateway tests are in `audio/tests/`. The older `test_ha_mqtt.py` contains stale entity-count/option assertions and incomplete mocks: its pre-change baseline had3 failures and2 errors. The body-availability assertion was intentionally superseded when host controls were decoupled from body availability. New focused regression tests are included under `tests/regressions/`.

Body uses PlatformIO, with native C++ tests for pure policy components where provided. Build the requested firmware version only. Building does not authorize flashing. The staged2.8.4 recovery policy has tests for setup hotspot timing, repeated station retries, restoration, missing credentials and millis rollover.

The exported Python files are checked by parsing without imports, avoiding hardware access and missing optional packages. A successful parse does not prove dependency compatibility or runtime correctness.

Validation of this initial export is documented in `docs/EXPORT_VALIDATION.md`; it distinguishes source checks from physical acceptance testing.
