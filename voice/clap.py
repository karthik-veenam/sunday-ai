"""
Clap detector — reads from a MicStream subscriber queue, identifies 1/2/3 clap
sequences, then fires a Home Assistant event 'sunday_clap' with {"count": N}.

Automation trigger:
  platform: event
  event_type: sunday_clap
  event_data:
    count: 2        # 1=single, 2=double, 3=triple

MQTT binary sensors (requires HA MQTT integration) are also published via
autodiscovery to sunday/clap, payload "1"/"2"/"3".
"""
import asyncio
import json
import time
import numpy as np
import paho.mqtt.client as _mqtt

# ── Tuning ─────────────────────────────────────────────────────────────────────
# A clap:        silent → very loud in ONE chunk (sharp onset), short duration
# A bump/knock:  gradual rise OR loud but sustained
_CLAP_RMS_THRESHOLD  = 4000    # minimum RMS to register as a potential clap
_CLAP_ONSET_RATIO    = 6.0     # current chunk must be 6× louder than previous
_CLAP_MAX_CHUNKS     = 2       # ≤2×80ms = 160ms — crisp claps are brief
_CLAP_SETTLE_CHUNKS  = 8       # 8×80ms = 640ms silence → emit pattern
_CLAP_WINDOW_S       = 2.2     # lookback window for counting claps

# ── MQTT autodiscovery (active once HA MQTT integration is added) ──────────────
_STATE_TOPIC = "sunday/clap"
_DISCOVERY   = {
    n: {
        "name": f"Clap {'Single' if n==1 else 'Double' if n==2 else 'Triple'}",
        "unique_id": f"sunday_clap_{['single','double','triple'][n-1]}",
        "state_topic": _STATE_TOPIC,
        "payload_on": str(n),
        "payload_off": "0",
        "off_delay": 2,
        "device_class": "sound",
        "icon": "mdi:hand-clap",
        "device": {"identifiers": ["sunday_pi"], "name": "Sunday"},
    }
    for n in (1, 2, 3)
}


class ClapDetector:
    def __init__(self, mic, broker: str = "localhost", port: int = 1883,
                 ha_url: str = "http://localhost:8123", ha_token: str = ""):
        self._mic      = mic
        self._broker   = broker
        self._port     = port
        self._ha_url   = ha_url.rstrip("/")
        self._ha_token = ha_token
        self._client: _mqtt.Client | None = None

    # ── MQTT ───────────────────────────────────────────────────────────────────

    def _mqtt_connect(self) -> None:
        try:
            c = _mqtt.Client(_mqtt.CallbackAPIVersion.VERSION2)
            c.connect(self._broker, self._port, keepalive=60)
            c.loop_start()
            for n, payload in _DISCOVERY.items():
                topic = f"homeassistant/binary_sensor/sunday_clap_{n}/config"
                c.publish(topic, json.dumps(payload), retain=True)
            self._client = c
            print("[Clap] MQTT connected, autodiscovery retained")
        except Exception as e:
            print(f"[Clap] MQTT connect failed: {e}")

    def _mqtt_publish(self, count: int) -> None:
        if self._client:
            try:
                self._client.publish(_STATE_TOPIC, str(count))
            except Exception:
                pass

    # ── HA REST event ───────────────────────────────────────────────────────────

    async def _fire_ha_event(self, count: int) -> None:
        import aiohttp
        url = f"{self._ha_url}/api/events/sunday_clap"
        headers = {"Authorization": f"Bearer {self._ha_token}",
                   "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as s:
                await s.post(url, json={"count": count}, headers=headers,
                             timeout=aiohttp.ClientTimeout(total=3))
        except Exception as e:
            print(f"[Clap] HA event error: {e}")

    # ── Detection loop ──────────────────────────────────────────────────────────

    async def run(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._mqtt_connect)

        queue = self._mic.subscribe()
        print("[Clap] subscribed, entering audio loop")

        in_clap       = False
        clap_chunks   = 0
        silence_after = 0
        prev_rms      = 0          # track previous chunk for onset ratio check
        clap_times: list[float] = []

        while True:
            chunk = await queue.get()
            rms = int(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
            now = time.monotonic()

            sharp_onset = (prev_rms > 0 and rms >= prev_rms * _CLAP_ONSET_RATIO)
            if rms > 1500:
                ratio = rms / max(prev_rms, 1)
                print(f"[Clap] loud: rms={rms}, prev={prev_rms}, ratio={ratio:.1f}x, onset={sharp_onset}")
            is_loud     = rms > _CLAP_RMS_THRESHOLD

            if is_loud and (sharp_onset or in_clap):
                if not in_clap:
                    in_clap     = True
                    clap_chunks = 0
                    silence_after = 0
                clap_chunks += 1
            elif in_clap:
                # End of loud burst — validate duration
                if clap_chunks <= _CLAP_MAX_CHUNKS:
                    clap_times.append(now)
                    print(f"[Clap] hit ({clap_chunks * 80}ms, peak rms≈{rms})")
                in_clap     = False
                clap_chunks = 0
            else:
                if clap_times:
                    silence_after += 1
                    if silence_after >= _CLAP_SETTLE_CHUNKS:
                        cutoff = now - _CLAP_WINDOW_S
                        clap_times = [t for t in clap_times if t >= cutoff]
                        n = len(clap_times)
                        if 1 <= n <= 3:
                            print(f"[Clap] {n} clap(s) → firing HA event")
                            self._mqtt_publish(n)
                            asyncio.create_task(self._fire_ha_event(n))
                        clap_times    = []
                        silence_after = 0

            prev_rms = rms
