"""Сквозной тест в настоящем Chromium на локальном «фейковом ЯКлассе»: страницы берутся из
samples/, статические ресурсы грузятся с публичного CDN. Реальный сайт не затрагивается.
Пропускается, если Playwright/Chromium не установлены или нет сети."""
import re
from pathlib import Path

import pytest

pw_mod = pytest.importorskip("playwright.sync_api")

from yaklass_bot import runner  # noqa: E402
from yaklass_bot.config import Config  # noqa: E402
from yaklass_bot.solver.base import Provider  # noqa: E402

S = Path(__file__).parent.parent / "samples"
BASE = "http://yaklass.test"


def sample(prefix: str) -> str:
    raw = next(S.glob(prefix + "*.html")).read_text(encoding="utf-8")
    return re.sub(r'(src|href|srcset|content)="//', r'\1="https://', raw)


class Echo(Provider):
    """Фейковая «модель»: на каждое поле отвечает «1» (первый вариант / текст «1»)."""
    def ask(self, prompt, images=()):
        names = re.findall(r"^- \[\[(.+?)\]\]", prompt, re.M)
        return '{"answers": {%s}, "confidence": 0.9}' % ", ".join(f'"{n}": 1' for n in names)


@pytest.fixture
def page():
    with pw_mod.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Chromium недоступен: {e}")
        ctx = browser.new_context()
        calls = {"start": 0, "answer_posts": 0}

        def handler(route):
            req = route.request
            path = req.url.removeprefix(BASE)
            if path.startswith("/TestWork") and "Run" not in path:
                body = sample("Проверочные")
            elif path.startswith("/TestWorkRun/Preview/"):
                body = sample("Определение")
            elif path.startswith("/TestWorkRun/Start/") and req.method == "POST":
                calls["start"] += 1
                body = sample("1. Взаимное")
            elif path.startswith("/TestWorkRun/Exercise") and req.method == "POST":
                calls["answer_posts"] += 1      # отправка ответа — этого быть не должно
                body = "submitted"
            else:
                return route.fulfill(status=404, body="")
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

        ctx.route(f"{BASE}/**", handler)
        p = ctx.new_page()
        p.calls = calls
        yield p
        browser.close()


def cfg():
    c = Config()
    c.base_url = BASE
    return c


def test_list_to_start_to_filled_task_without_submit(page, capfd):
    c = cfg()
    page.goto(BASE + "/TestWork")
    runner.process_page(page, c, Echo(), auto_start=True, delay=0, pick_work=True)   # список -> Preview
    assert re.search(r"/TestWorkRun/Preview/" + re.search(r"/TestWorkRun/Preview/(\d+)", sample("Проверочные")).group(1), page.url)
    runner.process_page(page, c, Echo(), auto_start=True, delay=0)                   # жмёт «Начать»
    page.wait_for_selector("#taskhtml")
    assert page.calls["start"] == 1
    runner.process_page(page, c, Echo())                                             # заполняет задание
    vals = page.evaluate("[...document.querySelectorAll('select')].map(s => s.selectedIndex)")
    assert vals == [1, 1, 1, 1, 1]                                                   # выбран 1-й вариант в каждом
    assert page.calls["answer_posts"] == 0                                           # «Ответить» не нажималась
    out = capfd.readouterr().out
    assert "Работа «Определение и свойства" in out and "время: 45 мин" in out and "✓" in out


def test_no_start_flag_does_not_click_start(page):
    page.goto(BASE + "/TestWorkRun/Preview/100001")
    runner.process_page(page, cfg(), Echo(), auto_start=False, delay=0)
    assert page.calls["start"] == 0
