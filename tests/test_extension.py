"""Расширение браузера (extension/): манифест, Node-тесты и сквозной прогон в настоящем Chromium — расширение
подключается к настоящему серверу (с подставной моделью) и проходит фейковый ЯКласс из samples/."""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
EXT = ROOT / "extension"
MANIFEST = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_is_valid_and_files_exist():
    assert MANIFEST["manifest_version"] == 3
    version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    assert MANIFEST["version"] == version                                         # одна версия на всё
    files = [MANIFEST["background"]["service_worker"], MANIFEST["action"]["default_popup"],
             *MANIFEST["icons"].values(), *MANIFEST["action"]["default_icon"].values()]
    for cs in MANIFEST["content_scripts"]:
        files += cs["js"]
    for f in files:
        assert (EXT / f).is_file(), f
    assert set(MANIFEST["permissions"]) <= {"storage", "tabs", "alarms", "scripting"}   # без лишних прав
    assert all("yaklass.ru" in m or m.startswith("https://api.github.com") for m in MANIFEST["host_permissions"])


def test_no_remote_code_or_eval_in_sources():
    for f in list((EXT / "src").glob("*.js")) + list((EXT / "popup").glob("*.js")):
        text = f.read_text(encoding="utf-8")
        assert "eval(" not in text and "new Function(" not in text, f.name
        assert not re.search(r"""import\s*\(\s*['"]https?:""", text), f.name


