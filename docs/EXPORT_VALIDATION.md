# Initial export validation

Validated locally before the initial push:

- 534 Python source files parsed without runtime imports.
-Source packaging check found no forbidden credential files, state databases, recordings or build caches (the two known non-speech chime WAVs are intentional assets).
-Redacted Gitleaks source scan returned0 findings after known-value sanitization.
-Two wake-profile regression tests passed, including a failed model load retaining the previous model and the old profile enabling its verifier.
-Three MQTT availability regression tests passed, separating Pi settings from physical body availability and validating gateway last-will publication.
-Native C++ Wi-Fi-recovery policy test passed.
-Wake-model file checksums generated and verified.

No full production test suite, firmware rebuild, hardware flash, appliance action, provider call or service restart was performed for this repository export. Existing runtime data and production projects were left in place. The legacy MQTT suite has documented baseline failures; these are not represented as passing.

The initial repository is a sanitized source snapshot, not a byte-for-byte copy of the Pi or a complete HA backup. Configure credentials, optional dependencies, helper entities, REST resources and legacy paths before deploying elsewhere.
