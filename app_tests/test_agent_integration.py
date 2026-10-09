import os
import stat
from pathlib import Path

import pytest
import requests

from yaklass_app import solvers
from yaklass_app.settings import AppSettings, Store
from yaklass_bot.models import Field, Option, Task
from yaklass_bot.solver import FatalSolveError, SolveError

from .conftest import link_and_start, wait_for


def sample_task():
    return Task(position=2, title="t", points=1, text="Столица — [[e1|dd]]", images=[],
                fields=[Field("e1|dd", "dropdown", options=[Option("m", "Москва"), Option("k", "Киев")])])


# ---------- привязка и решение ----------

def test_link_then_remote_solve_through_real_server(server):
    token, username = solvers.link(server.url, server.new_code(7, "bob"), "pc")
    assert token.startswith("yks_") and username == "bob"
    ans = solvers.RemoteSolver(server.url, token).solve(sample_task())
    assert ans.values == {"e1|dd": "m"} and ans.confidence == 0.7
    assert "Москва" in server.model.prompts[0]


def test_link_errors_are_readable(server):
    with pytest.raises(solvers.LinkError, match="неверный|истёк"):
        solvers.link(server.url, "AAAA-BBBB", "pc")
    with pytest.raises(solvers.LinkError, match="недоступен"):
        solvers.link("http://127.0.0.1:1", "AAAA-BBBB", "pc")


def test_ws_and_url_helpers():
    assert solvers.ws_url("https://solver.example.com/") == "wss://solver.example.com/v1/agent"
    assert solvers.ws_url("http://127.0.0.1:8080") == "ws://127.0.0.1:8080/v1/agent"


def test_remote_solver_error_mapping(monkeypatch):
    class R:
        def __init__(self, code, detail="", headers=None):
            self.status_code, self._d, self.headers = code, detail, headers or {}
        def json(self): return {"detail": self._d}

    def solver_with(*responses):
        it = iter(responses)
        def post(*a, **k):
            r = next(it)
            if isinstance(r, Exception):
                raise r
            return r
        monkeypatch.setattr(requests, "post", post)
        return solvers.RemoteSolver("http://x", "yks_t", sleep=lambda s: None)

    with pytest.raises(FatalSolveError, match="отвязано"):
        solver_with(R(401)).solve(sample_task())
    with pytest.raises(FatalSolveError, match="лимит"):
        solver_with(R(429, "дневной лимит 300 запросов исчерпан")).solve(sample_task())
    with pytest.raises(SolveError) as e:                                  # 502 — не фатально: следующее задание можно пробовать
        solver_with(R(502, "модель не дала ответа")).solve(sample_task())
    assert not isinstance(e.value, FatalSolveError)
    with pytest.raises(FatalSolveError, match="недоступен"):              # сеть: 3 попытки и стоп
        solver_with(*[requests.ConnectionError()] * 3).solve(sample_task())
    ok = R(200)
    ok.json = lambda: {"answers": {"e1|dd": "m"}, "confidence": 0.9, "evidence": "e", "remaining_today": 5}
    s = solver_with(R(429, "слишком часто", {"Retry-After": "1"}), ok)    # rate limit: подождали и повторили
    assert s.solve(sample_task()).values == {"e1|dd": "m"} and s.remaining == 5


# ---------- агент ↔ сервер ----------

def test_agent_connects_and_server_sees_hello(setup):
    link_and_start(setup)
    assert wait_for(lambda: setup.server.hub.online(1))
    assert wait_for(lambda: setup.server.events_of("hello"))
    assert setup.server.events_of("hello")[0]["device"] == "test-pc"


def test_telegram_list_works_command(setup):
    link_and_start(setup)
    setup.server.command(1, {"cmd": "list_works"})
    assert wait_for(lambda: any(not e.get("new") for e in setup.server.events_of("works")))
    ev = [e for e in setup.server.events_of("works") if not e.get("new")][0]
    assert [w["id"] for w in ev["works"]] == ["111", "222"] and ev["works"][0]["subject"] == "Алгебра"


def test_new_works_are_reported_once(setup):
    link_and_start(setup)
    setup.agent.refresh_works(notify_new=True)                                            # «опрос по таймеру» (90+ с) вызываем вручную
    assert wait_for(lambda: any(e.get("new") for e in setup.server.events_of("works")), 10)
    n = len([e for e in setup.server.events_of("works") if e.get("new")])
    setup.agent.refresh_works(notify_new=True)                                            # те же работы — не «новые»
    import time
    time.sleep(0.5)
    assert len([e for e in setup.server.events_of("works") if e.get("new")]) == n


