"""Итоговые баллы и название работы со страниц выполнения/результата."""
from pathlib import Path

import pytest

from yaklass_bot.parser import parse_result, parse_work_title

ROW = '''<div class="test-exercise-list-v2__row"><span class="test-exercise-list-v2__name">
<a href="/e">{n}. Задание</a></span><span class="test-exercise-list-v2__points"><span class="points-cell-v2">
<span class="points-cell-v2__value"><span class="points-cell-v2__earned">{got}</span> / {mx}</span></span></span></div>'''


def page(rows):
    return "<html><body>" + "".join(ROW.format(n=i, got=g, mx=m) for i, (g, m) in enumerate(rows, 1)) + "</body></html>"


def test_sums_earned_and_total_with_decimal_comma():
    r = parse_result(page([("1,5", "1,5"), ("1", "1,5"), ("0", "1,5"), ("2", "2")]))
    assert r.known and r.earned == 4.5 and r.total == 6.5 and r.percent == 69.2


def test_unchecked_rows_make_result_unknown():
    r = parse_result(page([("1", "1,5"), ("?", "1,5")]))
    assert not r.known and r.pending == 1 and r.percent is None


def test_page_without_points_is_unknown():
    r = parse_result("<html><body><p>нет баллов</p></body></html>")
    assert not r.known and r.earned is None


def test_title_from_itemtitle():
    assert parse_work_title('<h1 id="itemtitle" itemprop="name">Ing and the infinitive</h1>') == "Ing and the infinitive"
    assert parse_work_title("<html></html>") == ""


def test_sample_before_finishing_has_unchecked_points():
    f = Path(__file__).parent.parent / "samples" / "Ing and the infinitive.html"
    if not f.exists():
        pytest.skip("нет sample-страницы")
    h = f.read_text(encoding="utf-8")
    assert parse_work_title(h) == "Ing and the infinitive"
    r = parse_result(h)
    assert r.total > 0 and r.pending == 10 and not r.known          # до завершения баллы — «?»
