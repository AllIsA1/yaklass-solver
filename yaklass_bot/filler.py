"""Выставление ответов на странице задания (Playwright).

ВАЖНО: этот модуль никогда не нажимает «Ответить» / «Завершить» и не отправляет форму
(в т.ч. не жмёт Enter в полях). Отправку делает человек.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass

from . import pace as _pace

from .models import Field, Task
from .solver.base import Answer


@dataclass
class FillResult:
    field: str
    wanted: str          # человекочитаемо: что должно стоять
    ok: bool
    note: str = ""


def q(s: str) -> str:
    """Значение для CSS-атрибута в двойных кавычках."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _pause(lo: float | None = None, hi: float | None = None) -> None:
    """Пауза между действиями; без аргументов — из текущего темпа (см. pace.Pace.field_pause)."""
    if lo is None or hi is None:
        lo, hi = _pace.current().field_pause
    time.sleep(random.uniform(lo, hi))


def _opt_text(f: Field, value: str) -> str:
    o = next((o for o in f.options if o.value == value), None)
    return (o.text or o.image or value) if o else value


# ---------- по типам ----------

def _fill_dropdown(page, f: Field, value: str) -> FillResult:
    loc = page.locator(f'select[name="{q(f.name)}"]')
    loc.scroll_into_view_if_needed()
    loc.select_option(value=value)
    return FillResult(f.name, _opt_text(f, value), loc.input_value() == value)


def _click_option_label(page, input_loc) -> None:
    """Радио/чекбоксы в ЯКлассе скрыты за <label>: кликаем по нему, как человек."""
    id_ = input_loc.get_attribute("id")
    target = page.locator(f'label[for="{q(id_)}"]') if id_ else input_loc
    target.scroll_into_view_if_needed()
    target.click()


def _fill_radio(page, f: Field, value: str) -> FillResult:
    inp = page.locator(f'input[type="radio"][name="{q(f.name)}"][value="{q(value)}"]')
    if not inp.is_checked():
        _click_option_label(page, inp)
    return FillResult(f.name, _opt_text(f, value), inp.is_checked())


def _fill_checkbox(page, f: Field, value: str) -> FillResult:
    chosen = set(value.split(","))
    ok = True
    for o in f.options:
        inp = page.locator(f'input[type="checkbox"][name="{q(o.value)}"]')
        if inp.is_checked() != (o.value in chosen):
            _click_option_label(page, inp)
            _pause(0.15, 0.5)
        ok &= inp.is_checked() == (o.value in chosen)
    return FillResult(f.name, "; ".join(_opt_text(f, v) for v in value.split(",")), ok)


def _find_text_input(page, name: str):
    """Обычные поля имеют name; поля формул (<mi class=ykl-input>) JS превращает в input
    без гарантии name — пробуем несколько вариантов."""
    base = name.split("|")[0]
    for sel in (f'input[name="{q(name)}"]', f'textarea[name="{q(name)}"]',
                f'input#{base}', f'[data-name="{q(name)}"] input', f'input[data-name="{q(name)}"]'):
        loc = page.locator(sel)
        if loc.count() == 1:
            return loc
    return None


def _fill_text(page, f: Field, value: str) -> FillResult:
    loc = _find_text_input(page, f.name)
    if loc is None:
        return FillResult(f.name, value, False, "поле ввода не найдено на странице")
    loc.scroll_into_view_if_needed()
    loc.click()
    loc.fill("")
    loc.press_sequentially(value, delay=random.randint(*map(int, _pace.current().typing_ms)))   # без Enter!
    return FillResult(f.name, value, loc.input_value().strip() == value.strip())


def _drag(page, src, dst) -> None:
    dst.scroll_into_view_if_needed()
    src.scroll_into_view_if_needed()
    a, b = src.bounding_box(), dst.bounding_box()
    if not a or not b:
        raise RuntimeError("нет геометрии элементов")
    x0, y0 = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
    x1, y1 = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    page.mouse.move(x0, y0)
    page.mouse.down()
    page.mouse.move(x0 + 8, y0 + 8, steps=4)       # «стронуть» перетаскивание
    page.mouse.move(x1, y1, steps=random.randint(12, 20))
    page.mouse.up()


def _fill_dnd(page, f: Field, value: str) -> FillResult:
    field = page.locator(f'.gxs-dnd-field:has(input[name="{q(f.name)}"])')
    src = page.locator(f'.gxs-dnd-option[data-id="{q(value)}"]').first
    hidden = page.locator(f'input[name="{q(f.name)}"]')

    def landed() -> bool:
        # JS записывает в скрытое поле data-id перенесённого варианта; пустое/чужое значение = не получилось
        return (hidden.input_value() or "") == value

    try:
        if landed():
            return FillResult(f.name, _opt_text(f, value), True)       # уже стоит нужный ответ
        _drag(page, src, field)
        if not landed():
            src.drag_to(field)          # запасной вариант — HTML5 drag
    except Exception as e:  # noqa: BLE001 — любая UI-ошибка не должна ронять весь проход
        return FillResult(f.name, _opt_text(f, value), False, f"перетаскивание не удалось: {e}")
    return FillResult(f.name, _opt_text(f, value), landed())


_FILL = {"dropdown": _fill_dropdown, "radio": _fill_radio, "checkbox": _fill_checkbox,
         "text": _fill_text, "dnd": _fill_dnd}


def fill_task(page, task: Task, answer: Answer, pace: "_pace.Pace | None" = None,
              control=None) -> list[FillResult]:
    """control (RunControl) — чтобы пауза/стоп работали и внутри длинного задания."""
    if pace is not None:
        _pace.set_current(pace)
    results: list[FillResult] = []
    for f in task.fields:
        if control is not None:
            control.checkpoint()
        try:
            results.append(_FILL[f.kind](page, f, answer.values[f.name]))
        except Exception as e:  # noqa: BLE001
            results.append(FillResult(f.name, answer.values[f.name], False, str(e)))
        _pause()
    return results