def test_remote_start_runs_work_with_server_solver_and_reports(setup):
    link_and_start(setup)
    setup.server.command(1, {"cmd": "start_work", "work_id": "111", "mode": "auto"})
    assert wait_for(lambda: setup.server.events_of("finished"), 10)
    fin = setup.server.events_of("finished")[0]
    assert fin["completed"] is True and fin["work_id"] == "111" and fin["summary"] == {"новых ответов": 3}
    prog = setup.server.events_of("progress")
    assert [(p["done"], p["total"]) for p in prog][-1] == (3, 3)
    assert len(setup.server.model.prompts) == 3                       # решение шло через сервер
    assert setup.backend.runs[0][:2] == ("111", "auto") and setup.agent.state == "idle"
    assert [a for a in setup.server.events_of("ack") if a["ok"]]


def test_dry_mode_not_completed(setup):
    link_and_start(setup)
    ok, _ = setup.agent.start_work("111", "dry")
    assert ok and wait_for(lambda: setup.server.events_of("finished"), 10)
    assert setup.server.events_of("finished")[0]["completed"] is False


def test_remote_start_can_be_disabled(setup):
    s = setup.store.load()
    s.remote_start_allowed = False
    setup.store.save(s)
    link_and_start(setup)
    setup.server.command(1, {"cmd": "start_work", "work_id": "111", "mode": "auto"})
    assert wait_for(lambda: [a for a in setup.server.events_of("ack") if not a["ok"]])
    assert "отключён" in setup.server.events_of("ack")[0]["msg"] and not setup.backend.runs
    assert setup.agent.start_work("111", "auto")[0]                    # из самого приложения — можно


def test_cannot_start_two_works_and_validates_input(setup):
    link_and_start(setup)
    setup.backend.tasks = 8
    assert setup.agent.start_work("111", "auto")[0]
    ok, why = setup.agent.start_work("222", "auto")
    assert not ok and "Уже выполняется" in why
    assert not setup.agent.start_work("12; rm", "auto")[0] and not setup.agent.start_work("1", "weird")[0]
    setup.agent.stop_run()
    assert wait_for(lambda: setup.agent.state == "idle", 10)


def test_pause_resume_stop_from_telegram(setup):
    link_and_start(setup)
    setup.backend.tasks = 40
    setup.agent.start_work("111", "auto")
    assert wait_for(lambda: setup.server.events_of("progress"))
    setup.server.command(1, {"cmd": "pause"})
    assert wait_for(lambda: setup.agent.state == "paused")
    done = len(setup.server.events_of("progress"))
    import time
    time.sleep(0.6)
    assert len(setup.server.events_of("progress")) <= done + 1         # на паузе прогресс не идёт
    setup.server.command(1, {"cmd": "resume"})
    assert wait_for(lambda: setup.agent.state == "running")
    setup.server.command(1, {"cmd": "stop"})
    assert wait_for(lambda: setup.server.events_of("finished"), 10)
    fin = setup.server.events_of("finished")[0]
    assert fin["completed"] is False and "остановлено" in fin["note"]


def test_quota_exhaustion_stops_the_run(tmp_path):
    from .conftest import FakeBackend, RealServer
    from yaklass_app.agent import Agent
    srv = RealServer(quota=2)
    try:
        store = Store(tmp_path / "c", use_keyring=False)
        store.save(AppSettings(server_url=srv.url))
        backend = FakeBackend()
        agent = Agent(store, backend, directory=tmp_path / "d")
        agent.link(srv.new_code())
        agent.start()
        assert wait_for(lambda: agent.connected)
        agent.start_work("111", "auto")
        assert wait_for(lambda: srv.events_of("finished"), 10)
        fin = srv.events_of("finished")[0]
        assert fin["completed"] is False and "лимит" in fin["note"]
        assert len(srv.model.prompts) == 2                             # третий запрос не прошёл
        agent.shutdown()
    finally:
        srv.stop()


