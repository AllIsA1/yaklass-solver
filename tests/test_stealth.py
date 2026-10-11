"""Режимы выполнения: выбор оценки, расписание времени, намеренные ошибки (без браузера)."""
import random

import pytest

from yaklass_bot import stealth
from yaklass_bot.models import Field, Option, Task
from yaklass_bot.stealth import (GRADE_BANDS, LIMIT_MARGIN_S, Planner, RunStyle, Schedule, corrupt, parse_minutes,
                                 pick_percent, target_seconds)


def opts(*vals):
    return [Option(v, v.upper()) for v in vals]


def task(n_fields=1, points=1.0, kind="dropdown", position=1):
    fields = [Field(f"f{i}", kind, options=opts("a", "b", "c"), max_points=points / n_fields) for i in range(n_fields)]
    return Task(position=position, title="t", points=points, text="", fields=fields, images=[])


def simulate(grade, n_tasks, seed, fields=1, points=2.0, total_known=True, allow_skip=True):
    rng = random.Random(seed)
    p = Planner(grade, n_tasks * points if total_known else None, rng, allow_skip=allow_skip)
    for i in range(n_tasks):
        t = task(fields, points, position=i + 1)
        d = p.decide(t, n_tasks - i - 1)
        if d.kind == "skip":
            p.commit(d, None)
        else:
            values = {f.name: "a" for f in t.fields}
            wrong = corrupt(t, values, d.wrong, rng) if d.wrong else set()
            p.commit(d, wrong)
    return p


# ---------- оценка ----------

@pytest.mark.parametrize("grade", [3, 4, 5])
@pytest.mark.parametrize("fields", [1, 4])
def test_expected_percent_lands_in_grade_band(grade, fields):
    lo, hi = GRADE_BANDS[grade]
    for seed in range(40):
        p = simulate(grade, 20, seed, fields=fields)
        assert lo - 2 <= p.expected_percent <= hi + 0.01, (grade, seed, p.expected_percent)


def test_band_reached_without_known_total():
    lo, hi = GRADE_BANDS[4]
    got = [simulate(4, 20, s, total_known=False).expected_percent for s in range(30)]
    assert sum(lo - 4 <= g <= hi + 0.01 for g in got) >= 27


def test_mix_of_outcomes_for_middle_grade():
    kinds = set()
    for seed in range(30):
        rng = random.Random(seed)
        p = Planner(3, 40.0, rng)
        for i in range(20):
            d = p.decide(task(3, 2.0, position=i), 19 - i)
            kinds.add(d.kind)
            p.commit(d, None if d.kind == "skip" else set(d.wrong))
    assert {"full", "partial", "wrong", "skip"} <= kinds


def test_percent_is_inside_band_with_margins():
    rng = random.Random(1)
    for g, (lo, hi) in GRADE_BANDS.items():
        for _ in range(200):
            assert lo < pick_percent(g, rng) < hi


# ---------- время ----------

def test_round_minutes_are_blurred_within_57s():
    rng = random.Random(3)
    vals = [target_seconds(7, rng, None)[0] for _ in range(300)]
    assert all(420 - 57 <= v <= 420 + 57 for v in vals) and len({round(v) for v in vals}) > 50
    assert min(vals) < 400 and max(vals) > 440                       # разброс реальный, а не нулевой


def test_not_round_time_is_kept_exact():
    rng = random.Random(3)
    assert {target_seconds(7.5, rng, None)[0] for _ in range(20)} == {450.0}


def test_time_never_exceeds_work_limit():
    rng = random.Random(5)
    for left in (600, 420, 200, 90):
        for _ in range(100):
            sec, note = target_seconds(10, rng, left)               # просят 10 мин ±57 с
            assert sec <= max(left - LIMIT_MARGIN_S, 0.6 * left) + 1e-9 and sec <= left
    sec, note = target_seconds(30, rng, 900)
    assert sec == 900 - LIMIT_MARGIN_S and "не помещается" in note
    sec, note = target_seconds(5, rng, 3600)
    assert note == "" and 5 * 60 - 57 <= sec <= 5 * 60 + 57


