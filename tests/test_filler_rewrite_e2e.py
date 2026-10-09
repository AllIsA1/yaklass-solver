"""Переписывание уже данного ответа в настоящем Chromium: заполнили вариантом A, затем B — должен остаться B.
Нужно для режима --auto, где задание может быть выполнено заранее (кнопка «Сохранить»)."""
import re
from pathlib import Path

import pytest

pw_mod = pytest.importorskip("playwright.sync_api")

from yaklass_bot import filler  # noqa: E402
from yaklass_bot.parser import parse_exercise  # noqa: E402
from yaklass_bot.solver.base import Answer  # noqa: E402

S = Path(__file__).parent.parent / "samples"


def cdn_up() -> bool:
    import requests
    try:
        return requests.head("https://cdnjs.cloudflare.com", timeout=4).status_code < 500
    except requests.RequestException:
        return False


@pytest.fixture(scope="module")
def browser():
    with pw_mod.sync_playwright() as pw:
        try:
            b = pw.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Chromium недоступен: {e}")
        yield b
        b.close()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(filler, "_pause", lambda *a, **k: None)


def load(browser, prefix, wait_ms=0):
    raw = re.sub(r'(src|href|srcset|content)="//', r'\1="https://', next(S.glob(prefix + "*.html")).read_text(encoding="utf-8"))
    pg = browser.new_page(viewport={"width": 1100, "height": 900})
    pg.set_content(raw, wait_until="domcontentloaded", timeout=60_000)   # не ждём все картинки/счётчики сайта
    if wait_ms:
        pg.wait_for_timeout(wait_ms)
    return pg, parse_exercise(pg.content())


def two_answers(t):
    """Два разных допустимых ответа A и B."""
    a, b = {}, {}
    for i, f in enumerate(t.fields):
        if f.kind == "checkbox":
            a[f.name] = ",".join(o.value for o in f.options[:2])
            b[f.name] = ",".join(o.value for o in f.options[3:5])
        elif f.options:
            a[f.name] = f.options[i % len(f.options)].value
            b[f.name] = f.options[(i + 1) % len(f.options)].value
        else:
            a[f.name], b[f.name] = f"first{i}", f"second{i}"
    return a, b


STATE = {
    "dropdown": "[...document.querySelectorAll('select')].map(e => e.value)",
    "text": "[...document.querySelectorAll('input[type=text], textarea')].map(e => e.value)",
    "radio": "[...document.querySelectorAll('input[type=radio]:checked')].map(e => e.value)",
    "checkbox": "[...document.querySelectorAll('input[type=checkbox]:checked')].map(e => e.name)",
    "dnd": "[...document.querySelectorAll('input[name$=\"|dnd\"]')].map(e => e.value)",
}


@pytest.mark.parametrize("prefix,kind", [
    ("1. Взаимное", "dropdown"), ("Впиши пропущенное", "text"), ("Наша страна и её", "radio"),
    ("Внутренние", "checkbox"), ("Наша страна и права", "dnd"), ("Соотнеси", "dnd"), ("Карта", "dnd"),
    ("Тип сложноподчинённого", "text"),
])
def test_second_answer_replaces_first(browser, prefix, kind):
    if kind == "dnd" and not cdn_up():
        pytest.skip("нет CDN: перетаскивание работает только со скриптами сайта")
    pg, t = load(browser, prefix, wait_ms=4000 if kind == "dnd" else 0)
    a, b = two_answers(t)
    assert all(r.ok for r in filler.fill_task(pg, t, Answer(a, .9)))
    assert all(r.ok for r in filler.fill_task(pg, t, Answer(b, .9))), "второй ответ не встал"
    state = pg.evaluate(STATE[kind])
    if kind == "checkbox":
        assert sorted(state) == sorted(b[t.fields[0].name].split(","))
    elif kind == "radio":
        assert state == [b[t.fields[0].name]]
    elif kind == "dnd":
        assert state == [b[f.name] for f in t.fields]
    elif kind == "text":
        assert [v for v in state if v] == [b[f.name] for f in t.fields if f.kind == "text"]
    else:
        assert state == [b[f.name] for f in t.fields]
    pg.close()
