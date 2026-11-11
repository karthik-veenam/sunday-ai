"""Home Assistant integration for the Stack-chan body, over MQTT discovery.

The body shows up in Home Assistant as ONE device, "Sunday Body", with proper entities (expression, face state, display mode, head
motion, volume, camera buttons, room light, Wi-Fi...). Nothing here is required for voice: if MQTT or Home Assistant is down, the
gateway carries on exactly as before.

Flow:  HA entity  --MQTT-->  this bridge  --WebSocket {"type":"cmd"}-->  Stack-chan
       Stack-chan --WebSocket {"type":"telemetry"}-->  this bridge  --MQTT-->  HA entities

The translation logic is in plain functions (discovery_messages / status_from_telemetry / map_command) so it can be tested alone.
"""
import asyncio
import json

BASE = "sunday/body"
DISCOVERY = "homeassistant"
AVAIL = BASE + "/availability"
GATEWAY_AVAIL = BASE + "/gateway_availability"
HOST_CONTROLS = {"reply_model", "microphone", "microphone_status", "stt_mode", "stt_cleanup", "adaptive_speech_detection", "stt_silence_threshold", "stt_speech_threshold", "stt_silence_seconds"}
STATUS = BASE + "/status"
CMD = BASE + "/cmd/"
SLEEP = BASE + "/sleep_status"
BATTERY = BASE + "/battery"

EXPRESSIONS = ["Idle", "Happy", "Grin", "Laughing", "Thinking", "Listening", "Speaking", "Surprised", "Sleepy", "Sleeping", "Delight", "Bliss", "Sad"]
FACE_STATES = ["idle", "wake", "listening", "thinking", "speaking", "sleeping"]
DISPLAYS = ["classic", "immersive"]
LED_DAY = ["Auto", "Off", "Red", "Orange", "Amber", "Green", "Cyan", "Blue", "Purple", "Pink", "White", "Warm white"]   # Auto = colours follow what Sunday is doing
LED_NIGHT = LED_DAY[1:]                                                                                               # at night: off or a (very dim) fixed colour
NIGHT_SCREENS = {"Sleep": "sleep", "Dim face": "dim"}


def _device(fw):
    return {"identifiers": ["sunday_stackchan"], "name": "Sunday Body", "manufacturer": "M5Stack",
            "model": "Stack-chan (CoreS3)", "sw_version": fw or ""}


