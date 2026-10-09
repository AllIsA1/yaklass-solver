from pathlib import Path

import pytest

from yaklass_bot.parser import parse_exercise
from yaklass_bot.solver.base import Provider, SolveError, build_prompt, solve_task
from yaklass_bot.storage import works_table

S = Path(__file__).parent.parent / "samples"


class Fake(Provider):
    supports_images = False
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def ask(self, prompt, images=()):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def task(prefix):
    return parse_exercise(next(S.glob(prefix + "*.html")).read_text(encoding="utf-8"))


def test_dropdown_answer_maps_index_to_value():
    t = task("1. Взаимное")
    names = [f.name for f in t.fields]
    reply = '```json\n{"answers": {%s}, "confidence": 0.9}\n```' % ", ".join(f'"{n}": 1' for n in names)
    a = solve_task(Fake(reply), t)
    first = t.fields[0].options[0].value
    assert a.values[names[0]] == first and a.confidence == 0.9


def test_text_answer_and_retry_on_bad_json():
    t = task("3. Параллельность")
    fake = Fake("не знаю", '{"answers": {"e9r4|tb": "15", "e17r5|tb": "2"}, "confidence": 0.7}')
    a = solve_task(fake, t)
    assert a.values == {"e9r4|tb": "15", "e17r5|tb": "2"}
    assert len(fake.prompts) == 2 and "некорректен" in fake.prompts[1]


def test_out_of_range_option_rejected():
    t = task("1. Взаимное")
    bad = '{"answers": {%s}}' % ", ".join(f'"{f.name}": 9' for f in t.fields)
    with pytest.raises(SolveError):
        solve_task(Fake(bad, bad), t)


def test_prompt_mentions_image_and_fields():
    p = build_prompt(task("1. Взаимное"))
    assert "рисунок" in p and "[[e6r1|dd]]" in p and "1) прямая пересекает плоскость" in p


def test_csv_upsert_roundtrip(tmp_path):
    t = works_table(tmp_path)
    t.upsert({"work_id": "1", "title": "A, \"quoted\"", "status": "new"})
    t.upsert({"work_id": "1", "status": "done"})
    rows = t.read()
    assert len(rows) == 1 and rows[0]["status"] == "done" and rows[0]["title"] == 'A, "quoted"'


def test_checkbox_answer_list():
    t = task("Внутренние")
    f = t.fields[0]
    a = solve_task(Fake('{"answers": {"e13r1|*": [3, 4, 5]}, "confidence": 0.8}'), t)
    assert a.values["e13r1|*"] == ",".join(f.options[i].value for i in (2, 3, 4))


def test_ordering_requires_distinct_options():
    t = task("Наша страна и права")
    n = [f.name for f in t.fields]
    dup = '{"answers": {"%s": 1, "%s": 1, "%s": 2}}' % tuple(n)
    ok = '{"answers": {"%s": 3, "%s": 1, "%s": 2}, "confidence": 0.6}' % tuple(n)
    fake = Fake(dup, ok)
    a = solve_task(fake, t)
    assert len(fake.prompts) == 2 and len(set(a.values.values())) == 3
    assert "ровно один раз" in fake.prompts[0]


def test_image_options_labelled_and_attached():
    from yaklass_bot.solver.images import Image

    class V(Fake):
        supports_images = True
        def ask(self, prompt, images=()):
            self.n = len(images)
            return super().ask(prompt, images)

    t = task("Карта")
    v = V('{"answers": {"e27r1|dnd": 2}, "confidence": 0.9}')
    a = solve_task(v, t, image_loader=lambda u: Image(b"x", "image/png"))
    assert v.n == 3 and "[изображение №2]" in v.prompts[0]
    assert a.values["e27r1|dnd"] == t.fields[0].options[1].value


def test_missing_images_degrade_gracefully():
    t = task("Карта")
    fake = Fake('{"answers": {"e27r1|dnd": 1}, "confidence": 0}')   # провайдер без зрения
    solve_task(fake, t)
    assert "[изображение недоступно]" in fake.prompts[0]


def test_matching_prompt_lists_options_once():
    p = build_prompt(task("Соотнеси"))
    assert p.count("1) \\(3\\)") == 1 and "те же, что у первого" in p
