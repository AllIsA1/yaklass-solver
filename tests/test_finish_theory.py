"""Завершение работы: строки теории в «Списке заданий», уже обработанные позиции, окно с кнопкой-ссылкой,
запасной путь через адрес завершения со страницы."""
import pytest

pw_mod = pytest.importorskip("playwright.sync_api")

from yaklass_bot import autorun  # noqa: E402
from yaklass_bot.parser import parse_overview  # noqa: E402

BASE = "http://yaklass.test"


def rows(statuses):
    return "".join(f'''<div class="test-exercise-list-v2__row">
      <span class="test-exercise-list-v2__name"><a href="/e?{i}">{i}. Пункт</a></span>
      <span class="test-exercise-list-v2__status">{s}</span></div>''' for i, s in enumerate(statuses, 1))


def page_html(statuses, body_btn=True, dialog="a", finish_url=False):
    onclick = ""
    if dialog == "a":                     # подтверждение — ссылка-кнопка в окне сайта, а не <button>
        onclick = ("var d=document.createElement('div'); d.className='yk-dialog'; d.innerHTML="
                   "'<a class=\"btn\" id=\"c\">Отмена</a> <a class=\"btn\" id=\"ok\">Завершить работу</a>';"
                   "document.body.appendChild(d);document.getElementById('ok').onclick=function(){location.href='/done'};")
    btn = (f'<button type="button" class="btn" id="finishTestBtn" onclick="{onclick.replace(chr(34), "&quot;")}">'
           'Завершить </button>') if body_btn else ""
    js = ('<script>var n={Url:{}};n.Url.finishTestUrl="/TestWorkRun/CompleteTest?testResultId=1&twId=2";</script>'
          if finish_url else "")
    return f"<html><body>{btn}{rows(statuses)}{js}</body></html>"


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


def serve(page, html, posts=None):
    def handle(route):
        url = route.request.url
        if url.endswith("/done") or "CompleteTest" in url:
            if posts is not None and "CompleteTest" in url:
                posts.append(route.request.method)
            return route.fulfill(body="<html><body>Работа завершена</body></html>", content_type="text/html")
        route.fulfill(body=html, content_type="text/html; charset=utf-8")
    page.route(f"{BASE}/**", handle)
    page.goto(f"{BASE}/TestWorkRun/Overview")


def test_theory_rows_count_as_done():
    ov = parse_overview(page_html(["Ответ получен", "Прочитано", "Просмотрено", "Не прочитано", "Нет ответа"]))
    assert [r.answered for r in ov.rows] == [True, True, True, False, False]


def test_theory_status_does_not_block_finish(page):
    serve(page, page_html(["Ответ получен", "Прочитано", "Ответ получен"]))
    assert autorun.finish_work(page, BASE, lambda m: None) is True and page.url.endswith("/done")


def test_positions_handled_by_the_run_do_not_block_finish(page):
    serve(page, page_html(["Ответ получен", "Теория", "Ответ получен"]))         # статус теории, которого мы не знаем
    why = []
    assert autorun.finish_work(page, BASE, lambda m: None, why=why) is False and "2" in why[0]
    serve(page, page_html(["Ответ получен", "Теория", "Ответ получен"]))
    assert autorun.finish_work(page, BASE, lambda m: None, handled={1, 2, 3}) is True


def test_falls_back_to_finish_url_when_dialog_does_nothing(page):
    posts = []
    serve(page, page_html(["Ответ получен"], dialog="none", finish_url=True), posts)
    assert autorun.finish_work(page, BASE, lambda m: None) is True
    assert posts == ["POST"]


def test_reason_is_reported_when_nothing_works(page):
    serve(page, page_html(["Ответ получен"], body_btn=False))
    why = []
    assert autorun.finish_work(page, BASE, lambda m: None, why=why) is False
    assert "нет кнопки" in why[0]
