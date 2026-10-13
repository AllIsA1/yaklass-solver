"""Завершение работы кнопкой «Завершить» с окном подтверждения (разметка со страницы «Список заданий»,
скрипты сайта заменены минимальной моделью: кнопка открывает окно с «Отмена» и второй «Завершить»)."""
from pathlib import Path

import pytest

pw_mod = pytest.importorskip("playwright.sync_api")

from yaklass_bot import autorun  # noqa: E402
from yaklass_bot.parser import parse_overview  # noqa: E402

BASE = "http://yaklass.test"


def overview(answered=(True, True, True), dialog="jquery") -> str:
    rows = "".join(f'''<div class="test-exercise-list-v2__row">
      <span class="test-exercise-list-v2__name"><a href="/e?{i}">{i}. Задание</a></span>
      <span class="test-exercise-list-v2__status">{"Ответ получен" if a else "Нет ответа"}</span></div>'''
                   for i, a in enumerate(answered, 1))
    if dialog == "native":
        onclick = "if (confirm('Ты уверен?')) { location.href = '/done'; }"
    else:                      # окно подтверждения как у jQuery UI: кнопки «Отмена» и «Завершить»
        onclick = ("var d=document.createElement('div'); d.className='ui-dialog'; d.innerHTML="
                   "'<div class=\"ui-dialog-buttonpane\"><button id=\"c\" type=\"button\">Отмена</button>"
                   "<button id=\"ok\" type=\"button\">Завершить</button></div>';"
                   "document.body.appendChild(d);"
                   "document.getElementById('ok').onclick=function(){location.href='/done'};"
                   "document.getElementById('c').onclick=function(){d.remove()};")
    return f'''<html><body><div class="exercise-content-header"><div class="pull-left">Завершение работы</div></div>
      <div class="test-confirm-finish-v2"><button type="button" class="btn" id="finishTestBtn"
        onclick="{onclick.replace('"', '&quot;')}">Завершить </button></div>{rows}</body></html>'''


@pytest.fixture(scope="module")
def browser():
    with pw_mod.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Chromium недоступен: {e}")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    pg = browser.new_page()
    yield pg
    pg.close()


def serve(page, html):
    def handle(route):
        if route.request.url.endswith("/done"):
            return route.fulfill(body="<html><body>Работа завершена</body></html>", content_type="text/html")
        route.fulfill(body=html, content_type="text/html; charset=utf-8")
    page.route(f"{BASE}/**", handle)
    page.goto(f"{BASE}/TestWorkRun/Overview")


@pytest.mark.parametrize("dialog", ["jquery", "native"])
def test_finish_button_confirms_in_dialog(page, dialog):
    serve(page, overview(dialog=dialog))
    logs = []
    assert autorun.finish_work(page, BASE, logs.append) is True
    assert page.url.endswith("/done")


def test_unanswered_blocks_finish_unless_forced(page):
    serve(page, overview(answered=(True, False, True)))
    logs = []
    assert autorun.finish_work(page, BASE, logs.append) is False and not page.url.endswith("/done")
    assert any("без ответа" in m for m in logs)
    assert autorun.finish_work(page, BASE, logs.append, finish_anyway=True) is True
    assert page.url.endswith("/done")


def test_cancel_button_is_never_pressed(page):
    html = overview().replace("document.getElementById('ok').onclick=function(){location.href='/done'};", "")
    serve(page, html)
    assert autorun.finish_work(page, BASE, lambda m: None) is False        # «Завершить» в окне не сработало
    assert not page.url.endswith("/done")


def test_sample_overview_is_all_answered_without_form():
    f = Path(__file__).parent.parent / "samples" / "Ing and the infinitive.html"
    if not f.exists():
        pytest.skip("нет sample-страницы")
    ov = parse_overview(f.read_text(encoding="utf-8"))
    assert len(ov.rows) == 10 and not ov.unanswered and not ov.can_complete