def test_unlink_from_telegram_side_clears_token(setup):
    link_and_start(setup)
    assert setup.store.token
    setup.server.db.revoke_user_agents(1)
    import asyncio
    asyncio.run_coroutine_threadsafe(setup.server.hub.kick(1), setup.server.loop).result(5)
    assert wait_for(lambda: any(n == "unlinked" for n, _ in setup.events), 10)
    assert setup.store.token == "" and not setup.agent.connected


def test_unlink_from_app(setup):
    link_and_start(setup)
    setup.agent.unlink()
    assert setup.store.token == "" and wait_for(lambda: not setup.server.hub.online(1))


def test_not_logged_in_is_reported(setup):
    setup.backend.logged_in = False
    link_and_start(setup)
    setup.agent.refresh_works()
    assert any(n == "login_required" for n, _ in setup.events)
    assert wait_for(lambda: [e for e in setup.server.events_of("error") if "вход" in e["message"]])


def test_agent_reconnects_after_server_restart(tmp_path):
    from .conftest import FakeBackend, RealServer
    from yaklass_app.agent import Agent
    srv = RealServer()
    store = Store(tmp_path / "c", use_keyring=False)
    store.save(AppSettings(server_url=srv.url))
    agent = Agent(store, FakeBackend(), directory=tmp_path / "d")
    agent.link(srv.new_code())
    agent.start()
    try:
        assert wait_for(lambda: agent.connected)
        srv.stop()
        assert wait_for(lambda: not agent.connected, 10)                # сервер упал — агент это видит
    finally:
        agent.shutdown()


# ---------- настройки ----------

def test_settings_roundtrip_and_validation(tmp_path):
    st = Store(tmp_path, use_keyring=False)
    s = AppSettings(server_url="https://solver.example.com", solver_mode="own")
    s.own_api.update(model="deepseek-chat")
    s.pace = {"between_tasks": [5, 9], "typing_ms": [10, 20]}
    st.save(s)
    s2 = st.load()
    assert s2.own_api["model"] == "deepseek-chat" and s2.pace_obj().between_tasks == (5.0, 9.0)
    assert s2.pace_obj().before_submit == (0.5, 1.5)                   # недостающее — значения по умолчанию
    assert AppSettings(server_url="ftp://x").validate() and not s2.validate()
    assert AppSettings(poll_minutes=0).validate() and AppSettings(poll_minutes=4).validate() and not AppSettings(poll_minutes=5).validate()
    (tmp_path / "settings.json").write_text('{"server_url": "https://a", "unknown_key": 1, "pace": 5}')
    assert st.load().server_url == "https://a"                          # лишние ключи игнорируются
    assert st.load().pace_obj().between_tasks == (1.0, 3.0)            # битое значение -> по умолчанию
    (tmp_path / "settings.json").write_text('{"poll_minutes": "часто", "remote_start_allowed": 1, "theme": 5}')
    d = st.load()
    assert d.poll_minutes == 60 and d.remote_start_allowed is True and d.theme == "dark"
    (tmp_path / "settings.json").write_text("[1, 2]")
    assert st.load().poll_minutes == 60


def test_secrets_are_not_in_settings_file_and_private(tmp_path):
    st = Store(tmp_path, use_keyring=False)
    st.token, st.own_api_key = "yks_secret", "sk-secret"
    st.save(AppSettings())
    assert "yks_secret" not in (tmp_path / "settings.json").read_text()
    assert st.token == "yks_secret" and st.own_api_key == "sk-secret"
    import os
    if os.name != "nt":
        assert stat.S_IMODE((tmp_path / "secrets.json").stat().st_mode) == 0o600
    st.token = ""
    assert st.token == ""


def test_own_api_mode_uses_local_solver_and_never_touches_server(setup, monkeypatch):
    import yaklass_app.solvers as sv
    s = setup.store.load()
    s.solver_mode = "own"
    setup.store.save(s)
    setup.store.own_api_key = "k"
    calls = []

    class Fake:
        def __init__(self, *a): pass
        def solve(self, task, log=None):
            from yaklass_bot.solver.base import Answer
            calls.append(task.position)
            return Answer({"f1|dd": "a"}, 0.5)
    monkeypatch.setattr(sv, "LocalSolver", Fake)
    link_and_start(setup)
    setup.agent.start_work("111", "auto")
    assert wait_for(lambda: setup.server.events_of("finished"), 10)
    assert len(calls) == 3 and setup.server.model.prompts == []         # сервер модель не вызывал