def discovery_messages(fw=""):
    """[(topic, payload_dict)] for every entity of the body, in MQTT discovery format."""
    dev = _device(fw)

    def ent(component, obj, name, **kw):
        p = {"name": name, "unique_id": "sunday_body_" + obj, "device": dev}
        if obj in HOST_CONTROLS:
            p['availability_topic'] = GATEWAY_AVAIL
        else:
            p['availability'] = [{'topic': AVAIL}, {'topic': GATEWAY_AVAIL}]
            p['availability_mode'] = 'all'
        p.update(kw)
        return ("%s/%s/sunday_body/%s/config" % (DISCOVERY, component, obj), p)

    from stt_thresholds import LIMITS
    listening = [ent("number", key, label, command_topic=CMD+key, state_topic=STATUS,
        value_template="{{ value_json."+key+" }}", min=LIMITS[key][0], max=LIMITS[key][1],
        step=LIMITS[key][2], mode="box", **({'unit_of_measurement': 's'} if key.endswith('seconds') else {}))
        for key, label in [('stt_silence_threshold','Silence RMS threshold'),
                           ('stt_speech_threshold','Speech RMS threshold'),
                           ('stt_silence_seconds','End of speech silence')]]
    return listening + [
        ent("switch","dual_microphone_test","Dual microphone test",command_topic=CMD+"dual_mic",state_topic=STATUS,
            value_template="{{ 'ON' if value_json.dual_mic.actual_enabled else 'OFF' }}",payload_on="ON",payload_off="OFF",icon="mdi:surround-sound"),
        ent("number","mic_spacing","Microphone spacing",command_topic=CMD+"dual_mic_spacing",state_topic=STATUS,
            value_template="{{ value_json.dual_mic.spacing_mm }}",min=0,max=200,step=1,mode="box",unit_of_measurement="mm"),
        ent("select","mic_channel0_side","Microphone channel 0 side",command_topic=CMD+"dual_mic_channel",state_topic=STATUS,
            value_template="{{ value_json.dual_mic.channel0_side }}",options=["Uncalibrated","Left","Right"]),
        ent("number","mic_center_offset","Microphone center offset",command_topic=CMD+"dual_mic_offset",state_topic=STATUS,
            value_template="{{ value_json.dual_mic.center_offset_us }}",min=-500,max=500,step=1,mode="box",unit_of_measurement="µs"),
        ent("sensor","sound_direction","Sound direction",state_topic=STATUS,
            value_template="{{ value_json.dual_mic.state }}",json_attributes_topic=BASE+"/dual_mic",icon="mdi:compass"),
        ent("button","capture_dual_mic","Capture dual microphone clip",command_topic=CMD+"dual_mic_capture",payload_press="PRESS",icon="mdi:record-rec"),
        ent("select","stt_mode","STT mode",command_topic=CMD+"stt_mode",state_topic=STATUS,
            value_template="{{ value_json.stt_mode }}",options=["Live","After speech"],icon="mdi:microphone-message"),
        ent("select","reply_model","Reply model",command_topic=CMD+"reply_model",state_topic=STATUS,
            value_template="{{ value_json.reply_model }}",options=["Luna (Fast)","Claude Haiku 5.5","GPT-5.4 nano"],icon="mdi:brain"),
        ent("switch","adaptive_speech_detection","Adaptive speech detection",command_topic=CMD+"adaptive_speech_detection",state_topic=STATUS,
            value_template="{{ 'ON' if value_json.adaptive_speech_detection else 'OFF' }}",payload_on="ON",payload_off="OFF",icon="mdi:microphone-settings"),
        ent("switch","stt_cleanup","STT audio cleanup",command_topic=CMD+"stt_cleanup",state_topic=STATUS,
            value_template="{{ 'ON' if value_json.stt_cleanup else 'OFF' }}",payload_on="ON",payload_off="OFF",icon="mdi:waveform"),
        ent("select","microphone","Microphone",command_topic=CMD+"microphone",state_topic=STATUS,
            value_template="{{ value_json.microphone }}",options=["Body microphone","AIRHUG USB microphone"],icon="mdi:microphone"),
        ent("sensor","microphone_status","Microphone status",state_topic=STATUS,
            value_template="{{ value_json.microphone_status }}",json_attributes_topic=STATUS,icon="mdi:microphone-settings"),
        ent("select", "expression", "Expression", command_topic=CMD + "expression", state_topic=STATUS,
            value_template="{{ value_json.expression }}", options=["Auto"] + EXPRESSIONS, icon="mdi:emoticon-outline"),
        ent("select", "face_state", "Face state", command_topic=CMD + "state", state_topic=STATUS,
            value_template="{{ value_json.state }}", options=FACE_STATES, icon="mdi:face-recognition"),
        ent("select", "display", "Display", command_topic=CMD + "display", state_topic=STATUS,
            value_template="{{ value_json.display }}", options=DISPLAYS, icon="mdi:monitor-shimmer"),
        ent("switch", "head_motion", "Head motion", command_topic=CMD + "head_motion", state_topic=STATUS,
            value_template="{{ 'ON' if value_json.head_motion else 'OFF' }}", payload_on="ON", payload_off="OFF", icon="mdi:head-sync-outline"),
        ent("number", "volume", "Volume", command_topic=CMD + "volume", state_topic=STATUS, value_template="{{ value_json.volume }}",
            min=0, max=255, step=5, mode="slider", icon="mdi:volume-high"),
        ent("button", "take_picture", "Take picture", command_topic=CMD + "take_picture", payload_press="PRESS", icon="mdi:camera"),
        ent("switch", "camera_preview", "Camera preview", command_topic=CMD + "camera_preview", state_topic=STATUS,
            value_template="{{ 'ON' if value_json.camera_preview else 'OFF' }}", payload_on="ON", payload_off="OFF", icon="mdi:video"),
        ent("switch", "night_mode", "Night mode", command_topic=CMD + "night_mode", state_topic=STATUS,
            value_template="{{ 'ON' if value_json.forced_night_mode else 'OFF' }}", payload_on="ON", payload_off="OFF", icon="mdi:weather-night"),
        ent("select", "night_screen", "Night screen", command_topic=CMD + "night_screen", state_topic=STATUS,
            value_template="{{ value_json.night_screen }}", options=list(NIGHT_SCREENS), icon="mdi:monitor-off"),
        ent("number", "night_brightness", "Night brightness", command_topic=CMD + "night_brightness", state_topic=STATUS,
            value_template="{{ value_json.night_brightness }}", min=1, max=30, step=1, mode="slider", icon="mdi:brightness-4"),
        ent("select", "led_day", "LED colour (day)", command_topic=CMD + "led_day", state_topic=STATUS,
            value_template="{{ value_json.led_day }}", options=LED_DAY, icon="mdi:led-on"),
        ent("select", "led_night", "LED colour (night)", command_topic=CMD + "led_night", state_topic=STATUS,
            value_template="{{ value_json.led_night }}", options=LED_NIGHT, icon="mdi:led-off"),
        ent("switch","pet_proximity_guard","Petting proximity guard",command_topic=CMD+"pet_proximity_guard",state_topic=STATUS,
            value_template="{{ 'ON' if value_json.pet_proximity_guard else 'OFF' }}",payload_on="ON",payload_off="OFF",icon="mdi:hand-back-right-outline"),
        ent("number","pet_proximity_threshold","Petting proximity threshold",command_topic=CMD+"pet_proximity_threshold",state_topic=STATUS,
            value_template="{{ value_json.pet_proximity_threshold }}",min=1,max=2047,step=1,mode="box",icon="mdi:tune"),
        ent("sensor","proximity_counts","Proximity counts",state_topic=STATUS,
            value_template="{{ value_json.proximity_counts if value_json.proximity_counts is not none else 'unknown' }}",icon="mdi:gesture-tap"),
        ent("binary_sensor","petting_object_near","Object near for petting",state_topic=STATUS,
            value_template="{{ 'ON' if value_json.petting_object_near else 'OFF' }}",payload_on="ON",payload_off="OFF",icon="mdi:hand-wave-outline"),
        ent("switch", "pet_mode", "Pet mode", command_topic=CMD + "pet_mode", state_topic=STATUS,
            value_template="{{ 'ON' if value_json.pet_mode else 'OFF' }}", payload_on="ON", payload_off="OFF", icon="mdi:paw"),
        ent("switch", "pet_night", "Pet mode at night", command_topic=CMD + "pet_night", state_topic=STATUS,
            value_template="{{ 'ON' if value_json.pet_night else 'OFF' }}", payload_on="ON", payload_off="OFF", icon="mdi:paw-off"),
        ent("switch", "idle_sounds", "Idle sounds", command_topic=CMD+"idle_sounds",state_topic=STATUS,
            value_template="{{ 'ON' if value_json.idle_sounds else 'OFF' }}",payload_on="ON",payload_off="OFF",icon="mdi:music-note-outline"),
        ent("switch", "stay_awake", "Stay awake (1 hour)",command_topic=CMD+"stay_awake",state_topic=STATUS,
            value_template="{{ 'ON' if value_json.stay_awake else 'OFF' }}",payload_on="ON",payload_off="OFF",icon="mdi:coffee-outline"),
        ent("binary_sensor", "night_active", "Night rendering active",state_topic=STATUS,
            value_template="{{ 'ON' if value_json.night_mode else 'OFF' }}",payload_on="ON",payload_off="OFF",icon="mdi:weather-night"),
        ent("sensor", "sleep_status", "Sleep status",state_topic=SLEEP,value_template="{{ value_json.state }}",json_attributes_topic=SLEEP,icon="mdi:sleep"),
        ent("sensor", "sleepiness", "Sleepiness",state_topic=SLEEP,value_template="{{ value_json.sleepiness }}",unit_of_measurement="%",icon="mdi:power-sleep"),
        ent("sensor", "sleep_reason", "Sleep reason",state_topic=SLEEP,value_template="{{ value_json.reason }}",icon="mdi:help-circle-outline"),
        ent("sensor", "room_light", "Room light", state_topic=STATUS,
            value_template="{% if value_json.lux is not none %}{{ value_json.lux }}{% else %}unknown{% endif %}",
            unit_of_measurement="lx", device_class="illuminance", state_class="measurement"),
        ent("sensor", "wifi_signal", "Wi-Fi signal", state_topic=STATUS, value_template="{{ value_json.rssi }}",
            unit_of_measurement="dBm", device_class="signal_strength", state_class="measurement", entity_category="diagnostic"),
        ent("sensor", "uptime", "Uptime", state_topic=STATUS, value_template="{{ value_json.uptime }}",
            unit_of_measurement="s", device_class="duration", state_class="total_increasing", entity_category="diagnostic"),
    ]


