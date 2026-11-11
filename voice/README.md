# Voice runtime and transitional workers

`main.py` owns the voice loop; `voice_brain.py` forwards conversation turns to the rebuilt brain. This directory also contains retained transitional workers and household adapters. Do not delete or start them indiscriminately.

Pipeline: selected PCM source → openWakeWord → optional Sherpa verifier → live/batch STT → rebuilt brain → TTS/playback. The pinned runtime is in `../wake/runtime/` and two selector-compatible model bundles in `../wake/models/`.

Copy `config.example.json` to a local `config.json`, and `wake_verifier.example.json` to `wake_verifier.json`. Supply credentials only locally. `wake_profile.py` watches `input_select.sunday_wake_model`: the November7 classifier or previous V5/V6-filter model, both threshold0.99. The previous choice ensures Sherpa verification is enabled; current preserves the verifier switch. Switching occurs only while waiting for wake audio.

Audio cleanup, adaptive speech detection, silence/speech thresholds and live/batch recognition are separate controls. The No Listening helper remains an independent gate. This code assumes the existing audio tooling and paths; see deployment notes before running on another host.

The original requirements manifest was not maintained consistently for this runtime. `requirements.snapshot.txt` records selected installed packages; it is reference evidence, not a complete cross-platform lockfile. OS audio tools, optional integrations and provider models are separate requirements.