@pytest.mark.skipif(shutil.which("node") is None, reason="нет Node.js")
def test_node_unit_tests():
    r = subprocess.run(["node", "--test", str(EXT / "tests" / "connect.test.mjs")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


# ---------------------------------------------------------------- сквозной прогон в Chromium

pw_mod = pytest.importorskip("playwright.sync_api")

from app_tests.conftest import RealServer, wait_for  # noqa: E402
from tests.test_autorun_e2e import BASE, Fake, cdn_up, sample  # noqa: E402
from yaklass_bot.connect import make_connection_code  # noqa: E402


class FakeSite:
    """Фейковый ЯКласс по HTTP (страницы из samples/): расширение открывает вкладки само, а запросы таких вкладок
    Playwright-маршруты перехватывают не всегда — поэтому настоящий локальный сервер."""

    def __init__(self, fake):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        outer = self

        class Route:
            def __init__(self, handler, body):
                self.h, self.request = handler, type("Rq", (), dict(url=f"http://x{handler.path}", method=handler.command,
                                                                    post_data=body))()

            def fulfill(self, status=200, content_type="text/html; charset=utf-8", body=""):
                data = body.encode("utf-8")
                self.h.send_response(status)
                self.h.send_header("Content-Type", content_type)
                self.h.send_header("Content-Length", str(len(data)))
                self.h.end_headers()
                self.h.wfile.write(data)

        class H(BaseHTTPRequestHandler):
            def handle_any(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n).decode("utf-8", "replace") if n else None
                route = Route(self, body)
                path = self.path.split("?")[0]
                if path == "/TestWork":
                    return route.fulfill(body=outer.works_html)
                if outer.overview_html and path == "/TestWorkRun/Overview":
                    return route.fulfill(body=outer.overview_html)
                if path == "/done":
                    outer.done += 1
                    return route.fulfill(body=outer.done_html)
                fake.handle(route)

            do_GET = do_POST = handle_any

            def log_message(self, *a):
                pass

        self.works_html = sample("Проверочные работы")
        self.overview_html, self.done = "", 0
        self.done_html = "<html><body>Работа завершена</body></html>"
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"

    def stop(self):
        self.srv.shutdown()


def patched_extension(tmp: Path) -> Path:
    """Копия расширения для теста: страницы фейкового ЯКласса и локальный сервер — в разрешениях."""
    dst = tmp / "ext"
    shutil.copytree(EXT, dst, ignore=shutil.ignore_patterns("tests"))
    m = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
    m["host_permissions"] += ["http://127.0.0.1/*"]
    for cs in m["content_scripts"]:
        cs["matches"].append("http://127.0.0.1/*")
    (dst / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return dst


@pytest.fixture
def browser_env(tmp_path):
    if not cdn_up():
        pytest.skip("нет доступа к CDN: скрипты сайта не загрузятся")
    srv = RealServer(quota=100)
    fake = Fake()
    site = FakeSite(fake)
    ext = patched_extension(tmp_path)
    with pw_mod.sync_playwright() as pw:
        try:
            ctx = pw.chromium.launch_persistent_context(
                str(tmp_path / "profile"), channel="chromium", headless=True,
                args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"])
        except Exception as e:  # noqa: BLE001
            srv.stop()
            site.stop()
            pytest.skip(f"Chromium с расширениями недоступен: {e}")
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
        ext_id = sw.url.split("/")[2]
        page = ctx.new_page()
        page.goto(f"chrome-extension://{ext_id}/popup/popup.html")
        page.evaluate("(u) => chrome.storage.local.set({config: {baseUrl: u}})", site.url)
        yield type("E", (), dict(ctx=ctx, srv=srv, fake=fake, page=page, ext_id=ext_id, sw=sw, site=site))
        ctx.close()
    srv.stop()
    site.stop()


def link(env):
    code = make_connection_code(env.srv.url, env.srv.new_code(1, "tester"))
    r = env.page.evaluate("(t) => chrome.runtime.sendMessage({api: 'link', text: t})", code)
    assert r["ok"], r
    assert wait_for(lambda: env.srv.hub.online(1), 15)
    assert wait_for(lambda: env.srv.events_of("hello"), 5)


def test_extension_links_runs_a_work_and_reports(browser_env):
    env = browser_env
    link(env)
    hello = env.srv.events_of("hello")[0]
    assert hello["client"] == "extension" and hello["version"] == MANIFEST["version"]
    env.srv.command(1, {"cmd": "start_work", "work_id": "100001", "mode": "auto", "style": "fast"})
    assert wait_for(lambda: env.srv.events_of("finished"), 150), "работа не завершилась"
    fin = env.srv.events_of("finished")[0]
    assert fin["completed"] is True and fin["work_id"] == "100001", fin
    assert [p for p, _ in env.fake.submits] == list(range(1, 10))                 # с первого по последнее, по разу
    assert env.fake.completed == 1 and len(env.srv.model.prompts) == 9           # решение шло через сервер
    assert fin["summary"].get("переписано") == 1 and fin["summary"].get("новых ответов") == 8
    st = env.page.evaluate("() => chrome.runtime.sendMessage({api: 'getState'})")
    assert st["linked"] and st["history"][0]["completed"] is True and st["history"][0]["work_id"] == "100001"
    assert [e for e in env.srv.events_of("ack") if e["ok"]]


def test_dry_run_fills_but_never_submits_and_stealth_is_refused(browser_env):
    env = browser_env
    link(env)
    env.srv.command(1, {"cmd": "start_work", "work_id": "100001", "mode": "auto", "style": "stealth", "minutes": 7, "grade": 4})
    assert wait_for(lambda: [a for a in env.srv.events_of("ack") if not a["ok"]], 15)
    assert "недоступен" in env.srv.events_of("ack")[0]["msg"] and env.fake.submits == []
    env.srv.command(1, {"cmd": "start_work", "work_id": "100001", "mode": "dry", "style": "fast"})
    assert wait_for(lambda: env.srv.events_of("finished"), 150)
    fin = env.srv.events_of("finished")[0]
    assert fin["completed"] is False and "без отправки" in fin["note"]
    assert env.fake.submits == [] and env.fake.completed == 0


def test_works_list_command_and_stop(browser_env):
    env = browser_env
    link(env)
    env.srv.command(1, {"cmd": "list_works"})
    assert wait_for(lambda: [e for e in env.srv.events_of("works") if not e.get("new")], 40)
    works = [e for e in env.srv.events_of("works") if not e.get("new")][0]["works"]
    assert works and all(w["id"].isdigit() for w in works)
    env.srv.command(1, {"cmd": "start_work", "work_id": "100001", "mode": "auto", "style": "normal"})
    assert wait_for(lambda: [s for s in env.srv.events_of("status") if s.get("state") == "running"], 20)
    env.srv.command(1, {"cmd": "stop"})
    assert wait_for(lambda: env.srv.events_of("finished"), 60)
    fin = env.srv.events_of("finished")[0]
    assert fin["completed"] is False and "остановлено" in fin["note"] and env.fake.completed == 0


FINISH_PAGE = """<html><body>
<div class="test-confirm-finish-v2"><button type="button" id="finishTestBtn" onclick="
var d=document.createElement('div'); d.className='ui-dialog';
d.innerHTML='<div class=&quot;ui-dialog-buttonpane&quot;><button id=&quot;c&quot; type=&quot;button&quot;>Отмена</button><button id=&quot;ok&quot; type=&quot;button&quot;>Завершить</button></div>';
document.body.appendChild(d);
document.getElementById('ok').onclick=function(){location.href='/done'};
document.getElementById('c').onclick=function(){d.remove()};">Завершить </button></div>
%s</body></html>"""
ROW = ('<div class="test-exercise-list-v2__row"><span class="test-exercise-list-v2__name"><a>%d. Задание</a></span>'
       '<span class="test-exercise-list-v2__status">Ответ получен</span><span class="points-cell-v2__value">'
       '<span class="points-cell-v2__earned">%s</span> / 1</span></div>')


def test_finish_by_button_with_confirmation_and_real_result(browser_env):
    """«Список заданий» без формы: кнопка «Завершить» открывает окно подтверждения, в нём нажимается вторая
    «Завершить» («Отмена» не трогается); после завершения читаются баллы."""
    env = browser_env
    link(env)
    env.site.overview_html = FINISH_PAGE % "".join(ROW % (i, "?") for i in range(1, 10))
    env.site.done_html = "<html><body><h1 id='itemtitle'>Тема</h1>" + "".join(ROW % (i, "1" if i < 8 else "0") for i in range(1, 10)) + "</body></html>"
    env.srv.command(1, {"cmd": "start_work", "work_id": "100001", "mode": "auto", "style": "fast"})
    assert wait_for(lambda: env.srv.events_of("finished"), 150)
    fin = env.srv.events_of("finished")[0]
    assert fin["completed"] is True and env.site.done == 1 and env.fake.completed == 0   # завершили кнопкой, а не формой
    assert fin["earned"] == 7 and fin["total"] == 9 and fin["percent"] == 77.8           # реальные баллы со страницы результата


def test_confirm_is_auto_accepted_only_in_the_run_tab(browser_env):
    """Автоматическое принятие confirm() сайта — только во вкладке, где расширение выполняет работу;
    обычные вкладки ЯКласса остаются как есть."""
    env = browser_env
    link(env)
    page = env.ctx.new_page()
    page.goto(env.site.url + "/TestWorkRun/Exercise?exercisePosition=1&twId=1")
    page.wait_for_load_state("load")
    page.evaluate("() => { window.__asked = 0; }")
    attr = page.evaluate("() => document.documentElement.hasAttribute('data-yk-auto')")
    assert attr is False                                                          # обычная вкладка пользователя
    page.on("dialog", lambda d: d.dismiss())                                      # «пользователь» нажимает «Отмена»
    assert page.evaluate("() => window.confirm('Точно?')") is False               # сайт спрашивает — спрашивает по-настоящему
    page.evaluate("() => document.documentElement.setAttribute('data-yk-auto', '1')")
    assert page.evaluate("() => window.confirm('Точно?')") is True                # режим выполнения работы — принимается
