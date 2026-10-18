"""Код подключения (адрес сервера внутри) и User-Agent по выбранному браузеру."""
import pytest

from yaklass_bot.connect import (ConnectionCodeError, hide_host, make_connection_code, normalize_server_url,
                                 parse_connection_code)
from yaklass_bot.useragent import build_user_agent, supports


def test_roundtrip_and_formats():
    s = make_connection_code("https://Solver.Example.com/some/path", "abcd-efgh")
    assert s.startswith("yk1.") and "solver.example.com" not in s            # адрес не виден в строке
    assert parse_connection_code(s) == ("https://solver.example.com", "ABCD-EFGH")
    assert parse_connection_code(f"  `{s}`\n") == ("https://solver.example.com", "ABCD-EFGH")   # пробелы, кавычки
    assert parse_connection_code(s[:6] + "\n" + s[6:]) == ("https://solver.example.com", "ABCD-EFGH")
    assert parse_connection_code("abcdefgh") == (None, "ABCD-EFGH")           # старый формат: только код
    assert parse_connection_code("ABCD-EFGH") == (None, "ABCD-EFGH")


def test_bad_codes_are_rejected():
    for bad in ["", "yk1.", "yk1.!!!.ABCD-EFGH", "yk1.aHR0cHM6Ly94LmNvbQ.12", "ABCD", "https://x.com",
                make_connection_code("https://a.example.com", "ABCD-EFGH")[:-3]]:
        with pytest.raises(ConnectionCodeError):
            parse_connection_code(bad)
    import base64
    plain_http = "yk1." + base64.urlsafe_b64encode(b"http://evil.example.com").decode().rstrip("=") + ".ABCD-EFGH"
    with pytest.raises(ConnectionCodeError, match="https"):                   # без шифрования — только локально
        parse_connection_code(plain_http)
    assert parse_connection_code(make_connection_code("http://127.0.0.1:8080", "ABCD-EFGH"))[0] == "http://127.0.0.1:8080"
    assert normalize_server_url("https://a.example.com:8443/x?y") == "https://a.example.com:8443"


def test_hide_host_in_error_text():
    t = "HTTPSConnectionPool(host='solver.example.com', port=443): Max retries https://solver.example.com/v1/link"
    out = hide_host(t, "https://solver.example.com")
    assert "solver.example.com" not in out and "сервер" in out


def test_user_agent_per_browser():
    ver = "141.0.7390.37"
    chrome = build_user_agent("chrome", ver, "win32")
    assert chrome == ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/141.0.0.0 Safari/537.36")
    for same in ("vivaldi", "brave", "chromium"):                              # без собственных меток в UA
        assert build_user_agent(same, ver, "win32") == chrome
    assert build_user_agent("edge", ver, "win32").endswith(" Edg/141.0.0.0")
    assert build_user_agent("opera", ver, "win32").endswith(" OPR/126.0.0.0")
    assert "X11; Linux x86_64" in build_user_agent("chrome", ver, "linux")
    assert "Macintosh" in build_user_agent("chrome", ver, "darwin")
    assert build_user_agent("firefox", ver) == "" and build_user_agent("librewolf", ver) == ""
    assert supports("vivaldi") and not supports("firefox")
    assert "Chrome/130.0.0.0" in build_user_agent("chrome", "")                # версия неизвестна — запасная


def test_cookie_import_uses_user_agent_of_selected_browser(tmp_path, monkeypatch):
    """Контекст браузера для импортированных кук создаётся с UA выбранного браузера."""
    from yaklass_app import backend as be
    from yaklass_app.settings import AppSettings, Store
    store = Store(tmp_path, use_keyring=False)
    store.save(AppSettings(browser_mode="cookies", cookie_browser="vivaldi"))
    seen = {}

    class Ctx:
        def add_cookies(self, c): seen["cookies"] = c

    class Browser:
        version = "141.0.7390.37"
        def new_context(self, **kw):
            seen["kw"] = kw
            return Ctx()
        def close(self): pass

    class PW:
        class chromium:
            @staticmethod
            def launch(**kw): return Browser()

    monkeypatch.setattr(be, "load_cookies", lambda *a, **k: [{"name": "a"}])
    monkeypatch.setattr(be, "to_playwright_cookies", lambda c: [{"name": "a", "value": "1", "url": "https://x"}])
    b = be.PlaywrightBackend.__new__(be.PlaywrightBackend)
    b.store, b.dir, b._good = store, tmp_path, None
    monkeypatch.setattr(b, "_playwright", lambda: PW)
    ctx, closer = b._open(headless=True)
    assert seen["kw"]["user_agent"].startswith("Mozilla/5.0") and "Chrome/141.0.0.0" in seen["kw"]["user_agent"]
    store.save(AppSettings(browser_mode="cookies", cookie_browser="firefox"))
    b._open(headless=True)
    assert "user_agent" not in seen["kw"]                                     # Firefox не имитируем
