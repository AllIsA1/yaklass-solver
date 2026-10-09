"""Тесты логики filler на подставной странице (настоящий браузер здесь не запускается).
Главное — гарантия: ни кнопка «Ответить», ни Enter не трогаются."""
from pathlib import Path

import pytest

from yaklass_bot import filler
from yaklass_bot.parser import parse_exercise
from yaklass_bot.solver.base import Answer

S = Path(__file__).parent.parent / "samples"


class FakeLoc:
    def __init__(self, page, sel):
        self.page, self.sel = page, sel
        self.first = self

    def count(self): return 1
    def scroll_into_view_if_needed(self): pass
    def get_attribute(self, _): return "x"
    def bounding_box(self): return {"x": 0, "y": 0, "width": 10, "height": 10}
    def click(self):
        self.page.log.append(("click", self.sel))
        if self.sel.startswith("label"):                     # клик по label переключает инпут
            self.page.checked ^= {self.page.last_input}
    def fill(self, v): self.page.values[self.sel] = v
    def press_sequentially(self, v, delay=0): self.page.values[self.sel] = v
    def select_option(self, value): self.page.values[self.sel] = value
    def input_value(self): return self.page.values.get(self.sel, "")
    def is_checked(self):
        self.page.last_input = self.sel
        return self.sel in self.page.checked
    def drag_to(self, other): pass
    def locator(self, sel): return FakeLoc(self.page, sel)


class FakePage:
    def __init__(self):
        self.log, self.values, self.checked, self.last_input = [], {}, set(), None
        self.mouse = self
        self.keyboard = self

    def locator(self, sel):
        self.log.append(("locator", sel))
        return FakeLoc(self, sel)

    def move(self, *a, **k): self.log.append(("mouse.move",))
    def down(self): self.log.append(("mouse.down",))
    def up(self): self.log.append(("mouse.up",))
    def press(self, key): self.log.append(("press", key))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(filler, "_pause", lambda *a, **k: None)


def task(prefix):
    return parse_exercise(next(S.glob(prefix + "*.html")).read_text(encoding="utf-8"))


def answer(t, **by_name):
    return Answer({f.name: by_name.get(f.name, f.options[0].value if f.options else "7") for f in t.fields}, 0.9)


@pytest.mark.parametrize("prefix", ["1. Взаимное", "3. Параллельность", "Внутренние", "Наша страна и её",
                                    "Впиши пропущенное", "Наша страна и права", "Соотнеси", "Карта"])
def test_never_touches_submit_or_enter(prefix):
    t = task(prefix)
    page = FakePage()
    filler.fill_task(page, t, answer(t))
    flat = " ".join(str(x) for x in page.log).lower()
    assert "submit" not in flat and "ответить" not in flat and "taskform" not in flat
    assert not any(e[0] == "press" for e in page.log)       # Enter в поле = отправка формы


def test_dropdown_and_text_report_ok():
    t = task("1. Взаимное")
    res = filler.fill_task(FakePage(), t, answer(t))
    assert len(res) == 5 and all(r.ok for r in res)
    t = task("Впиши пропущенное")
    res = filler.fill_task(FakePage(), t, answer(t, **{"e13r1|ta": "детей"}))
    assert res[0].ok and res[0].wanted == "детей"


def test_checkbox_clicks_only_selected():
    t = task("Внутренние")
    f = t.fields[0]
    chosen = f"{f.options[2].value},{f.options[3].value}"
    page = FakePage()
    res = filler.fill_task(page, t, Answer({f.name: chosen}, 0.9))
    assert res[0].ok and res[0].wanted == "кулер; блок питания"
    assert sum(1 for e in page.log if e[0] == "click") == 2


def test_dnd_uses_mouse_drag():
    t = task("Наша страна и права")
    page = FakePage()
    filler.fill_task(page, t, Answer({f.name: f.options[i].value for i, f in enumerate(t.fields)}, 0.9))
    assert [e for e in page.log if e[0] == "mouse.down"].__len__() == 3


def test_missing_text_input_reported_not_raised(monkeypatch):
    monkeypatch.setattr(filler, "_find_text_input", lambda p, n: None)
    t = task("3. Параллельность")
    res = filler.fill_task(FakePage(), t, answer(t))
    assert not any(r.ok for r in res) and "не найдено" in res[0].note
