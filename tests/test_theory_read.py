"""Теория в работе: «Прочитано» нажимается, задание без полей с такой кнопкой тоже считается теорией.
Разметка — минимальная модель (страницы теории из проверочной работы в samples/ нет)."""
import pytest

pw_mod = pytest.importorskip("playwright.sync_api")

from yaklass_bot import autorun  # noqa: E402
from yaklass_bot.pace import Pace  # noqa: E402

NO_PAUSE = Pace(between_tasks=(0, 0), before_submit=(0, 0))


def page_html(header: str, button: str) -> str:
    return f"""<html><body>
<div class="exercise-content-header"><div class="pull-left">{header}</div></div>
<div id="taskhtml"><p>Текст теории</p></div>
{button}
<div id="errorSummary"></div>
<script>window.clicks = 0;</script>
</body></html>"""


READ_BTN = '<button type="button" onclick="window.clicks++; location.reload()">Прочитано</button>'


@pytest.fixture(scope="module")
def browser():
    with pw_mod.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser):
    pg = browser.new_page()
    yield pg
    pg.close()


def load(page, html):
    page.route("http://yaklass.test/**", lambda r: r.fulfill(body=html, content_type="text/html; charset=utf-8"))
    page.goto("http://yaklass.test/TestWorkRun/Exercise?exercisePosition=4&twId=1")


def run(page, submit=True):
    return autorun.do_task(page, lambda t, lg: pytest.fail("теорию не решают"), None, "1:4", lambda m: None,
                           submit=submit, pace=NO_PAUSE)


def test_theory_read_button_is_clicked(page):
    load(page, page_html("Теория", READ_BTN))
    hits = []
    page.on("request", lambda r: hits.append(r.url))
    o = run(page)
    assert o.status == "theory" and "Прочитано" in o.note and len(hits) == 1      # клик перезагрузил страницу


def test_page_without_fields_but_with_read_button_is_theory(page):
    load(page, page_html("Задание", READ_BTN))
    assert run(page).status == "theory"


def test_theory_without_button_is_not_an_error(page):
    load(page, page_html("Теория", ""))
    o = run(page)
    assert o.status == "theory" and "уже отмечено" in o.note


def test_dry_mode_does_not_click(page):
    load(page, page_html("Теория", READ_BTN))
    o = run(page, submit=False)
    assert o.status == "theory" and page.evaluate("window.clicks") == 0
