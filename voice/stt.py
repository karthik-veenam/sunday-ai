import asyncio
import os
import queue
import threading
import time

import numpy as np

from mic import MicStream

from google.cloud import speech


class STT:
    RATE             = 16000
    CHUNK            = 1280   # 80ms at 16kHz

    def __init__(self, model_size: str = "base.en", data_dir: str = "/home/djpi/data",
                 groq_api_key: str = None, xai_api_key: str = None,
                 deepgram_api_key: str = None,
                 google_credentials_path: str = None,
                 google_language_code: str = "en-IN"):
        if google_credentials_path:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = google_credentials_path
        self._language = google_language_code
        self._client = speech.SpeechClient()
        print(f"[STT] Using Google Cloud STT (lang={google_language_code})")

    async def listen_and_transcribe(
        self,
        mic: MicStream,
        silence_threshold: int  = 200,
        speech_threshold: int   = 400,
        silence_seconds: float  = 0.5,
        max_seconds: float      = 15.0,
        speech_start_timeout: float = 2.0,
        on_recorded: callable   = None,
    ) -> str:
        self.last_audio = b""
        self.last_started_at = None
        feedback_audio = []
        mic.drain()

        # Use ABSOLUTE thresholds (no calibration drift after wake-word echo)
        eff_speech  = speech_threshold
        eff_silence = silence_threshold
        print(f"[STT] speech>={eff_speech} silence<={eff_silence}")

        # Speech-context biasing: phrases the user says often but Google STT gets wrong.
        # Brand names that get split (ServiceNow -> "service now"), Indian names,
        # technical terms, and household device names go here. boost=15 is moderate.
        _bias_phrases = [
            "ServiceNow", "SNOW", "Karthik", "Roopa", "Sandhya", "RoKa",
            "Sunday", "Hogar", "Tapo", "Kasa", "Broadlink",
            "Nifty", "Sensex", "NSE", "BSE", "SCB",
            "projector screen", "cove light", "foot lamp", "top light",
        ]
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=self.RATE,
            language_code=self._language,
            enable_automatic_punctuation=True,
            model="latest_short",
            speech_contexts=[speech.SpeechContext(phrases=_bias_phrases, boost=15.0)],
        )
        streaming_config = speech.StreamingRecognitionConfig(
            config=config,
            interim_results=True,
            single_utterance=False,
        )

        loop = asyncio.get_running_loop()
        audio_q: queue.Queue = queue.Queue()
        transcript_parts: list[str] = []
        recognition_done = threading.Event()

        def _request_generator():
            while True:
                data = audio_q.get()
                if data is None:
                    return
                yield speech.StreamingRecognizeRequest(audio_content=data)

        def _recognize_loop():
            try:
                responses = self._client.streaming_recognize(
                    streaming_config, _request_generator()
                )
                for response in responses:
                    for result in response.results:
                        if result.alternatives:
                            text = result.alternatives[0].transcript.strip()
                            if result.is_final:
                                if text:
                                    transcript_parts.append(text)
                                    print(f"[STT] Google final: {text!r}")
                            else:
                                if text:
                                    print(f"[STT] interim: {text!r}")
            except Exception as e:
                print(f"[STT] Google error: {e}")
            finally:
                recognition_done.set()

        recog_thread = threading.Thread(target=_recognize_loop, daemon=True)
        recog_thread.start()

        try:
            silence_limit        = int(silence_seconds      * self.RATE / self.CHUNK)
            max_chunks           = int(max_seconds          * self.RATE / self.CHUNK)
            start_timeout_chunks = int(speech_start_timeout * self.RATE / self.CHUNK)

            silent_chunks    = 0
            speech_detected  = False
            waited_chunks    = 0
            PRE_BUFFER       = 12
            pre_buf: list    = []

            for _ in range(max_chunks):
                chunk = await mic.read()
                rms   = int(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))

                if not speech_detected:
                    waited_chunks += 1
                    if waited_chunks >= start_timeout_chunks:
                        break
                    pre_buf.append(chunk)
                    if len(pre_buf) > PRE_BUFFER:
                        pre_buf.pop(0)
                    if rms >= eff_speech:
                        speech_detected = True
                        print(f"[STT] speech detected (rms={rms})")
                        self.last_started_at = time.monotonic()-len(pre_buf)*self.CHUNK/self.RATE
                        for pb in pre_buf:
                            feedback_audio.append(pb.tobytes())
                            audio_q.put(pb.tobytes())
                        pre_buf = []
                    continue

                feedback_audio.append(chunk.tobytes())
                audio_q.put(chunk.tobytes())

                if rms < eff_silence:
                    silent_chunks += 1
                else:
                    silent_chunks = 0

                if silent_chunks >= silence_limit:
                    print(f"[STT] silence detected, ending stream")
                    break

            if on_recorded:
                result = on_recorded()
                if result is not None and hasattr(result, "__await__"):
                    await result

        finally:
            self.last_audio = b"".join(feedback_audio)
            audio_q.put(None)
            # Wait up to 5s for Google to send final result
            await loop.run_in_executor(
                None, lambda: recognition_done.wait(timeout=5.0)
            )

        if not speech_detected:
            print("[STT] no speech detected")
            return ""
        result = " ".join(transcript_parts).strip()
        print(f"[STT] final transcript: {result!r}")
        return result

    async def warmup(self) -> None:
        pass