# ---------- журнал не засоряется повторами ----------

def test_unlinked_agent_does_not_spam_connection_events(tmp_path):
    from yaklass_app.agent import Agent
    from .conftest import FakeBackend
    store = Store(tmp_path / "c", use_keyring=False)
    store.save(AppSettings(server_url="https://solver.example.com"))        # токена нет
    events = []
    agent = Agent(store, FakeBackend(), emit=lambda n, p: events.append((n, p)), directory=tmp_path / "d")
    agent.start()
    try:
        import time
        time.sleep(7.5)                                                       # раньше: событие каждые 3 с (≥ 3 штуки)
        conn = [p for n, p in events if n == "connection"]
        assert len(conn) == 1 and "Не привязано" in conn[0]["message"]
    finally:
        agent.shutdown()


def test_set_connected_deduplicates(setup):
    a = setup.agent
    for _ in range(5):
        a._set_connected(False, "Нет связи с сервером")
    a._set_connected(True, "Подключено к серверу")
    a._set_connected(True, "Подключено к серверу")
    a._set_connected(False, "Нет связи с сервером")
    msgs = [p["message"] for n, p in setup.events if n == "connection"]
    assert msgs == ["Нет связи с сервером", "Подключено к серверу", "Нет связи с сервером"]


def test_repeated_identical_ws_error_logged_once(tmp_path):
    """Сервер недоступен: агент пробует снова и снова, но об одной и той же ошибке пишет один раз."""
    from yaklass_app.agent import Agent
    from .conftest import FakeBackend
    store = Store(tmp_path / "c", use_keyring=False)
    store.save(AppSettings(server_url="http://127.0.0.1:1"))
    store.token = "yks_x"
    events = []
    agent = Agent(store, FakeBackend(), emit=lambda n, p: events.append((n, p)), directory=tmp_path / "d")
    agent.start()
    try:
        assert wait_for(lambda: any("Связь с сервером" in p.get("text", "") for n, p in events if n == "log"), 8)
        import time
        time.sleep(7)                                                         # за это время было несколько попыток (2 с, 4 с…)
        errs = [p["text"] for n, p in events if n == "log" and "Связь с сервером" in p["text"]]
        assert len(errs) == 1
    finally:
        agent.shutdown()


def test_friendly_ws_errors():
    from yaklass_app.agent import friendly_ws_error
    raw = ("Handshake status 404 Not Found -+-+- {'server': 'nginx/1.28.3 (Ubuntu)'} -+-+- "
           '{"detail":"Not Found"}')
    assert "Upgrade" in friendly_ws_error(raw) and "nginx" in friendly_ws_error(raw) and "-+-+-" not in friendly_ws_error(raw)
    assert "502" in friendly_ws_error("Handshake status 502 Bad Gateway -+-+- {}")
    assert "домен" in friendly_ws_error("[Errno -2] Name or service not known")
    assert "отклонил" in friendly_ws_error("[Errno 111] Connection refused")
    assert friendly_ws_error("что-то иное") == "что-то иное"


# ---------- проверка входа в ЯКласс (режим «куки браузера») ----------

def _cookies_backend(tmp_path, monkeypatch, cookies=("c",), cookie_error=None):
    import yaklass_app.backend as be
    store = Store(tmp_path / "c", use_keyring=False)
    store.save(AppSettings(browser_mode="cookies", cookie_browser="firefox"))
    backend = be.PlaywrightBackend(store, tmp_path / "d")

    def fake_load(browser, domain, *a, **k):
        if cookie_error:
            raise cookie_error
        return list(cookies)
    monkeypatch.setattr(be, "load_cookies", fake_load)
    monkeypatch.setattr(be, "to_playwright_cookies", lambda jar: [{"name": c, "value": "v", "domain": ".yaklass.ru", "path": "/"} for c in jar])
    return be, backend


def test_cookies_mode_checks_cookies_before_starting_any_browser(tmp_path, monkeypatch):
    be, b = _cookies_backend(tmp_path, monkeypatch, cookies=())
    with pytest.raises(be.NotLoggedIn, match="firefox"):
        b._call(lambda: b._open(headless=True))
    assert b._pw is None
    be, b = _cookies_backend(tmp_path, monkeypatch, cookie_error=OSError("database is locked"))
    with pytest.raises(be.BackendError, match="database is locked"):
        b._call(lambda: b._open(headless=True))
    assert b._pw is None                                                  # ни Playwright, ни браузер не запускались


