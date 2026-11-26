"""Аудирование: разбор <audio>, расшифровка (transcribe), звук прямо в модель (native), выключено (off).
Сеть не трогаем: загрузчик записей и сервис расшифровки подменяются."""
import json
import re

import pytest

from yaklass_bot.config import Config
from yaklass_bot.parser import parse_exercise
from yaklass_bot.pipeline import solve
from yaklass_bot.solver import audio as audio_mod
from yaklass_bot.solver.api import ApiProvider
from yaklass_bot.solver.audio import Audio, AudioConf, AudioHelper, AudioUnsupported, SttNotFound
from yaklass_bot.solver.base import Provider, SolveError

MP3 = "https://cdn.example.selcdn.net/a/listen1.mp3"
# урезанная копия разметки задания «Listening» с ЯКласса: картинка, плеер, три пропуска
PAGE = """<html><body>
<div class="test-ex-nav-v2"><ul><li class="test-ex-nav-v2__item is-current" title="5. Listening. Big plans">
<span class="test-ex-nav-v2__link">5</span></li></ul></div>
<div class="exercise-content-header"><div class="pull-left">Задание</div><div class="obj-points">4,5 Б.</div></div>
<form method="post" action="/TestWorkRun/Exercise?testResultId=1&amp;exercisePosition=5&amp;twId=2" class="taskForm">
<div class="taskhtmlwrapper" id="taskhtml"><div>
<div><strong>Listen to the conversation. Daniel is talking about his future big plans.</strong></div>
<div><img class="gxs-resource-image" alt="pic.jpg" src="//cdn.example.selcdn.net/a/pic.jpg" /></div>
<div>AUDIO_TAG</div>
<div><strong>Write only one word into each gap.</strong></div>
<div><strong>1.</strong> <span class="gxs-text">The first thing that Daniel wants is to visit every </span>
<input type="text" class="gxs-answer-text-short maxpoints1,5" id="e8r1" name="e8r1|ta" />.</div>
<div><strong>2.</strong> <span class="gxs-text">The third thing is to raise his </span>
<input type="text" class="gxs-answer-text-short maxpoints1,5" id="e9r2" name="e9r2|ta" /></div>
<div><strong>3.</strong> <span class="gxs-text">It has so many </span>
<input type="text" class="gxs-answer-text-short maxpoints1,5" id="e10r3" name="e10r3|ta" /></div>
</div></div></form></body></html>"""
SOURCE_TAG = '<audio controls="" class="gxs-audio-player"><source type="audio/mpeg" src="//cdn.example.selcdn.net/a/listen1.mp3" /></audio>'


class Model(Provider):
    supports_images = False
    supports_audio = True

    def __init__(self):
        self.prompts, self.audio = [], []

    def ask(self, prompt, images=(), audio=()):
        self.prompts.append(prompt)
        self.audio.append(list(audio))
        names = re.findall(r"^- \[\[(.+?)\]\]", prompt, re.M)
        return json.dumps({"answers": {n: "country" for n in names}, "confidence": 0.9})


class OldModel(Provider):
    """Провайдер со старой сигнатурой ask(prompt, images) — не должен ломаться на заданиях без аудио."""
    def ask(self, prompt, images=()):
        names = re.findall(r"^- \[\[(.+?)\]\]", prompt, re.M)
        return json.dumps({"answers": {n: "x" for n in names}, "confidence": 0.9})


class FakeStt:
    def __init__(self, text="Daniel wants to visit every country."):
        self.text, self.calls = text, []

    def transcribe(self, clip, language=""):
        self.calls.append((clip, language))
        return self.text


def task(tag: str = SOURCE_TAG):
    return parse_exercise(PAGE.replace("AUDIO_TAG", tag))


def loader(url):
    return Audio(b"ID3fake", "mp3")


def test_parser_finds_audio_and_marks_place():
    t = task()
    assert t.audio == [MP3] and t.has_audio and t.supported
    assert "[AUDIO:1]" in t.text and t.text.index("[AUDIO:1]") < t.text.index("[[e8r1|ta]]")
    assert [f.name for f in t.fields] == ["e8r1|ta", "e9r2|ta", "e10r3|ta"]


def test_parser_audio_src_attribute_and_no_audio():
    assert task('<audio controls src="//cdn.example.selcdn.net/a/listen1.mp3"></audio>').audio == [MP3]
    plain = task("")
    assert plain.audio == [] and not plain.has_audio and "[AUDIO" not in plain.text
    broken = task('<audio controls class="gxs-audio-player"></audio>')        # плеер без ссылки
    assert broken.has_audio and broken.audio == []


