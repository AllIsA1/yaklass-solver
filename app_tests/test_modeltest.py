"""Тест модели: набор заданий, проверка ответов, итоги по предметам; доступность только в режиме «Свой API»."""
from collections import Counter
from types import SimpleNamespace

import pytest

from yaklass_app import modeltest as mt


def test_every_subject_has_three_levels_and_valid_answers():
    c = Counter(i.subject for i in mt.ITEMS)
    assert len(c) >= 15 and set(c.values()) == {3}
    for item in mt.ITEMS:
        assert sorted(i.level for i in mt.ITEMS if i.subject == item.subject) == [1, 2, 3]
        for f in item.fields:
            assert f"[[{f.name}]]" in item.text and item.correct[f.name]
            if f.options:
                values = {o.value for o in f.options}
                assert all(v in values for a in item.correct[f.name] for v in a.split(","))


@pytest.mark.parametrize("got,ok", [("0,875", True), ("0.875", True), ("7/8", True), (" 0,875 ", True), ("0,87", False)])
def test_numbers_compare_by_value(got, ok):
    item = next(i for i in mt.ITEMS if "3/4 + 1/8" in i.text)
    assert (mt.check(item, {"a": got})[0] == 1.0) is ok


def test_words_ignore_case_yo_and_trailing_dot():
    item = next(i for i in mt.ITEMS if "Солнцу" in i.text)
    assert mt.check(item, {"a": "меркурий."})[0] == 1.0
    item = next(i for i in mt.ITEMS if "законосовещательное" in i.text)
    assert mt.check(item, {"a": "государственный  совет"})[0] == 1.0


def test_checkbox_needs_exact_set():
    item = next(i for i in mt.ITEMS if i.fields[0].kind == "checkbox")
    right = item.correct["a"][0]
    assert mt.check(item, {"a": ",".join(reversed(right.split(",")))})[0] == 1.0
    assert mt.check(item, {"a": right.split(",")[0]})[0] == 0.0


def answer(values, conf=0.9):
    return SimpleNamespace(values=values, confidence=conf)


def test_run_reports_per_subject_and_survives_errors():
    items = [i for i in mt.ITEMS if i.subject in ("Химия", "Английский язык")]

    def solve(task, log):
        item = items[task.position - 1]
        if item.subject == "Химия":
            if item.level == 3:
                raise RuntimeError("таймаут API")
            return answer({"a": "неверно"}, 0.8)
        return answer({"a": item.correct["a"][0]}, 0.95)

    seen = []
    rep = mt.run(solve, model="m", items=items, on_item=lambda d, t, r: seen.append(d))
    assert seen == list(range(1, 7))
    stats = {s.subject: s for s in rep.by_subject()}
    assert stats["Английский язык"].correct == 3 and stats["Химия"].correct == 0
    assert rep.correct == 3 and round(rep.percent) == 50
    assert any(r.error == "таймаут API" for r in rep.items)
    ok_c, bad_c = rep.calibration()
    assert ok_c == pytest.approx(0.95) and bad_c == pytest.approx(0.8)
    again = mt.Report.from_dict(rep.to_dict())
    assert again.correct == rep.correct and again.model == "m"


def test_fatal_error_stops_the_test():
    from yaklass_bot.solver import FatalSolveError

    def solve(task, log):
        raise FatalSolveError("неверный ключ")
    with pytest.raises(FatalSolveError):
        mt.run(solve, items=mt.ITEMS[:2])


def test_stop_event_ends_early():
    import threading
    stop = threading.Event()

    def solve(task, log):
        stop.set()
        return answer({"a": "x"})
    rep = mt.run(solve, items=mt.ITEMS[:5], stop=stop)
    assert len(rep.items) == 1 and rep.stopped


def test_only_own_api_mode_can_run_it(tmp_path):
    from yaklass_app.agent import Agent
    from yaklass_app.settings import Store

    store = Store(tmp_path, use_keyring=False)
    agent = Agent(store, backend=None, directory=tmp_path)
    s = store.load()
    s.solver_mode = "server"
    store.save(s)
    ok, why = agent.start_model_test()
    assert not ok and "Свой API" in why
