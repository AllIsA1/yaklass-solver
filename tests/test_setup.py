import os

import pytest

from yaklass_bot import setup_wizard as w
from yaklass_bot.config import Config
from yaklass_bot.solver.api import ApiProvider
from yaklass_bot.solver.base import SolveError


class ScriptedIO:
    """Ответы по порядку вопросов: числа — для choose, True/False — для yesno, строки — для ask/secret."""
    def __init__(self, *answers):
        self.a = list(answers)
        self.out = []

    def say(self, t=""): self.out.append(t)
    def ask(self, p, d=""): v = self.a.pop(0); return d if v is None else v
    def secret(self, p): return self.a.pop(0)
    def yesno(self, p, d=True): v = self.a.pop(0); return d if v is None else v
    def choose(self, p, o, d=0): v = self.a.pop(0); return d if v is None else v


class Checks:
    def __init__(self, cookies=3, api="ok", chromium=True):
        self._c, self._a, self._ch = cookies, api, chromium
        self.installed = False
    def cookies(self, cfg): return self._c
    def api(self, cfg): return self._a
    def searx(self, url): return 3
    def chromium(self): return self._ch
    def install_chromium(self): self.installed = True; return True


def test_config_save_load_roundtrip(tmp_path):
    c = Config()
    c.browser, c.searxng_url, c.search_mode = "firefox", "http://x:8080", "model"
    c.api.update(model='my "model"', vision=False, json_mode=True, api_key_file=str(tmp_path / "k"))
    c.run.update(channel="chrome", user_agent="UA/1.0")
    p = tmp_path / "c.toml"
    c.save(p)
    d = Config.load(p)
    assert (d.browser, d.searxng_url, d.search_mode) == ("firefox", "http://x:8080", "model")
    assert d.api["model"] == 'my "model"' and d.api["vision"] is False and d.api["json_mode"] is True
    assert d.run["channel"] == "chrome" and d.run["user_agent"] == "UA/1.0"


def test_old_chatgpt_browser_config_warns_and_falls_back_to_api(tmp_path, capsys):
    p = tmp_path / "c.toml"
    p.write_text('[llm]\nprovider = "chatgpt_browser"\n', encoding="utf-8")
    Config.load(p)
    assert "удалён" in capsys.readouterr().err


def test_wizard_openai_env_key(tmp_path, monkeypatch):
    monkeypatch.delenv("MY_KEY", raising=False)
    # браузер=firefox(2), проверка кук=да | OpenAI(0), url/модель по умолчанию, vision=да, хранение=env(0),
    # имя=MY_KEY, тест=да | поиск=нет | браузер run = встроенный(0)
    io = ScriptedIO(2, True, 0, None, None, True, 0, "MY_KEY", True, False, 0)
    p = tmp_path / "config.toml"
    w.run_wizard(Config(), p, io, Checks())
    c = Config.load(p)
    assert c.browser == "firefox" and c.api["api_key_env"] == "MY_KEY"
    assert c.api["model"] == "gpt-4o-mini" and c.api["vision"] is True and c.searxng_url == ""
    assert any("MY_KEY" in line for line in io.out)            # подсказка, как задать переменную
    assert not io.a


def test_wizard_deepseek_preset(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    # chrome(0), без кук | DeepSeek(1), дефолты, vision (дефолт у DeepSeek — нет), env(0), имя по умолчанию, без теста | без поиска | Chromium
    io = ScriptedIO(0, False, 1, None, None, None, 0, None, False, False, 0)
    p = tmp_path / "config.toml"
    w.run_wizard(Config(), p, io, Checks())
    a = Config.load(p).api
    assert a["base_url"] == "https://api.deepseek.com/v1" and a["model"] == "deepseek-chat"
    assert a["api_key_env"] == "DEEPSEEK_API_KEY" and a["vision"] is False and a["json_mode"] is True


def test_wizard_key_file_is_private_and_used(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # chrome(0), без кук | OpenAI(0), дефолты, vision, ключ в файл(1), secret | без теста | без поиска | Chromium(0)
    io = ScriptedIO(0, False, 0, None, None, True, 1, "sk-secret", False, False, 0)
    p = tmp_path / "config.toml"
    w.run_wizard(Config(), p, io, Checks())
    key = tmp_path / "api_key"
    assert key.read_text().strip() == "sk-secret"
    if os.name != "nt":
        assert oct(key.stat().st_mode & 0o777) == "0o600"
    assert "sk-secret" not in p.read_text() and "sk-secret" not in " ".join(io.out)   # ключ нигде не светится
    assert ApiProvider(Config.load(p).api).key == "sk-secret"


def test_wizard_ollama_needs_no_key(tmp_path):
    # firefox, без кук | Ollama(3), url, модель, vision=нет, «нужен ключ?»=нет, без теста | поиск=да, адрес | Chromium
    io = ScriptedIO(2, False, 3, "http://192.168.1.10:11434/v1", "llama3.2-vision", False, False, False,
                    True, "http://192.168.1.5:8080/", 0)
    p = tmp_path / "config.toml"
    w.run_wizard(Config(), p, io, Checks())
    c = Config.load(p)
    assert c.api["base_url"].startswith("http://192.168.1.10") and c.api["vision"] is False
    assert c.api["api_key_env"] == "" and c.searxng_url == "http://192.168.1.5:8080"
    ApiProvider(c.api)    # локальному серверу ключ не нужен — не падает


def test_wizard_offers_chromium_download(tmp_path):
    io = ScriptedIO(0, False, 3, None, None, False, False, False, False, 0, True)
    p = tmp_path / "config.toml"
    ch = Checks(chromium=False)
    w.run_wizard(Config(), p, io, ch)
    assert ch.installed and not io.a


def test_wizard_survives_failed_checks(tmp_path):
    class Bad(Checks):
        def cookies(self, cfg): raise RuntimeError("БД кук заблокирована")
        def api(self, cfg): raise SolveError("API 401")
    io = ScriptedIO(0, True, 0, None, None, True, 0, "OPENAI_API_KEY", True, False, 0)
    p = tmp_path / "config.toml"
    w.run_wizard(Config(), p, io, Bad())
    assert p.exists() and any("заблокирована" in x for x in io.out) and any("401" in x for x in io.out)


def test_missing_key_message_points_to_setup(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SolveError, match="setup"):
        ApiProvider(Config().api)
