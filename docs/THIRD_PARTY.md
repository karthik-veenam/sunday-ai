# Third-party components and assets

No blanket license is newly granted by this source export. Keep upstream notices when redistributing third-party code.

Dependencies include Home Assistant, openWakeWord/ONNX Runtime, Sherpa-ONNX, PyYAML, aiohttp, provider SDKs, MQTT/Paho, M5Unified/M5GFX/StackChan BSP, Arduino ESP32 and arduinoWebSockets. Consult their upstream licenses and the versions pinned by component requirements/build files.

`wake/runtime/openwakeword/` is the pinned local runtime used by the deployed detector. Its provenance is the local evaluated openWakeWord runtime; it should be reviewed against upstream before public redistribution. Frontend model assets are copied byte-for-byte from the evaluated local deployment. Wake classifier assets contain model weights, not source recordings. No training audio is included.

Provider STT/TTS/LLM service access and model licenses are separate from this repository. Download large Sherpa/Whisper/Piper/Gemma/Qwen assets from their official distributions where needed; they are not bundled here.

The vendored runtime and model checksums are recorded in `wake/models/SHA256SUMS`. The private repository is an operational backup, not a licensing audit.
