import logging
import threading
from pathlib import Path
from typing import Optional

import librosa
import numpy as np
import torch
from whisper import Whisper, audio, load_model

from modules.core.handler.datacls.stt_model import STTConfig
from modules.core.models.stt.STTModel import STTModel, TranscribeResult
from modules.core.models.stt.whisper.writer import get_writer
from modules.core.pipeline.processor import NP_AUDIO
from modules.devices import devices


# typing
class WhisperSegment:
    seek: int
    start: float
    end: float
    text: str
    tokens: list
    temperature: float
    avg_logprob: float
    compression_ratio: float
    noise_level: float


# typing
class WhisperTranscribeResult(dict):
    text: str
    segments: list[WhisperSegment]
    language: Optional[str]


class WhisperModel(STTModel):
    SAMPLE_RATE = audio.SAMPLE_RATE

    lock = threading.Lock()

    logger = logging.getLogger(__name__)

    model: Optional[Whisper] = None

    def __init__(self, model_id: str):
        # example: `whisper.large` or `whisper` or `whisper.small`
        model_ver = model_id.lower().split(".")

        assert model_ver[0] == "whisper", f"Invalid model id: {model_id}"

        self.model_size = model_ver[1] if len(model_ver) > 1 else "large"
        self.model_dir = Path("./models/whisper")

        self.device = devices.get_device_for("whisper")
        self.dtype = devices.dtype

    def load(self):
        if WhisperModel.model is None:
            with self.lock:
                self.logger.info(f"Loading Whisper model [{self.model_size}]...")
                WhisperModel.model = load_model(
                    name=self.model_size,
                    download_root=str(self.model_dir),
                    device=self.device,
                )
                self.logger.info("Whisper model loaded.")
        return WhisperModel.model

    @devices.after_gc()
    def unload(self):
        if WhisperModel.model is None:
            return
        with self.lock:
            del self.model
            self.model = None
            del WhisperModel.model
            WhisperModel.model = None

    def resample_audio(self, audio: NP_AUDIO):
        sr, data = audio

        if data.dtype == np.int16:
            data = data.astype(np.float32)
            data /= np.iinfo(np.int16).max

        if sr == self.SAMPLE_RATE:
            return sr, data
        data = librosa.resample(data, orig_sr=sr, target_sr=self.SAMPLE_RATE)
        return self.SAMPLE_RATE, data

    def transcribe(self, audio: NP_AUDIO, config: STTConfig) -> TranscribeResult:
        prompt = config.prompt
        prefix = config.prefix

        language = config.language
        tempperature = config.tempperature
        sample_len = config.sample_len
        best_of = config.best_of
        beam_size = config.beam_size
        patience = config.patience
        length_penalty = config.length_penalty

        writer_options = {
            "highlight_words": config.highlight_words,
            "max_line_count": config.max_line_count,
            "max_line_width": config.max_line_width,
            "max_words_per_line": config.max_words_per_line,
        }

        format = config.format

        model = self.load()

        _, audio_data = self.resample_audio(audio=audio)

        # ref https://platform.openai.com/docs/api-reference/audio/createTranscription#audio-createtranscription-temperature
        if tempperature is None or tempperature <= 0:
            tempperature = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)

        result: WhisperTranscribeResult = model.transcribe(
            audio=audio_data,
            language=language,
            prompt=prompt,
            prefix=prefix,
            sample_len=sample_len,
            temperature=tempperature,
            best_of=best_of,
            beam_size=beam_size,
            patience=patience,
            length_penalty=length_penalty,
            word_timestamps=True,
            initial_prompt=prefix,
            fp16=self.dtype == torch.float16,
        )

        writer = get_writer(format.value)
        output = writer.write(result=result, options=writer_options)

        return TranscribeResult(
            text=output,
            segments=result.get("segments", []),
            language=result.get("language", language),
            # TODO 其他参数, 需要重写 transcribe 函数
        )


if __name__ == "__main__":
    import json

    from scipy.io import wavfile

    devices.reset_device()

    model = WhisperModel("whisper.large")

    input_audio_path = "./test_cosyvoice.wav"
    sr, input_data = wavfile.read(input_audio_path)
    input_data = input_data.astype(np.float32)
    input_data /= np.iinfo(np.int16).max

    print(f"Input audio sample rate: {sr}")

    result = model.transcribe(
        input_data, STTConfig(mid="whisper.large", language="Chinese")
    )

    print(result.text)
    with open("test_whisper_result.json", "w") as f:
        json.dump(result.__dict__, f, ensure_ascii=False, indent=2)