def test_cookies_are_passed_to_the_browser_context(tmp_path, monkeypatch):
    be, b = _cookies_backend(tmp_path, monkeypatch, cookies=("a", "b"))
    added = []

    class Ctx:
        def add_cookies(self, cs): added.extend(cs)

    class Br:
        def new_context(self): return Ctx()
        def close(self): pass
    monkeypatch.setattr(b, "_playwright", lambda: object())
    monkeypatch.setattr(b, "_launch_any", lambda s, make: Br())
    b._call(lambda: b._open(headless=True))
    assert [c["name"] for c in added] == ["a", "b"]


def test_friendly_browser_error_message():
    from yaklass_app.backend import friendly_error
    t = friendly_error(Exception("BrowserType.launch: Executable doesn't exist at /root/.cache/ms-playwright/chromium-1/chrome"))
    assert "Chrome" in t and "Chromium" in t and "ms-playwright" not in t
    assert friendly_error(Exception("что-то\nвторая строка")) == "что-то"


def test_check_login_gives_feedback_for_every_outcome(setup):
    a = setup.agent

    def last(name):
        return [p for n, p in setup.events if n == name]
    a.refresh_works()
    assert last("login_status")[-1]["ok"] is True and last("works")
    setup.backend.logged_in = False
    a.refresh_works()
    a.refresh_works()                                                    # повторная проверка снова даёт ответ (раньше — тишина)
    assert len(last("login_required")) == 2
    from yaklass_app.backend import BackendError
    setup.backend.list_error = BackendError("Не удалось прочитать куки из firefox: locked")
    a.refresh_works()
    st = last("login_status")[-1]
    assert st["ok"] is None and "firefox" in st["message"]
    setup.backend.list_error = Exception("BrowserType.launch: Executable doesn't exist at /x")
    a.refresh_works()
    assert "Chrome" in last("login_status")[-1]["message"]


def test_login_button_behaviour_depends_on_mode(setup):
    a = setup.agent
    s = setup.store.load()
    s.browser_mode = "cookies"
    setup.store.save(s)
    a.login_yaklass()
    assert wait_for(lambda: any(n == "login_status" and p.get("ok") is True for n, p in setup.events))
    assert setup.backend.login_calls == 0                                # окно входа не открывается
    s.browser_mode = "profile"
    setup.store.save(s)
    a.login_yaklass()
    assert wait_for(lambda: setup.backend.login_calls == 1)
    setup.backend.login_error = RuntimeError("Executable doesn't exist at /x")
    a.login_yaklass()
    assert wait_for(lambda: any(n == "login_status" and "окно входа" in p.get("message", "") for n, p in setup.events))


# ---------- подбор браузера ----------

def _backend(tmp_path, **settings):
    import yaklass_app.backend as be
    store = Store(tmp_path / "c", use_keyring=False)
    store.save(AppSettings(**settings))
    return be, be.PlaywrightBackend(store, tmp_path / "d"), store.load()


def test_frozen_playwright_browsers_path_points_to_cache(monkeypatch):
    """Корень ошибки «не найден браузер»: в собранном приложении Playwright ищет браузеры внутри приложения (=0)."""
    import yaklass_app.backend as be
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    be.ensure_browsers_path()
    import os
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(be.playwright_cache_dir()) != "0"
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/custom/pw")        # явное значение пользователя не трогаем
    be.ensure_browsers_path()
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "/custom/pw"


def test_find_browsers_prefers_playwright_cache_newest_first(tmp_path, monkeypatch):
    import sys
    import yaklass_app.backend as be
    if sys.platform in ("win32", "darwin"):
        pytest.skip("тест раскладки Linux")
    cache = tmp_path / "pw"
    for rev in (1100, 1243):
        f = cache / f"chromium-{rev}" / "chrome-linux64" / "chrome"
        f.parent.mkdir(parents=True)
        f.write_text("#!/bin/sh\n"); f.chmod(0o755)
    (cache / "chromium-999" / "chrome-linux").mkdir(parents=True)        # без исполняемого файла — не считается
    sysb = tmp_path / "bin" / "chromium"
    sysb.parent.mkdir()
    sysb.write_text("#!/bin/sh\n"); sysb.chmod(0o755)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(cache))
    monkeypatch.setenv("PATH", str(sysb.parent))
    labels = [l for l, _ in be.find_system_browsers()]
    assert labels[:2] == ["Chromium Playwright (chromium-1243)", "Chromium Playwright (chromium-1100)"]
    assert labels[2] == "chromium" and len(labels) == 3
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")                  # «0» (режим PyInstaller) -> обычный кэш, а не папка «0»
    assert be.playwright_cache_dir() != Path("0")


