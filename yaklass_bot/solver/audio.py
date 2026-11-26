"""Аудирование: загрузка аудиозаписей задания и два способа дать их модели.

transcribe — расшифровка через OpenAI-совместимый /audio/transcriptions (Whisper и аналоги), текст идёт в промпт;
             работает с любой основной моделью.
chat       — расшифровку делает отдельная модель, понимающая звук (Gemini и т.п.), через chat/completions с input_audio;
             текст идёт в промпт основной модели, которой звук понимать не нужно.
native     — mp3 прикладывается к запросу chat/completions как input_audio; нужна модель, понимающая звук
             (Gemini, gpt-4o-audio и т.п.).
off        — задания с аудио не решаются (AudioUnsupported).

Если у сервиса расшифровки нет /audio/transcriptions (404/405 — частый случай, когда адрес STT не задан и берётся
адрес основной модели не от OpenAI), transcribe сам переходит на native: записи уходят модели (см. pipeline)."""
from __future__ import annotations

import base64
import dataclasses
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import requests

from ..models import Task
from .base import ProviderUnavailable, SolveError

MODES = ("transcribe", "chat", "native", "off")
TRANSCRIBE_MODES = ("transcribe", "chat")      # записи превращаются в текст для основной модели
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


CHAT_SYSTEM = ("You are a speech-to-text engine. You receive an audio recording and return its complete verbatim "
               "transcript in the language it is spoken in. Do not translate, summarize or comment. Never stop before "
               "the recording ends. If there are several speakers, start each line with the speaker's name or role if "
               "it is clear, otherwise with a dash. Output only the transcript.")
CHAT_USER = "Transcribe this recording completely."
# модель ответила, что звука не видит (посредник выбросил вложение) — пробуем другой способ вложить запись
NO_AUDIO = ("provide the audio", "provide or upload", "upload the audio", "attach the audio", "no audio",
            "don't see any audio", "do not see any audio", "can't hear", "cannot hear", "unable to access the audio",
            "предоставьте", "загрузите", "прикрепите", "не вижу аудио", "нет аудио")
WORDS_PER_S_MIN = 0.25                # речь — 2–3 слова/с; меньше 0,25 — расшифровка явно оборвалась
RETRY_STATUS = (408, 429, 500, 502, 503, 504)


def mp3_seconds(data: bytes) -> float | None:
    """Длительность MP3 (Layer III) по заголовку кадра; Xing/Info — точно для VBR, иначе по битрейту. None — не MP3."""
    i = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        i = 10 + ((data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F))
    n = len(data)
    while i + 4 <= n and not (data[i] == 0xFF and data[i + 1] & 0xE0 == 0xE0):
        i += 1
        if i > 64 * 1024:
            return None
    if i + 4 > n:
        return None
    h = data[i:i + 4]
    ver, layer = (h[1] >> 3) & 3, (h[1] >> 1) & 3          # ver: 3 MPEG1, 2 MPEG2, 0 MPEG2.5; layer 1 = III
    br_i, sr_i, mono = h[2] >> 4, (h[2] >> 2) & 3, (h[3] >> 6) == 3
    if layer != 1 or ver == 1 or br_i in (0, 15) or sr_i == 3:
        return None
    mpeg1 = ver == 3
    br = ((0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320) if mpeg1 else
          (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160))[br_i] * 1000
    sr = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}[ver][sr_i]
    side = (17 if mono else 32) if mpeg1 else (9 if mono else 17)
    x = i + 4 + side
    if data[x:x + 4] in (b"Xing", b"Info") and data[x + 7] & 1:
        frames = int.from_bytes(data[x + 8:x + 12], "big")
        return frames * (1152 if mpeg1 else 576) / sr
    return (n - i) * 8 / br


def _parts(clip: Audio, how: str) -> dict:
    b64 = base64.b64encode(clip.data).decode()
    if how == "data_url":                             # посредники к Gemini (inline_data) понимают так
        mime = {"mp3": "audio/mpeg", "m4a": "audio/mp4"}.get(clip.format, f"audio/{clip.format}")
        return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
    return {"type": "input_audio", "input_audio": {"data": b64, "format": clip.format}}     # формат OpenAI