def battery_discovery_messages(fw=""):
    """Battery entities (the body reports its battery separately from the 5-second telemetry)."""
    dev = _device(fw)
    common = {"device": dev, "availability": [{"topic": AVAIL}, {"topic": GATEWAY_AVAIL}], "availability_mode": "all", "state_topic": BATTERY}
    return [
        ("%s/sensor/sunday_body/battery/config" % DISCOVERY,
         dict(common, name="Battery", unique_id="sunday_body_battery", value_template="{{ value_json.percent }}",
              unit_of_measurement="%", device_class="battery", state_class="measurement")),
        ("%s/binary_sensor/sunday_body/battery_charging/config" % DISCOVERY,
         dict(common, name="Charging", unique_id="sunday_body_battery_charging", device_class="battery_charging",
              value_template="{{ 'ON' if value_json.charging == 1 else 'OFF' }}", payload_on="ON", payload_off="OFF")),
    ]


def status_from_telemetry(t):
    """The body's compact telemetry message -> the JSON the HA entities read."""
    lux = t.get("lux")
    return {
        "expression": t.get("expr") if t.get("ov") else "Auto",   # an expression override shows its name; otherwise the face is automatic
        "face_expression": t.get("expr"),
        "state": t.get("st"),
        "display": t.get("disp"),
        "head_motion": bool(t.get("pet")),
        "volume": t.get("vol"),
        "camera_preview": bool(t.get("live")),
        "lux": lux if isinstance(lux, (int, float)) and lux >= 0 else None,
        "proximity_counts":t.get("px") if isinstance(t.get("px"),int) and t["px"]>=0 else None,
        "petting_object_near":bool(t.get("pxn")),
        "pet_proximity_guard":bool(t.get("pxg")),
        "pet_proximity_threshold":t.get("pxt"),
        "pet_mode": t.get("pe", 1) != 0,
        "pet_night": bool(t.get("pn")),
        "night_mode": bool(t.get("n")),
        "night_screen": "Sleep" if t.get("ns") == 0 else "Dim face",
        "night_brightness": t.get("nb"),
        "led_day": t.get("ld"),
        "led_night": t.get("ln"),
        "rssi": t.get("rssi"),
        "uptime": t.get("up"),
    }


