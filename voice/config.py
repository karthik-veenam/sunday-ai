import json
import os
from dataclasses import dataclass, asdict


@dataclass
class Config:
    # Anthropic
    anthropic_api_key: str = ""

    # xAI (Grok) — fallback LLM
    xai_api_key: str = ""

    # Google — primary LLM (Gemini)
    google_api_key: str = ""

    # OpenAI — alternative LLM backend
    openai_api_key: str = ""
    llm_provider: str = "gemini"       # "gemini" or "openai"
    openai_voice_model: str = ""       # override default gpt-4.1-mini
    openai_chat_model: str = ""        # override default gpt-4.1

    # Groq — STT (Whisper)
    groq_api_key: str = ""

    # ElevenLabs — TTS
    elevenlabs_api_key: str = ""

    # FireTV
    firetv_ip: str = ""

    # Home Assistant
    ha_url: str = "http://homeassistant.local:8123"
    ha_token: str = ""

    # MQTT
    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883

    # Audio
    mic_device: str = "plughw:3,0"
    stt_silence_threshold: int = 700   # RMS below this = silence
    stt_speech_threshold: int = 1200   # RMS above this = speech started

    # Models
    wakeword_model: str = "/home/djpi/openwakeword-models/sunday.onnx"
    wakeword_threshold: float = 0.5
    wakeword_test_mode: bool = False
    wake_enabled: bool = True
    whisper_model: str = "tiny-int8"
    whisper_data_dir: str = "/home/djpi/data"
    piper_model: str = "/home/djpi/piper-data/en_GB-southern_english_female-low.onnx"
    piper_length_scale: float = 0.9

    # Sounds
    awake_sound: str = "/home/djpi/sounds/awake.wav"
    done_sound: str = "/home/djpi/sounds/done.wav"

    # Reflection
    roka_phone_ip: str = ""
    reflection_interval: int = 3600

    # Tapo / Kasa
    tapo_username: str = ""
    tapo_password: str = ""
    tapo_devices: dict = None  # friendly name → IP

    # Tuya Cloud
    tuya_api_key: str = ""
    tuya_api_secret: str = ""
    tuya_region: str = "in"
    tuya_geyser_id: str = ""

    # Presence
    presence_devices: dict = None  # ip → label, e.g. {"192.168.88.56": "phone", "192.168.88.9": "macbook"}

    # Telegram
    tavily_api_key: str = ""
    smartthings_token: str = ""
    smartthings_soundbar_id: str = ""
    telegram_bot_token: str = "REPLACE_ME"
    telegram_chat_id: str = "REPLACE_ME"
    telegram_users: dict = None  # chat_id → name, e.g. {"REPLACE_ME": "Karthik", "8650118400": "Roopa"}

    # Calendars (ICS URLs — Outlook free/busy + Google Calendar)
    outlook_ics_url: str = ""
    gcal_ics_url: str = ""  # Google Calendar ICS (for badminton etc.)

    # Hogar Z-wave hub
    hogar_ip: str = "192.168.88.7"
    hogar_home_id: int = 4783
    hogar_user_id: str = "REPLACE_ME"
    hogar_token: str = ""
    hogar_device_map: dict = None  # friendly name → devid suffix, e.g. {"fan": "4-9", "light 1": "2-7"}

    @classmethod
    def load(cls, path: str = "config.json") -> "Config":
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        return cls()

    def save(self, path: str = "config.json") -> None:
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)