def test_schedule_gives_slices_that_sum_to_budget():
    now = [0.0]
    sch = Schedule(310.0, random.Random(1), clock=lambda: now[0])    # 300 с на задания (10 с — на завершение)
    for i in range(10):
        s = sch.slice(1.0, 10 - i)
        assert 0 <= s <= sch.left()
        now[0] += s
    assert now[0] <= 300.0 + 1e-6
    assert now[0] > 200                                              # уложились в бюджет, а не «убежали» вперёд


def test_parse_minutes_and_style():
    assert parse_minutes("7") == 7 and parse_minutes("7,5") == 7.5 and parse_minutes("7:30") == 7.5
    with pytest.raises(ValueError):
        parse_minutes("скоро")
    assert RunStyle.parse({"style": "stealth", "minutes": 7, "grade": 4}) == RunStyle("stealth", 7.0, 4)
    assert RunStyle.parse({}).kind == "normal" and RunStyle.parse({"style": "fast"}).kind == "fast"
    for bad in ({"style": "x"}, {"style": "stealth"}, {"style": "stealth", "minutes": 7, "grade": 2},
                {"style": "stealth", "minutes": 0, "grade": 4}, {"style": "stealth", "minutes": 999, "grade": 4},
                {"style": "stealth", "minutes": "abc", "grade": 4}):
        with pytest.raises(ValueError):
            RunStyle.parse(bad)


# ---------- намеренные ошибки ----------

def test_corrupt_each_field_kind_changes_value():
    rng = random.Random(7)
    t = Task(position=1, title="t", points=5, text="", images=[], fields=[
        Field("dd", "dropdown", options=opts("a", "b", "c")),
        Field("rb", "radio", options=opts("x", "y")),
        Field("cb", "checkbox", options=opts("p", "q", "r")),
        Field("n", "text"), Field("w", "text"), Field("d", "text")])
    for _ in range(50):
        v = {"dd": "a", "rb": "x", "cb": "p,q", "n": "42", "w": "Москва", "d": "3,5"}
        wrong = corrupt(t, v, {"dd", "rb", "cb", "n", "w", "d"}, rng)
        assert wrong == {"dd", "rb", "cb", "n", "w", "d"}
        assert v["dd"] in ("b", "c") and v["rb"] == "y" and v["cb"] != "p,q" and v["cb"]
        assert v["n"] != "42" and v["w"].lower() != "москва" and v["d"] != "3,5" and "," in v["d"]


def test_corrupt_touches_only_requested_fields():
    rng = random.Random(1)
    t = task(3)
    v = {"f0": "a", "f1": "a", "f2": "a"}
    assert corrupt(t, v, {"f1"}, rng) == {"f1"} and v["f0"] == v["f2"] == "a" and v["f1"] != "a"


def test_corrupt_dnd_keeps_each_option_once():
    rng = random.Random(2)
    t = Task(position=1, title="t", points=3, text="", images=[],
             fields=[Field(f"s{i}", "dnd", options=opts("1", "2", "3")) for i in range(3)])
    for _ in range(30):
        v = {"s0": "1", "s1": "2", "s2": "3"}
        wrong = corrupt(t, v, {"s0"}, rng)                           # одно поле — потянет соседнее
        assert sorted(v.values()) == ["1", "2", "3"] and len(wrong) >= 2 and "s0" in wrong


def test_corrupt_dnd_with_spare_options_uses_unused_one():
    rng = random.Random(2)
    t = Task(position=1, title="t", points=1, text="", images=[],
             fields=[Field("s0", "dnd", options=opts("1", "2", "3"))])
    v = {"s0": "1"}
    assert corrupt(t, v, {"s0"}, rng) == {"s0"} and v["s0"] in ("2", "3")


def test_single_option_field_cannot_be_corrupted_and_is_reported():
    t = Task(position=1, title="t", points=1, text="", images=[], fields=[Field("f", "dropdown", options=opts("a"))])
    v = {"f": "a"}
    assert corrupt(t, v, {"f"}, random.Random(0)) == set() and v == {"f": "a"}      # честно: не изменилось


def test_unsupported_task_counts_in_total_but_not_earned():
    p = Planner(4, 10.0, random.Random(1))
    p.observe(task(1, 4.0))
    assert p.earned == 0 and p.seen == 4.0 and p.expected_percent == 0.0