def map_command(name, payload):
    """HA command topic suffix + payload -> (device command name, value) or None if it is not valid."""
    payload = (payload or "").strip()
    from stt_thresholds import LIMITS, validate
    if name in LIMITS:
        try:
            return (name, str(validate(name, payload)))
        except (ValueError, TypeError):
            return None
    if name=="dual_mic":return (name,"1" if payload.upper()=="ON" else "0") if payload.upper() in ("ON","OFF") else None
    if name=="dual_mic_capture":return (name,"") if payload=="PRESS" else None
    if name=="dual_mic_channel":return (name,payload) if payload in ("Uncalibrated","Left","Right") else None
    if name in ("dual_mic_spacing","dual_mic_offset"):
        try:
            v=float(payload);valid=0<=v<=200 if name=="dual_mic_spacing" else -500<=v<=500
            return (name,payload) if valid else None
        except ValueError:return None
    if name == "expression":
        if payload.lower() == "auto":
            return ("expression", "auto")
        return ("expression", payload) if payload in EXPRESSIONS else None
    if name == "state":
        return ("state", payload.lower()) if payload.lower() in FACE_STATES else None
    if name == "display":
        return ("display", payload.lower()) if payload.lower() in DISPLAYS else None
    if name == "reply_model":
        return (name,payload) if payload in ("Luna (Fast)","Claude Haiku 5.5","GPT-5.4 nano") else None
    if name == "stt_mode":
        return (name,payload) if payload in ("Live","After speech") else None
    if name in ("stt_cleanup","adaptive_speech_detection"):
        return (name,"1" if payload.upper()=="ON" else "0") if payload.upper() in ("ON","OFF") else None
    if name == "microphone":
        return (name,payload) if payload in ("Body microphone","AIRHUG USB microphone") else None
    if name == "head_motion":
        return ("pet_motion", "1" if payload.upper() == "ON" else "0") if payload.upper() in ("ON", "OFF") else None
    if name == "volume":
        try:
            return ("volume", str(max(0, min(255, int(float(payload))))))
        except ValueError:
            return None
    if name == "pet_proximity_guard":
        return ("pet_proximity","1" if payload.upper()=="ON" else "0") if payload.upper() in ("ON","OFF") else None
    if name == "pet_proximity_threshold":
        try:
            value=int(payload)
            return (name,str(value)) if 1<=value<=2047 else None
        except (ValueError,TypeError):return None
    if name in ("pet_mode", "pet_night"):
        return ("pet_enable" if name == "pet_mode" else "pet_night", "1" if payload.upper() == "ON" else "0") if payload.upper() in ("ON", "OFF") else None
    if name in ("idle_sounds","stay_awake"):
        return (name,"1" if payload.upper()=="ON" else "0") if payload.upper() in ("ON","OFF") else None
    if name == "night_mode":
        return ("night", "1" if payload.upper() == "ON" else "0") if payload.upper() in ("ON", "OFF") else None
    if name == "night_screen":
        return ("night_screen", NIGHT_SCREENS[payload]) if payload in NIGHT_SCREENS else None
    if name == "night_brightness":
        try:
            return ("night_brightness", str(max(1, min(30, int(float(payload))))))
        except ValueError:
            return None
    if name in ("led_day", "led_night"):
        options = LED_DAY if name == "led_day" else LED_NIGHT
        match = next((o for o in options if o.lower() == payload.lower()), None)
        return (name, match) if match else None
    if name == "take_picture":
        return ("snap", "1")
    if name == "camera_preview":
        return ("live", "1" if payload.upper() == "ON" else "0") if payload.upper() in ("ON", "OFF") else None
    return None