def test_attempts_order_and_last_good_first(tmp_path, monkeypatch):
    be, backend, s = _backend(tmp_path, browser_path="/opt/my/chrome", channel="msedge")
    monkeypatch.setattr(be, "find_system_browsers", lambda: [("chromium", "/usr/bin/chromium"), ("dup", "/opt/my/chrome")])
    labels = [l for l, _ in backend.attempts(s)]
    assert labels == ["указанный путь /opt/my/chrome", "канал msedge", "встроенный Chromium Playwright", "chromium"]
    backend._good = backend.attempts(s)[3]
    assert backend.attempts(s)[0][0] == "chromium"                       # удавшийся вариант — первым


def test_launch_falls_back_until_something_works(tmp_path, monkeypatch):
    be, backend, s = _backend(tmp_path, channel="chrome")
    monkeypatch.setattr(be, "find_system_browsers", lambda: [("chromium", "/usr/bin/chromium")])
    calls = []

    def make(kw):
        calls.append(kw)
        if "executable_path" not in kw:
            raise Exception("BrowserType.launch: Executable doesn't exist at /x/chrome\n" + "Looks like Playwright was just installed")
        return "BROWSER"
    assert backend._launch_any(s, make) == "BROWSER" and backend.last_browser == "chromium"
    assert calls == [{"channel": "chrome"}, {}, {"executable_path": "/usr/bin/chromium"}]


def test_launch_reports_everything_it_tried(tmp_path, monkeypatch):
    be, backend, s = _backend(tmp_path, channel="chrome")
    monkeypatch.setattr(be, "find_system_browsers", lambda: [])

    def make(kw):
        raise Exception("Chromium distribution 'chrome' is not found at /opt/google/chrome/chrome\nRun playwright install chrome")
    with pytest.raises(be.BackendError) as e:
        backend._launch_any(s, make)
    t = str(e.value)
    assert "Пробовал" in t and "канал chrome" in t and "встроенный Chromium Playwright" in t and "укажите путь" in t


def test_other_launch_errors_are_not_swallowed(tmp_path):
    be, backend, s = _backend(tmp_path)
    with pytest.raises(TimeoutError):
        backend._launch_any(s, lambda kw: (_ for _ in ()).throw(TimeoutError("Timeout 30000ms exceeded")))


def test_real_chromium_launch_via_executable_path(tmp_path):
    """Реальный запуск: канала нет, встроенный не выбран — но Chromium из кэша Playwright находится и открывается."""
    be, backend, s = _backend(tmp_path, channel="msedge", browser_mode="profile")
    if not be.find_system_browsers():
        pytest.skip("на машине нет Chromium")
    def job():
        ctx, closer = backend._open(headless=True)
        try:
            page = ctx.new_page()
            page.set_content("<p id=x>ok</p>")
            return page.inner_text("#x")
        finally:
            closer()
    try:
        assert backend._call(job) == "ok" and backend.last_browser
    except be.BackendError as e:
        pytest.skip(f"браузер не запускается в этой среде: {e}")
    finally:
        backend.close()


# ---------- журнал без дублей ----------

def test_same_error_is_logged_once_per_streak(setup):
    from yaklass_app.backend import BackendError
    setup.backend.list_error = BackendError("Не найден браузер для выполнения")
    for _ in range(3):
        setup.agent.refresh_works()
    logs = [p["text"] for n, p in setup.events if n == "log"]
    assert len(logs) == 1 and "Не найден браузер" in logs[0]
    statuses = [p for n, p in setup.events if n == "login_status" and p.get("ok") is None]
    assert len(statuses) == 3                                           # а панель статуса обновляется при каждой проверке
    setup.backend.list_error = None
    setup.agent.refresh_works()
    setup.backend.list_error = BackendError("Не найден браузер для выполнения")
    setup.agent.refresh_works()
    assert len([p for n, p in setup.events if n == "log"]) == 2         # после успеха та же ошибка снова попадает в журнал


