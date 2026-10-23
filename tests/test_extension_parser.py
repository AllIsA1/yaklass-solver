"""JS-парсер расширения (extension/src/parser.js) должен давать тот же результат, что Python-парсер, на одних и тех же
страницах: сверяем на реальных sample-страницах (если они есть) и на небольших синтетических."""
import json
import re
from dataclasses import asdict
from pathlib import Path

import pytest

pw_mod = pytest.importorskip("playwright.sync_api")

from yaklass_bot import parser as py  # noqa: E402

ROOT = Path(__file__).parent.parent
PARSER_JS = (ROOT / "extension" / "src" / "parser.js").read_text(encoding="utf-8")
SAMPLES = sorted((ROOT / "samples").glob("*.html"))


def strip_scripts(html: str) -> str:
    return re.sub(r"<script\b.*?</script>", "", html, flags=re.S | re.I)


@pytest.fixture(scope="module")
def page():
    with pw_mod.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Chromium недоступен: {e}")
        pg = b.new_page()
        pg.set_content("<html></html>")
        pg.add_script_tag(content=PARSER_JS)
        yield pg
        b.close()


def js(page, html, expr):
    page.set_content(strip_scripts(html), wait_until="domcontentloaded")
    return page.evaluate(f"() => {{ const D = document; return ({expr}); }}")


def same(a, b):
    return json.loads(json.dumps(a)) == json.loads(json.dumps(b))


def py_task(html):
    return json.loads(json.dumps(asdict(py.parse_exercise(html))))


@pytest.mark.skipif(not SAMPLES, reason="нет sample-страниц")
@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name[:40])
def test_sample_pages_give_identical_results(page, path):
    html = path.read_text(encoding="utf-8")
    kind = py.page_kind(html)
    assert js(page, html, "YKParser.pageKind(D)") == kind
    if kind in ("task", "theory"):
        want = py_task(html)
        got = js(page, html, "YKParser.parseExercise(D)")
        for k in want:
            assert same(got[k], want[k]), f"{path.name}: поле {k}: {got[k]!r} != {want[k]!r}"
        want_nav = [asdict(n) for n in py.parse_nav(html)]
        assert same(js(page, html, "YKParser.parseNav(D)"), want_nav)
        assert js(page, html, "YKParser.workTitle(D)") == (py.parse_work_title(html) if hasattr(py, "parse_work_title") else "")
    ov = py.parse_overview(html)
    got_ov = js(page, html, "YKParser.parseOverview(D)")
    assert same(got_ov["rows"], [asdict(r) for r in ov.rows]) and got_ov["complete_action"] == ov.complete_action
    r = py.parse_result(html)
    got_r = js(page, html, "YKParser.parseResult(D)")
    assert got_r["known"] == r.known and got_r["pending"] == r.pending
    if r.total is not None:
        assert got_r["total"] == pytest.approx(r.total) and got_r["earned"] == pytest.approx(r.earned)
    if "test-preview" in html:
        want_p = asdict(py.parse_preview(html))
        got_p = js(page, html, "YKParser.parsePreview(D)")
        for k in ("title", "time_limit_min", "tasks_count", "max_points", "attempts_left", "can_start", "start_label",
                  "is_continue"):
            assert got_p[k] == want_p[k], f"{path.name}: preview {k}"
    if "wg-testworks" in html:
        assert same(js(page, html, "YKParser.parseWorksList(D)"), [asdict(w) for w in py.parse_works_list(html)])


# ---------- синтетические страницы (работают и без sample) ----------

TASK = '''<html><body>
<form class="taskForm" action="/TestWorkRun/Exercise?testResultId=5&amp;exercisePosition=3&amp;twId=7"></form>
<div class="exercise-content-header"><div class="pull-left">Задание</div></div>
<span class="obj-points">2,5</span><span class="tst-time" data-left-time="300"></span>
<div id="taskhtml"><p>Найди:&nbsp;<m:math><m:mfrac><m:mi>a</m:mi><m:mn>2</m:mn></m:mfrac></m:math> =
<input name="e1|ta" size="4" class="maxpoints1"></p>
<select name="e2|dd" class="maxpoints1,5"><option value="">--</option><option value="a">А</option><option value="b">Б</option></select>
<ul class="gxs-answer-select maxpoints2"><li><input type="radio" name="e3|rb" value="1" id="r1"><label for="r1"><span class="select-text">Да</span></label></li>
<li><input type="radio" name="e3|rb" value="2" id="r2"><label for="r2">Нет</label></li></ul>
<ul class="gxs-answer-select"><li><input type="checkbox" name="e4|cb1" id="c1"><label for="c1">Один</label></li>
<li><input type="checkbox" name="e4|cb2" id="c2"><label for="c2">Два</label></li></ul>
<table><tr><td>x</td><td>[IMG] <img src="//cdn.example/p.png" alt="рис"></td></tr></table>
<div class="gxs-dnd-field" data-boxid="box1"><input name="e5|dnd" type="hidden"></div>
<div id="box1" class="answer-box"><div class="gxs-dnd-option" data-id="o1">Первый</div><div class="gxs-dnd-option" data-id="o2">Второй</div></div>
</div></body></html>'''


def test_synthetic_task_matches_python(page):
    want = py_task(TASK)
    got = js(page, TASK, "YKParser.parseExercise(D)")
    for k in want:
        assert same(got[k], want[k]), f"поле {k}: {got[k]!r} != {want[k]!r}"
    assert [f["kind"] for f in got["fields"]] == ["radio", "checkbox", "dropdown", "text", "dnd"]
    assert got["position"] == 3 and got["points"] == 2.5 and got["time_left_s"] == 300


def test_overview_and_result_synthetic(page):
    row = ('<div class="test-exercise-list-v2__row"><span class="test-exercise-list-v2__name"><a>{n}. Задание</a></span>'
           '<span class="test-exercise-list-v2__status">Ответ получен</span><span class="points-cell-v2__value">'
           '<span class="points-cell-v2__earned">{g}</span> / {m}</span></div>')
    html = "<html><body><button id='finishTestBtn'>Завершить</button>" + row.format(n=1, g="1,5", m="1,5") + \
        row.format(n=2, g="0", m="2") + "</body></html>"
    ov = js(page, html, "YKParser.parseOverview(D)")
    assert ov["has_finish_button"] and [r["answered"] for r in ov["rows"]] == [True, True] and ov["complete_action"] is None or ov["complete_action"] == ""
    r = js(page, html, "YKParser.parseResult(D)")
    assert r["known"] and r["earned"] == 1.5 and r["total"] == 3.5 and r["percent"] == 42.9
    r2 = js(page, html.replace(">0<", ">?<"), "YKParser.parseResult(D)")
    assert not r2["known"] and r2["pending"] == 1
