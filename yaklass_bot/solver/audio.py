"""Аудирование: загрузка аудиозаписей задания и два способа дать их модели.

transcribe — расшифровка через OpenAI-совместимый /audio/transcriptions (Whisper и аналоги), текст идёт в промпт;
             работает с любой основной моделью.
native     — mp3 прикладывается к запросу chat/completions как input_audio; нужна модель, понимающая звук
             (Gemini, gpt-4o-audio и т.п.).
off        — задания с аудио не решаются (AudioUnsupported).

Если у сервиса расшифровки нет /audio/transcriptions (404/405 — частый случай, когда адрес STT не задан и берётся
адрес основной модели не от OpenAI), transcribe сам переходит на native: записи уходят модели (см. pipeline)."""
from __future__ import annotations

import dataclasses
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import requests

from ..models import Task
from .base import ProviderUnavailable, SolveError

MODES = ("transcribe", "native", "off")
MAX_BYTES = 25 * 1024 * 1024          # предел OpenAI /audio/transcriptions; уроки ЯКласса — единицы мегабайт
MAX_CLIPS = 4
TIMEOUT = (10, 180)
# формат по content-type / расширению -> имя для input_audio и для имени файла в multipart
FORMATS = {"audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
           "audio/ogg": "ogg", "audio/webm": "webm", "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "aac",
           "audio/flac": "flac"}
# начало detail ответа 422 сервера на /v1/solve, когда задание с аудио решить нечем (клиент -> AudioUnsupported)
REMOTE_PREFIX = "аудирование: "
NOT_AUDIO = ("text/", "image/", "application/json", "application/xml", "application/xhtml")
EXTS = {"mp3", "wav", "ogg", "oga", "webm", "m4a", "mp4", "aac", "flac"}


class AudioUnsupported(SolveError):
    """Задание с аудио, а слушать нечем (аудирование выключено, запись не скачалась, расшифровка не удалась).
    Такое задание пропускается как «без поддержки», а не считается ошибкой модели."""


class SttNotFound(AudioUnsupported):
    """У адреса расшифровки нет /audio/transcriptions (или модели STT): ответ 404/405.
    clips — уже скачанные записи задания, чтобы при переходе на native не скачивать их снова."""

    def __init__(self, msg: str, clips: list | None = None):
        super().__init__(msg)
        self.clips = clips or []


STT_MISSING = (404, 405)


@dataclass
class Audio:
    data: bytes
    format: str                       # mp3 | wav | ...


AudioLoader = Callable[[str], "Audio | None"]


def _format(url: str, mime: str) -> str:
    if mime in FORMATS:
        return FORMATS[mime]
    ext = url.lower().split("?")[0].rsplit(".", 1)[-1]
    return {"oga": "ogg", "mp4": "m4a"}.get(ext, ext) if ext in EXTS else ""