# ---------- блокировка IP / проверка «не робот» ----------

BLOCKED_HTML = ('<html><head><title>ЯКласс 403 Error</title></head><body><h1>Доступ запрещён</h1><p>с вашего ip адреса '
                'наблюдается подозрительная активность</p><p>Datetime: 2026-10-08, ip: 203.0.113.7, id: abc</p></body></html>')
CHALLENGE_HTML = ('<html><head><noscript><meta http-equiv="refresh" content="0; url=/x"></noscript></head>'
                  '<script src="https://servicepipe.tech/loaders/abc.js"></script><body><js-challenge-loader></js-challenge-loader>'
                  '<div id="id_captcha_frame_div"></div></body></html>')


def test_guard_recognizes_block_and_challenge_but_not_normal_pages():
    from yaklass_bot.guard import SiteBlocked, SiteChallenge, check_page, is_blocked, is_challenge, page_title
    assert is_blocked(BLOCKED_HTML, page_title(BLOCKED_HTML)) and not is_challenge(BLOCKED_HTML)
    assert is_challenge(CHALLENGE_HTML) and not is_blocked(CHALLENGE_HTML)
    with pytest.raises(SiteBlocked, match="отклонил"):
        check_page(BLOCKED_HTML)
    with pytest.raises(SiteChallenge, match="не робот"):
        check_page(CHALLENGE_HTML)
    normal = "<html><head><title>Проверочные работы</title></head><body><section class='wg-testworks'>" + "x" * 30000 + "</section></body></html>"
    check_page(normal)                                                   # не бросает
    check_page("<html><body><p>Доступ запрещён к чужой работе — обратитесь к учителю.</p></body></html>")   # без «403»/«подозрительн»


class FakePage:
    def __init__(self, html, has=False):
        self.html, self.has = html, has

    def content(self): return self.html

    def locator(self, sel):
        outer = self
        return type("L", (), {"count": lambda s: 1 if outer.has else 0})()


def test_wait_or_guard_raises_immediately_on_block_page():
    import time
    from yaklass_app.backend import PlaywrightBackend
    from yaklass_bot.guard import SiteBlocked
    t0 = time.time()
    with pytest.raises(SiteBlocked):
        PlaywrightBackend.wait_or_guard(FakePage(BLOCKED_HTML), ".wg-testworks", 20)
    assert time.time() - t0 < 3                                          # не ждём весь таймаут на странице блокировки
    assert PlaywrightBackend.wait_or_guard(FakePage("<html/>", has=True), ".wg-testworks", 5) is True
    with pytest.raises(Exception, match="не робот"):
        PlaywrightBackend.wait_or_guard(FakePage(CHALLENGE_HTML), ".x", 1)
    assert PlaywrightBackend.wait_or_guard(FakePage("<html><body>обычная страница</body></html>"), ".x", 1) is False


def test_agent_pauses_polling_after_block_and_reports_once(setup):
    from yaklass_bot.guard import BLOCKED_MSG, SiteBlocked
    link_and_start(setup)
    setup.backend.list_error = SiteBlocked(BLOCKED_MSG)
    assert setup.agent.should_poll()
    for _ in range(3):
        setup.agent.refresh_works()
    assert not setup.agent.should_poll()                                 # автоматических опросов нет (6 часов)
    assert setup.agent._blocked_until - __import__("time").time() > 5 * 3600
    st = [p for n, p in setup.events if n == "login_status" and p.get("ok") is None]
    assert len(st) == 3 and "отклонил" in st[-1]["message"]
    assert wait_for(lambda: [e for e in setup.server.events_of("error") if "отклонил" in e["message"]])
    import time
    time.sleep(0.3)
    assert len([e for e in setup.server.events_of("error") if "отклонил" in e["message"]]) == 1   # в Telegram — один раз
    setup.backend.list_error = None
    setup.agent.refresh_works()                                          # ручная проверка прошла — блокировка снята
    assert setup.agent.should_poll()