class ChatTranscriber:
    """Расшифровка моделью со звуком: POST {base_url}/chat/completions с вложенной записью (Gemini и т.п.).
    Вложить запись можно двумя способами, и посредники понимают по-разному: data:audio/… в image_url (так звук
    доходит до Gemini через большинство посредников) и input_audio (формат OpenAI). Пробуем по очереди и
    запоминаем способ, который сработал."""

    WAYS = ("data_url", "input_audio")

    def __init__(self, base_url: str, model: str, api_key: str = "", retries: int = 3, pause: float = 3.0):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.key = model, api_key
        self.retries, self.pause = retries, pause
        self.way: str | None = None                   # способ, который уже сработал

    def _post(self, body: dict) -> requests.Response:
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        last = ""
        for attempt in range(self.retries):
            if attempt:
                time.sleep(self.pause * attempt)
            try:
                r = requests.post(self.url, headers=headers, json=body, timeout=TIMEOUT)
            except requests.RequestException as e:
                last = e.__class__.__name__
                continue
            if r.status_code not in RETRY_STATUS:
                return r
            last = f"API {r.status_code}: {r.text[:200]}"
        raise ProviderUnavailable(f"расшифровка недоступна ({last})")

    def _ask(self, clip: Audio, how: str, language: str) -> tuple[int, str]:
        system = CHAT_SYSTEM + (f" The language is {language}." if language else "")
        body = {"model": self.model, "temperature": 0, "max_tokens": 4096, "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": [_parts(clip, how), {"type": "text", "text": CHAT_USER}]}]}
        r = self._post(body)
        if r.status_code != 200:
            return r.status_code, r.text[:300]
        try:
            msg = (r.json().get("choices") or [{}])[0].get("message") or {}
        except ValueError:
            return 502, r.text[:300]
        text = str(msg.get("content") or "").strip()
        if text.startswith("```"):                    # иногда модель заворачивает ответ в блок кода
            text = text.strip("`").split("\n", 1)[-1].strip() if "\n" in text else text.strip("`")
        return 200, text

    @staticmethod
    def _no_audio(text: str) -> bool:
        low = text.lower()
        return len(text.split()) < 60 and any(p in low for p in NO_AUDIO)

    def transcribe(self, clip: Audio, language: str = "") -> str:
        ways = [self.way] if self.way else list(self.WAYS)
        errors = []
        for how in ways:
            code, text = self._ask(clip, how, language)
            if code != 200:
                errors.append(f"API {code}: {text[:200]}")
                if code in (400, 415, 422):           # посредник не принял такой способ вложения — пробуем другой
                    continue
                raise SolveError(f"расшифровка: {errors[-1]}")
            if not text or self._no_audio(text):
                errors.append(f"модель не получила звук ({text[:80]!r})")
                continue
            self.way = how
            return self._check_length(clip, text, how, language)
        raise SolveError("расшифровка: " + "; ".join(errors))

    def _check_length(self, clip: Audio, text: str, how: str, language: str) -> str:
        secs = mp3_seconds(clip.data) if clip.format == "mp3" else None
        if not secs or secs < 8:
            return text
        if len(text.split()) >= secs * WORDS_PER_S_MIN:
            return text
        code, again = self._ask(clip, how, language)          # бывает случайный обрыв — ещё одна попытка
        if code == 200 and len(again.split()) >= secs * WORDS_PER_S_MIN:
            return again
        best = max(text, again if code == 200 else "", key=lambda t: len(t.split()))
        raise SolveError(f"расшифровка оборвалась: {len(best.split())} слов на {secs:.0f} с записи — модель "
                         f"{self.model} обрывает ответ на звуке, выберите другую (например, gemini-3-pro)")


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
        if self.transcriber is None and conf.mode in TRANSCRIBE_MODES and conf.base_url:
            cls = ChatTranscriber if conf.mode == "chat" else Transcriber
            self.transcriber = cls(conf.base_url, conf.model, conf.api_key)

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
        if self.conf.mode == "chat" and (not self.conf.model or self.conf.model.lower().startswith("whisper")):
            raise AudioUnsupported("для расшифровки моделью со звуком укажите её в «Модель расшифровки» (например, gemini-…)")
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