def download(url: str, allow_redirects: bool = True) -> Audio | None:
    """None — запись не скачалась, слишком большая или это не звук."""
    try:
        with requests.get(url, timeout=TIMEOUT, allow_redirects=allow_redirects, stream=True) as r:
            r.raise_for_status()
            mime = r.headers.get("content-type", "").split(";")[0].strip().lower()
            fmt = _format(url, mime)
            if not fmt or mime.startswith(NOT_AUDIO):
                return None
            chunks, size = [], 0
            for chunk in r.iter_content(64 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    return None
                chunks.append(chunk)
    except requests.RequestException:
        return None
    data = b"".join(chunks)
    return Audio(data, fmt) if data else None


def resolve_key(conf: dict) -> str:
    """Ключ как у ApiProvider: переменная окружения api_key_env, затем файл api_key_file, затем api_key."""
    env = conf.get("api_key_env", "")
    key = os.environ.get(env, "") if env else ""
    f = conf.get("api_key_file", "")
    if not key and f and Path(f).expanduser().is_file():
        key = Path(f).expanduser().read_text(encoding="utf-8").strip()
    return key or conf.get("api_key", "")


class Transcriber:
    """POST {base_url}/audio/transcriptions (OpenAI, Groq, локальный faster-whisper-server и т.п.)."""

    def __init__(self, base_url: str, model: str, api_key: str = ""):
        self.url = base_url.rstrip("/") + "/audio/transcriptions"
        self.model, self.key = model, api_key

    def transcribe(self, clip: Audio, language: str = "") -> str:
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        data = {"model": self.model, "response_format": "json", "temperature": "0"}
        if language:
            data["language"] = language
        try:
            r = requests.post(self.url, headers=headers, data=data, timeout=TIMEOUT,
                              files={"file": (f"audio.{clip.format}", clip.data, f"audio/{clip.format}")})
        except requests.RequestException as e:
            raise ProviderUnavailable(f"расшифровка недоступна ({e.__class__.__name__})") from e
        if r.status_code in STT_MISSING:
            raise SttNotFound(f"расшифровка: API {r.status_code}: {r.text[:200]}")
        if r.status_code != 200:
            raise SolveError(f"расшифровка: API {r.status_code}: {r.text[:200]}")
        try:
            text = r.json().get("text", "")
        except ValueError:
            text = r.text                       # некоторые серверы отдают text/plain
        return str(text).strip()


@dataclass
class AudioConf:
    mode: str = "transcribe"
    base_url: str = ""                # адрес STT; пусто — как у основной модели
    model: str = "whisper-1"
    api_key: str = ""

    @classmethod
    def from_dict(cls, d: dict | None, api: dict | None = None) -> "AudioConf":
        """d — секция аудио; api — секция основной модели: из неё берутся адрес и ключ, если у STT они не заданы."""
        d, api = d or {}, api or {}
        mode = str(d.get("mode", "transcribe")).strip().lower()
        base = str(d.get("base_url", "") or "").strip()
        key = resolve_key(d)
        if not base:
            base = str(api.get("base_url", "") or "")
            key = key or resolve_key(api)
        return cls(mode=mode if mode in MODES else "off", base_url=base,
                   model=str(d.get("model", "") or "whisper-1").strip(), api_key=key)


class AudioHelper:
    """Готовит задание с аудио к решению: расшифровывает (transcribe) или скачивает записи для модели (native)."""

    def __init__(self, conf: AudioConf, loader: AudioLoader = download, transcriber: Transcriber | None = None):
        self.conf, self.loader = conf, loader
        self.transcriber = transcriber
        # сервис расшифровки ответил 404/405 — дальше (пока жив объект) сразу native, без лишних запросов к STT
        self.stt_missing = False
        if self.transcriber is None and conf.mode == "transcribe" and conf.base_url:
            self.transcriber = Transcriber(conf.base_url, conf.model, conf.api_key)

    @property
    def mode(self) -> str:
        return self.conf.mode

    def _clips(self, task: Task) -> list[Audio]:
        if not task.audio:
            raise AudioUnsupported("в задании есть аудио, но ссылку на запись найти не удалось")
        if len(task.audio) > MAX_CLIPS:
            raise AudioUnsupported(f"слишком много аудиозаписей ({len(task.audio)})")
        clips = []
        for i, url in enumerate(task.audio, 1):
            clip = self.loader(url)
            if clip is None:
                raise AudioUnsupported(f"не удалось скачать аудиозапись №{i}")
            clips.append(clip)
        return clips

    def transcribed(self, task: Task, language: str = "", log: Callable[[str], None] = lambda m: None) -> Task:
        """Копия задания с заполненным audio_text."""
        if self.transcriber is None:
            raise AudioUnsupported("не задан адрес сервиса расшифровки аудио")
        texts, clips = [], self._clips(task)
        for i, clip in enumerate(clips, 1):
            try:
                text = self.transcriber.transcribe(clip, language)
            except SttNotFound as e:
                self.stt_missing = True
                raise SttNotFound(f"аудиозапись №{i} не расшифрована: {e}", clips) from e
            except SolveError as e:
                raise AudioUnsupported(f"аудиозапись №{i} не расшифрована: {e}") from e
            if not text:
                raise AudioUnsupported(f"расшифровка аудиозаписи №{i} пустая")
            log(f"  аудио №{i}: расшифровано {len(text)} симв.")
            texts.append(text)
        return dataclasses.replace(task, audio_text=texts)

    def clips(self, task: Task) -> list[Audio]:
        return self._clips(task)