def test_transcribe_puts_transcript_into_prompt():
    m, stt = Model(), FakeStt()
    helper = AudioHelper(AudioConf(mode="transcribe", base_url="https://stt.invalid/v1"), loader=loader, transcriber=stt)
    ans = solve(Config(), m, task(), log=lambda s: None, audio=helper)
    assert ans.values == {"e8r1|ta": "country", "e9r2|ta": "country", "e10r3|ta": "country"}
    assert stt.calls[0][1] == ""                                     # язык записи Whisper определяет сам
    p = m.prompts[0]
    assert "Daniel wants to visit every country." in p and "аудирование" in p
    assert m.audio == [[]]                                           # сам звук модели не отправляется


def test_native_sends_audio_clip_to_model():
    m = Model()
    helper = AudioHelper(AudioConf(mode="native"), loader=loader)
    solve(Config(), m, task(), log=lambda s: None, audio=helper)
    assert m.audio[0] and m.audio[0][0].format == "mp3"
    assert "приложены аудиозаписи (1)" in m.prompts[0]


def test_native_requires_audio_capable_provider():
    with pytest.raises(AudioUnsupported):
        solve(Config(), OldModel(), task(), log=lambda s: None, audio=AudioHelper(AudioConf(mode="native"), loader=loader))


@pytest.mark.parametrize("helper, why", [
    (AudioHelper(AudioConf(mode="off"), loader=loader), "выключено"),
    (AudioHelper(AudioConf(mode="transcribe", base_url="x"), loader=lambda u: None, transcriber=FakeStt()), "скачать"),
    (AudioHelper(AudioConf(mode="transcribe", base_url="x"), loader=loader, transcriber=FakeStt("")), "пустая"),
    (AudioHelper(AudioConf(mode="transcribe", base_url=""), loader=loader), "адрес"),
])
def test_unusable_audio_raises_audio_unsupported(helper, why):
    m = Model()
    with pytest.raises(AudioUnsupported, match=why):
        solve(Config(), m, task(), log=lambda s: None, audio=helper)
    assert not m.prompts                                             # модель зря не спрашиваем


def test_transcription_api_error_becomes_audio_unsupported():
    class Broken:
        def transcribe(self, clip, language=""):
            raise SolveError("API 404: no such endpoint")
    helper = AudioHelper(AudioConf(mode="transcribe", base_url="x"), loader=loader, transcriber=Broken())
    with pytest.raises(AudioUnsupported, match="404"):
        solve(Config(), Model(), task(), log=lambda s: None, audio=helper)


def test_task_without_audio_unaffected_by_audio_mode():
    solve(Config(), OldModel(), task(""), log=lambda s: None, audio=AudioHelper(AudioConf(mode="off")))


def test_player_without_link_is_unsupported():
    helper = AudioHelper(AudioConf(mode="native"), loader=loader)
    with pytest.raises(AudioUnsupported, match="ссылку"):
        solve(Config(), Model(), task('<audio controls class="gxs-audio-player"></audio>'), log=lambda s: None,
              audio=helper)


def test_audio_conf_falls_back_to_main_api(monkeypatch):
    monkeypatch.setenv("MAIN_KEY", "sk-main")
    c = AudioConf.from_dict({"mode": "transcribe"}, {"base_url": "https://api.openai.com/v1", "api_key_env": "MAIN_KEY"})
    assert (c.base_url, c.api_key, c.model) == ("https://api.openai.com/v1", "sk-main", "whisper-1")
    c = AudioConf.from_dict({"base_url": "https://api.groq.com/openai/v1", "api_key": "gsk", "model": "whisper-large-v3"},
                            {"base_url": "https://api.openai.com/v1", "api_key": "sk-main"})
    assert (c.base_url, c.api_key, c.model) == ("https://api.groq.com/openai/v1", "gsk", "whisper-large-v3")
    assert AudioConf.from_dict({"mode": "nonsense"}).mode == "off"


def test_config_roundtrip_keeps_audio_section(tmp_path):
    cfg = Config()
    cfg.audio.update(mode="native", model="m")
    cfg.save(tmp_path / "c.toml")
    back = Config.load(tmp_path / "c.toml")
    assert back.audio["mode"] == "native" and back.audio["model"] == "m"


def test_api_provider_encodes_input_audio(monkeypatch):
    sent = {}

    class R:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "{}"}}]}

    def post(url, headers=None, timeout=None, json=None):
        sent.update(json)
        return R()
    monkeypatch.setattr("yaklass_bot.solver.api.requests.post", post)
    ApiProvider({"base_url": "http://local/v1", "model": "gemini"}).ask("p", (), [Audio(b"abc", "mp3")])
    part = sent["messages"][0]["content"][1]
    assert part == {"type": "input_audio", "input_audio": {"data": "YWJj", "format": "mp3"}}


