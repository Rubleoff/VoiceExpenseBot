import json
import shutil
import subprocess
import wave
from functools import lru_cache
from pathlib import Path

from vosk import KaldiRecognizer, Model, SetLogLevel


SetLogLevel(-1)


class SpeechRecognitionError(Exception):
    """
    Ошибка при распознавании речи.
    """
    pass


def is_voice_recognition_available(model_path: str) -> tuple[bool, str]:
    """
    Проверяет, доступен ли голосовой ввод.
    """
    if not model_path:
        return False, "не указан путь к модели Vosk"

    if not Path(model_path).exists():
        return False, f"модель Vosk не найдена по пути: {model_path}"

    if shutil.which("ffmpeg") is None:
        return False, "не установлен ffmpeg"

    return True, "OK"


@lru_cache(maxsize=1)
def get_vosk_model(model_path: str) -> Model:
    """
    Загружает модель Vosk один раз и переиспользует её.
    """
    if not Path(model_path).exists():
        raise SpeechRecognitionError(
            f"Модель Vosk не найдена по пути: {model_path}"
        )

    return Model(model_path)


def convert_ogg_to_wav(ogg_path: str, wav_path: str) -> None:
    """
    Конвертирует Telegram voice .ogg в .wav.

    Параметры итогового WAV:
    - mono;
    - 16000 Hz;
    - PCM 16-bit.
    """
    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        ogg_path,
        "-ac",
        "1",
        "-ar",
        "16000",
        "-acodec",
        "pcm_s16le",
        wav_path,
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        raise SpeechRecognitionError(
            f"ffmpeg не смог конвертировать файл: {result.stderr}"
        )


def recognize_wav(wav_path: str, model_path: str) -> str:
    """
    Распознаёт речь из WAV-файла через Vosk.
    """
    model = get_vosk_model(model_path)

    try:
        wav_file = wave.open(wav_path, "rb")
    except wave.Error as error:
        raise SpeechRecognitionError(f"Не удалось открыть WAV-файл: {error}")

    with wav_file:
        if wav_file.getnchannels() != 1:
            raise SpeechRecognitionError("WAV-файл должен быть mono.")

        if wav_file.getsampwidth() != 2:
            raise SpeechRecognitionError("WAV-файл должен быть 16-bit PCM.")

        recognizer = KaldiRecognizer(model, wav_file.getframerate())

        result_parts: list[str] = []

        while True:
            data = wav_file.readframes(4000)

            if len(data) == 0:
                break

            if recognizer.AcceptWaveform(data):
                partial_result = json.loads(recognizer.Result())
                partial_text = partial_result.get("text", "").strip()

                if partial_text:
                    result_parts.append(partial_text)

        final_result = json.loads(recognizer.FinalResult())
        final_text = final_result.get("text", "").strip()

        if final_text:
            result_parts.append(final_text)

    return " ".join(result_parts).strip()


def recognize_ogg(ogg_path: str, wav_path: str, model_path: str) -> str:
    """
    Полный цикл распознавания:

    .ogg -> .wav -> текст
    """
    convert_ogg_to_wav(ogg_path, wav_path)
    return recognize_wav(wav_path, model_path)