def test_work_run_stops_cleanly_on_block_page():
    """Во время выполнения работы пришла страница блокировки: прогон прекращается, причина понятна."""
    from yaklass_bot.autorun import execute
    from yaklass_bot.pace import Pace

    class P(FakePage):
        url = "https://www.yaklass.ru/TestWorkRun/Exercise?x=1"
        def wait_for_selector(self, *a, **k): raise TimeoutError()
        def wait_for_function(self, *a, **k): raise TimeoutError()
        def evaluate(self, *a, **k): return None

    class T:
        def get(self, k): return None
        def upsert(self, r): pass
    res = execute(P(BLOCKED_HTML), "https://www.yaklass.ru", lambda t, l: None, T(), lambda m: None, pace=Pace())
    assert "отклонил" in res.fatal and not res.completed and res.outcomes == []


# ---------- сессионные куки профиля ----------

def _cookie_server():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            if self.path == "/login":
                self.send_header("Set-Cookie", ".AUTH=token123; Path=/; HttpOnly")        # СЕССИОННАЯ: без Expires/Max-Age
            self.end_headers()
            self.wfile.write(("cookie=" + (self.headers.get("Cookie") or "")).encode())
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_session_cookie_survives_browser_restart_in_profile_mode(tmp_path):
    """Реальный Chromium: сессионная кука входа пропадает при закрытии браузера — без снимка вход «забывался»."""
    import yaklass_app.backend as be
    if not be.find_system_browsers():
        pytest.skip("на машине нет Chromium")
    srv = _cookie_server()
    base = f"http://127.0.0.1:{srv.server_port}"
    store = Store(tmp_path / "c", use_keyring=False)
    store.save(AppSettings(browser_mode="profile"))
    b = be.PlaywrightBackend(store, tmp_path / "d")
    b.cookie_domain_filter = "127.0.0.1"
    try:
        def first():
            ctx, closer = b._open(headless=True)
            page = ctx.new_page()
            page.goto(base + "/login")
            b._snapshot(ctx)
            closer()

        def second(restore):
            ctx, closer = b._open(headless=True)
            page = ctx.new_page()
            page.goto(base + "/whoami")
            text = page.inner_text("body")
            closer()
            return text
        try:
            b._call(first)
        except be.BackendError as e:
            pytest.skip(f"браузер не запускается в этой среде: {e}")
        saved = b.cookies_path
        assert saved.exists() and "token123" in saved.read_text()
        if os.name != "nt":
            assert oct(saved.stat().st_mode & 0o777) == "0o600"
        orig = type(b)._restore
        # контроль ПЕРВЫМ (восстановление записало бы куку в профиль уже как постоянную): без снимка кука потеряна
        b._restore = lambda ctx: None
        assert "token123" not in b._call(lambda: second(False)), "сессионная кука неожиданно пережила перезапуск"
        b._restore = orig.__get__(b)
        assert "token123" in b._call(lambda: second(True))          # со снимком — на месте и отправляется серверу
    finally:
        b.close()
        srv.shutdown()


# ---------- автоматический опрос и видимое окно ----------

def test_auto_poll_is_off_by_default_and_needs_user_opt_in(setup):
    assert AppSettings().auto_poll is False
    assert not setup.agent.poll_due()                                    # по умолчанию сам на сайт не заходит
    s = setup.store.load()
    s.auto_poll = True
    setup.store.save(s)
    assert setup.agent.poll_due()
    setup.agent._blocked_until = __import__("time").time() + 3600       # сайт недавно отклонил запросы — пауза
    assert not setup.agent.poll_due()


def test_works_list_uses_a_visible_browser_window(tmp_path, monkeypatch):
    """Скрытый (headless) браузер ЯКласс отклоняет («403 подозрительная активность») — список открываем видимым окном."""
    import yaklass_app.backend as be
    store = Store(tmp_path / "c", use_keyring=False)
    store.save(AppSettings())
    b = be.PlaywrightBackend(store, tmp_path / "d")
    seen = {}

    class Page:
        def goto(self, url): seen["url"] = url
        def content(self): return "<html/>"
        def locator(self, sel): return type("L", (), {"count": lambda s: 1})()

    class Ctx:
        def new_page(self): return Page()
    monkeypatch.setattr(b, "_open", lambda headless: (seen.setdefault("headless", headless), (Ctx(), lambda: None))[1])
    monkeypatch.setattr(be, "parse_works_list", lambda html: [])
    try:
        assert b.list_works() == [] and seen["headless"] is False and seen["url"].endswith("/TestWork")
    finally:
        b.close()