def test_transcriber_request(monkeypatch):
    got = {}

    class R:
        status_code = 200
        def json(self):
            return {"text": " hello "}

    def post(url, headers=None, data=None, timeout=None, files=None):
        got.update(url=url, headers=headers, data=data, files=files)
        return R()
    monkeypatch.setattr("yaklass_bot.solver.audio.requests.post", post)
    text = audio_mod.Transcriber("https://api.groq.com/openai/v1/", "whisper-large-v3", "gsk").transcribe(
        Audio(b"x", "mp3"), "en")
    assert text == "hello" and got["url"] == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert got["data"]["model"] == "whisper-large-v3" and got["data"]["language"] == "en"
    assert got["headers"] == {"Authorization": "Bearer gsk"} and got["files"]["file"][0] == "audio.mp3"


def test_download_rejects_non_audio_and_oversize(monkeypatch):
    class R:
        def __init__(self, mime, body):
            self.headers, self.body = {"content-type": mime}, body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def raise_for_status(self):
            pass
        def iter_content(self, n):
            yield self.body
    monkeypatch.setattr(audio_mod.requests, "get", lambda *a, **k: R("text/html", b"<html>"))
    assert audio_mod.download("https://x/a.mp3") is None
    monkeypatch.setattr(audio_mod.requests, "get", lambda *a, **k: R("audio/mpeg", b"x" * (audio_mod.MAX_BYTES + 1)))
    assert audio_mod.download("https://x/a.mp3") is None
    monkeypatch.setattr(audio_mod.requests, "get", lambda *a, **k: R("audio/mpeg", b"ID3"))
    assert audio_mod.download("https://x/a.mp3") == Audio(b"ID3", "mp3")


@pytest.mark.parametrize("stt_url, stt_key, want", [
    ("", "", "sk-main"),                                       # расшифровка там же, где модель: ключ модели
    ("", "gsk-old", "sk-main"),                                # старый ключ STT на адрес модели не уходит
    ("https://api.groq.com/openai/v1", "gsk", "gsk"),
    ("https://api.groq.com/openai/v1", "", ""),                # ключ модели на чужой адрес не уходит
])
def test_app_never_sends_key_to_foreign_host(stt_url, stt_key, want):
    from yaklass_app.settings import AppSettings
    from yaklass_app.solvers import LocalSolver
    s = AppSettings(solver_mode="own")
    s.own_api = {**s.own_api, "stt_base_url": stt_url}
    assert LocalSolver(s, "sk-main", stt_key).audio.conf.api_key == want


def test_download_accepts_s3_octet_stream(monkeypatch):
    class R:
        headers = {"content-type": "binary/octet-stream"}
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def raise_for_status(self):
            pass
        def iter_content(self, n):
            yield b"ID3"
    monkeypatch.setattr(audio_mod.requests, "get", lambda *a, **k: R())
    assert audio_mod.download("https://x/a.mp3") == Audio(b"ID3", "mp3")


class Stt404:
    def __init__(self):
        self.calls = 0

    def transcribe(self, clip, language=""):
        self.calls += 1
        raise SttNotFound('расшифровка: API 404: {"error":"not_found"}')


def test_stt_404_falls_back_to_native_and_remembers():
    """У провайдера нет /audio/transcriptions: записи уходят модели, STT больше не дёргаем, запись не качаем дважды."""
    m, stt, loads = Model(), Stt404(), []
    helper = AudioHelper(AudioConf(mode="transcribe", base_url="https://api.site.invalid/v1"),
                         loader=lambda u: loads.append(u) or loader(u), transcriber=stt)
    logs = []
    ans = solve(Config(), m, task(), log=logs.append, audio=helper)
    assert ans.values["e8r1|ta"] == "country"
    assert m.audio[0] and m.audio[0][0].format == "mp3" and "приложены аудиозаписи (1)" in m.prompts[0]
    assert loads == [MP3] and helper.stt_missing and any("отправлены модели" in s for s in logs)
    solve(Config(), m, task(), log=lambda s: None, audio=helper)
    assert stt.calls == 1 and m.audio[1]                             # второе задание — сразу native


def test_stt_404_without_audio_capable_model_is_unsupported():
    helper = AudioHelper(AudioConf(mode="transcribe", base_url="x"), loader=loader, transcriber=Stt404())
    with pytest.raises(AudioUnsupported, match="404"):
        solve(Config(), OldModel(), task(), log=lambda s: None, audio=helper)


@pytest.mark.parametrize("code, cls", [(404, SttNotFound), (405, SttNotFound), (500, SolveError), (401, SolveError)])
def test_transcriber_marks_missing_endpoint(monkeypatch, code, cls):
    class R:
        status_code, text = code, "{}"
    monkeypatch.setattr("yaklass_bot.solver.audio.requests.post", lambda *a, **k: R())
    with pytest.raises(cls) as e:
        audio_mod.Transcriber("https://x.invalid/v1", "whisper-1").transcribe(Audio(b"x", "mp3"))
    assert isinstance(e.value, SttNotFound) == (cls is SttNotFound)