class HaBridge:
    """Runs the MQTT client in its own thread and hands commands to the asyncio hub."""

    def __init__(self, loop, hub, host, port, log):
        import paho.mqtt.client as mqtt
        self.loop, self.hub, self.host, self.port, self.log = loop, hub, host, port, log
        self.online = False
        self.fw = ""
        self.last_status = None
        self.last_sleep = None
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sunday-body-gateway")
        self.client.will_set(GATEWAY_AVAIL, "offline", retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = lambda *a, **k: self.log("mqtt: disconnected from the broker (will retry)")
        self.client.reconnect_delay_set(2, 30)

    def start(self):
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()

    def stop(self):
        try:
            self.client.publish(AVAIL, "offline", retain=True)
            self.client.publish(GATEWAY_AVAIL, "offline", retain=True)
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    # ---- called from the asyncio side
    def device_online(self, info):
        self.fw = (info or {}).get("fw") or ""
        self.online = True
        self._publish_discovery()
        self.client.publish(AVAIL, "online", retain=True)

    def device_offline(self):
        self.online = False
        self.client.publish(AVAIL, "offline", retain=True)

    def telemetry(self, t):
        self.last_status = status_from_telemetry(t)
        self.runtime_status()

    def runtime_status(self):
        if self.last_status is None:self.last_status={}
        from stt_thresholds import read as listening_settings
        self.last_status.update(listening_settings())
        self.last_status['dual_mic']=self.hub.dual_mic.status()
        self.client.publish(BASE+'/dual_mic',json.dumps(self.last_status['dual_mic']),retain=True)
        from stt_mode import live_enabled
        self.last_status['stt_mode']='Live' if live_enabled() else 'After speech'
        from adaptive_detection import enabled as adaptive_enabled
        self.last_status['adaptive_speech_detection']=adaptive_enabled()
        from stt_processing import enabled
        self.last_status['stt_cleanup']=enabled(self.hub.microphone.selected)
        model=getattr(self.hub,'reply_model',None)
        self.last_status['reply_model']=model.get('label','Luna (Fast)') if model else 'Luna (Fast)'
        mic=self.hub.microphone.status()
        self.last_status['microphone']=mic['selected']
        self.last_status['microphone_status']=mic['error'] or ('Active: '+str(mic['active']))
        self.last_status['microphone_active']=mic['active']
        self.last_status['idle_sounds']=self.hub.alive.flags.get('idle_sounds',False)
        sleep=getattr(self.hub,'sleep',None)
        snapshot=sleep.cached if sleep else None
        self.last_status['forced_night_mode']=snapshot['force_night'] if snapshot else self.last_status.get('night_mode',False)
        self.last_status['stay_awake']=snapshot.get('stay_awake',False) if snapshot else False
        self.client.publish(STATUS,json.dumps(self.last_status),retain=True)

    def sleep_status(self,snapshot):
        self.last_sleep=snapshot
        self.client.publish(SLEEP,json.dumps(snapshot),retain=True)
        self.runtime_status()

    # ---- MQTT callbacks (paho thread)
    def battery(self, info):
        """Publish the body's battery (called by the gateway when the body reports it)."""
        self.last_battery = info
        self.client.publish(BATTERY, json.dumps(info), retain=True)

    def _publish_discovery(self):
        for topic, payload in discovery_messages(self.fw):
            self.client.publish(topic, json.dumps(payload), retain=True)
        for topic, payload in battery_discovery_messages(self.fw):
            self.client.publish(topic, json.dumps(payload), retain=True)
        if getattr(self, 'last_battery', None):
            self.client.publish(BATTERY, json.dumps(self.last_battery), retain=True)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self.log("mqtt: connected to the broker", self.host, "reason", reason_code)
        client.publish(GATEWAY_AVAIL, "online", retain=True)
        client.subscribe([(CMD + "+", 0), (DISCOVERY + "/status", 0)])
        self._publish_discovery()
        if self.last_sleep is not None:client.publish(SLEEP,json.dumps(self.last_sleep),retain=True)
        client.publish(AVAIL, "online" if self.online else "offline", retain=True)
        if self.last_status is not None:
            client.publish(STATUS, json.dumps(self.last_status), retain=True)

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", "replace")
            if msg.topic == DISCOVERY + "/status":
                if payload == "online":                       # Home Assistant restarted: announce everything again
                    self._publish_discovery()
                    client.publish(GATEWAY_AVAIL, "online", retain=True)
                    client.publish(AVAIL, "online" if self.online else "offline", retain=True)
                    if self.last_status is not None:
                        client.publish(STATUS, json.dumps(self.last_status), retain=True)
                return
            name = msg.topic[len(CMD):]
            mapped = map_command(name, payload)
            if mapped is None:
                self.log("mqtt: ignored command", name, repr(payload)[:40])
                return
            future=asyncio.run_coroutine_threadsafe(self.hub.body_cmd(*mapped), self.loop)
            def done(f):
                try:f.result()
                except Exception as error:self.log("mqtt: command failed",mapped[0],str(error)[:160])
            future.add_done_callback(done)
        except Exception as e:
            self.log("mqtt: bad message:", type(e).__name__)
