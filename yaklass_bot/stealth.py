"""Режимы выполнения работы: обычный, быстрый и «скрытный».

Скрытный режим выполняет работу за заданное время и «на заданную оценку»: часть заданий решается верно,
часть оставляется без ответа, часть решается намеренно неверно, а часть — частично (верны не все поля).
Оценка — ожидаемая: считается в предположении, что модель отвечает верно (проверить правильность
ответов сайт заранее не даёт)."""
from __future__ import annotations

import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .control import RunControl, sleep
from .models import Field, Task
from .pace import Pace

KINDS = ("normal", "fast", "stealth")
GRADE_BANDS = {3: (53.0, 75.0), 4: (76.0, 90.0), 5: (91.0, 100.0)}    # процент выполнения -> оценка
MIN_MINUTES, MAX_MINUTES = 1.0, 240.0
ROUND_JITTER_S = 57          # «ровное» время (целое число минут) сдвигается не больше чем на столько секунд
LIMIT_MARGIN_S = 75          # запас до конца времени, отведённого на работу (больше разброса ±57 с)
FINISH_RESERVE_S = 10        # на завершение работы после последнего задания
SKIP_SHARE = 0.4             # из заданий «без баллов» такая доля остаётся без ответа, остальные — неверно
PARTIAL_SHARE = 0.35         # из заданий с несколькими полями столько решается частично


# ---------------------------------------------------------------- выбор режима
@dataclass(frozen=True)
class RunStyle:
    kind: str = "normal"                 # normal | fast | stealth
    minutes: float | None = None         # stealth: за сколько минут выполнить работу
    grade: int | None = None             # stealth: на какую оценку (3 / 4 / 5)

    @property
    def stealth(self) -> bool:
        return self.kind == "stealth"

    def problems(self) -> str:
        if self.kind not in KINDS:
            return f"режим: {' / '.join(KINDS)}"
        if self.stealth:
            if self.minutes is None or not MIN_MINUTES <= self.minutes <= MAX_MINUTES:
                return f"время: от {MIN_MINUTES:g} до {MAX_MINUTES:g} минут"
            if self.grade not in GRADE_BANDS:
                return "оценка: 3, 4 или 5"
        return ""

    @classmethod
    def parse(cls, d: dict | None) -> "RunStyle":
        """Из команды/настроек; ValueError с понятным текстом при некорректных значениях."""
        d = d if isinstance(d, dict) else {}
        kind = d.get("style") or d.get("kind") or "normal"
        try:
            minutes = float(d["minutes"]) if d.get("minutes") is not None else None
            grade = int(d["grade"]) if d.get("grade") is not None else None
        except (TypeError, ValueError):
            raise ValueError("минуты — число, оценка — 3, 4 или 5") from None
        s = cls(str(kind), minutes, grade)
        if (why := s.problems()):
            raise ValueError(why)
        return s if s.stealth else cls(s.kind)

    def to_command(self) -> dict:
        return {"style": self.kind, "minutes": self.minutes, "grade": self.grade} if self.stealth else {"style": self.kind}

    def label(self) -> str:
        if self.stealth:
            return f"скрытно: {fmt_minutes(self.minutes)}, оценка {self.grade}"
        return {"fast": "быстро", "normal": "обычно"}[self.kind]


def fmt_minutes(m: float | None) -> str:
    if m is None:
        return "?"
    total = int(round(m * 60))
    mm, ss = divmod(total, 60)
    return f"{mm} мин {ss} с" if ss else f"{mm} мин"


def parse_minutes(text: str) -> float:
    """«7», «7.5», «7,5», «7:30» (минуты:секунды) -> минуты."""
    t = text.strip().replace(",", ".")
    m = re.fullmatch(r"(\d{1,3}):([0-5]?\d)", t)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 60
    try:
        return float(t)
    except ValueError:
        raise ValueError("время — минуты числом (7, 7.5) или минуты:секунды (7:30)") from None


def fast_pace() -> Pace:
    return Pace(between_tasks=(0.2, 0.6), before_submit=(0.1, 0.3), field_pause=(0.05, 0.15), typing_ms=(0, 8))


def stealth_pace(base: Pace) -> Pace:
    """Темп набора — из настроек пользователя; паузы между заданиями задаёт расписание."""
    return Pace(between_tasks=(0.4, 1.2), before_submit=(0.2, 0.6), field_pause=base.field_pause,
                typing_ms=base.typing_ms)


# ---------------------------------------------------------------- время
def target_seconds(minutes: float, rng: random.Random, left_s: float | None) -> tuple[float, str]:
    """Сколько секунд выполнять работу. Целое число минут — «ровное» — размывается на ±57 с.
    Если у работы есть лимит времени (left_s — сколько осталось), цель не выходит за него с запасом."""
    base = minutes * 60.0
    note = ""
    if abs(minutes - round(minutes)) < 1e-9:
        base += rng.uniform(-ROUND_JITTER_S, ROUND_JITTER_S)
    base = max(30.0, base)
    if left_s is not None:
        cap = max(left_s - LIMIT_MARGIN_S, 0.6 * left_s)
        if base > cap:
            base = cap
            note = (f"заданное время не помещается в лимит работы (осталось {int(left_s // 60)} мин "
                    f"{int(left_s % 60)} с) — выполню быстрее: за {fmt_minutes(base / 60)}")
    return base, note


class Schedule:
    """Раздаёт заданиям доли оставшегося времени."""

    def __init__(self, total_s: float, rng: random.Random, clock: Callable[[], float] = time.time):
        self.rng, self.clock = rng, clock
        self.start = clock()
        self.end = self.start + max(10.0, total_s - FINISH_RESERVE_S)

    def left(self) -> float:
        return max(0.0, self.end - self.clock())

    def slice(self, weight: float, remaining_weight: float) -> float:
        left = self.left()
        share = weight / max(weight, remaining_weight, 1e-9)
        return min(left, left * share * self.rng.uniform(0.8, 1.2))


# ---------------------------------------------------------------- оценка
def task_points(task: Task) -> float:
    if task.points:
        return float(task.points)
    s = sum(f.max_points or 0 for f in task.fields)
    return float(s) if s else 1.0


def _field_points(task: Task) -> dict[str, float]:
    """Баллы за каждое поле (масштабированы так, чтобы в сумме давать баллы за задание)."""
    w = task_points(task)
    raw = {f.name: float(f.max_points) if f.max_points else 0.0 for f in task.fields}
    s = sum(raw.values())
    if s <= 0:
        return {n: w / len(task.fields) for n in raw} if task.fields else {}
    return {n: v * w / s for n, v in raw.items()}


def pick_percent(grade: int, rng: random.Random) -> float:
    """Целевой процент внутри диапазона оценки, чуть отступив от краёв (баллы дискретны)."""
    lo, hi = GRADE_BANDS[grade]
    margin = 0.2 * (hi - lo)
    return rng.uniform(lo + margin, hi - margin)


@dataclass
class Decision:
    kind: str                            # full | partial | wrong | skip
    wrong: frozenset[str]                # поля, которые нужно исказить
    points: float                        # максимум за задание
    field_points: dict[str, float] = field(default_factory=dict)


class Planner:
    """Решает по ходу работы, что делать с очередным заданием, чтобы к концу набрать целевой процент.
    Заранее задания не просматриваются: баллы за задание известны, только когда до него дошли."""

    def __init__(self, grade: int, total_points: float | None, rng: random.Random,
                 target_percent: float | None = None, allow_skip: bool = True):
        self.rng, self.allow_skip = rng, allow_skip
        self.target_percent = target_percent if target_percent is not None else pick_percent(grade, rng)
        self.total = float(total_points) if total_points else None
        self.seen = 0.0
        self.count = 0
        self.earned = 0.0

    def observe(self, task: Task) -> float:
        """Задание учтено в сумме баллов, даже если оно не будет решено (нет полей, аудио, ошибка)."""
        w = task_points(task)
        self.seen += w
        self.count += 1
        return w

    def _total(self, after: int) -> float:
        if self.total:
            return max(self.total, self.seen)
        return self.seen + (self.seen / max(self.count, 1)) * max(after, 0)

    @property
    def expected_percent(self) -> float:
        total = max(self.total or 0.0, self.seen)
        return 100.0 * self.earned / total if total else 0.0

    def decide(self, task: Task, after: int) -> Decision:
        w = self.observe(task)
        total = self._total(after)
        remaining = max(0.0, total - self.seen)
        need = total * self.target_percent / 100.0 - self.earned
        x = min(1.0, max(0.0, need / (w + remaining) if w + remaining > 0 else 1.0))
        fp = _field_points(task)
        names = list(fp)
        everything = frozenset(names)
        if x >= 0.97:
            return Decision("full", frozenset(), w, fp)
        if x <= 0.03:
            return self._zero(w, fp, everything)
        if len(names) >= 2 and self.rng.random() < PARTIAL_SHARE:
            order = names[:]
            self.rng.shuffle(order)
            want, cur, right = x * w, 0.0, set()
            for n in order:
                if cur + fp[n] <= want + 0.5 * fp[n]:
                    right.add(n)
                    cur += fp[n]
            if len(right) == len(names):
                return Decision("full", frozenset(), w, fp)
            if not right:
                return self._zero(w, fp, everything)
            return Decision("partial", frozenset(everything - right), w, fp)
        if self.rng.random() < x:                       # в среднем даёт долю x от баллов задания
            return Decision("full", frozenset(), w, fp)
        return self._zero(w, fp, everything)

    def _zero(self, w: float, fp: dict[str, float], everything: frozenset[str]) -> Decision:
        if self.allow_skip and self.rng.random() < SKIP_SHARE:
            return Decision("skip", everything, w, fp)
        return Decision("wrong", everything, w, fp)

    def commit(self, d: Decision, wrong_actual: set[str] | None) -> None:
        """Сколько баллов задание принесёт на самом деле (None — ничего: пропущено или не отправлено)."""
        if wrong_actual is None:
            return
        self.earned += sum(p for n, p in d.field_points.items() if n not in wrong_actual)


# ---------------------------------------------------------------- намеренные ошибки
def _mutate_text(s: str, rng: random.Random) -> str:
    t = s.strip()
    m = re.fullmatch(r"([-+]?\d+)(?:([.,])(\d+))?", t)
    if m:
        sep = m.group(2)
        if sep:
            places = len(m.group(3))
            v = float(f"{m.group(1)}.{m.group(3)}")
            delta = rng.choice([1, -1, 2, 10, 0.1, -0.1])
            return f"{round(v + delta, places):.{places}f}".replace(".", sep)
        return str(int(m.group(1)) + rng.choice([1, -1, 2, -2, 10]))
    cands = []
    if len(t) >= 2:
        i = rng.randrange(len(t) - 1)
        if t[i] != t[i + 1]:
            cands.append(t[:i] + t[i + 1] + t[i] + t[i + 2:])       # перестановка соседних букв
    if len(t) >= 3:
        cands.append(t[:-1])                                        # потеряна последняя буква
    if " " in t:
        words = t.split()
        if len(words) >= 2:
            cands.append(" ".join(words[:-1]))                      # потеряно последнее слово
    for c in cands:
        if c.lower() != t.lower():
            return c
    return (t + "а") if t else "1"


def _other_option(f: Field, current: str, rng: random.Random, exclude: set[str] = frozenset()) -> str | None:
    vals = [o.value for o in f.options if o.value and o.value != current and o.value not in exclude]
    return rng.choice(vals) if vals else None


def corrupt(task: Task, values: dict[str, str], wrong: frozenset[str] | set[str], rng: random.Random) -> set[str]:
    """Искажает ответы `values` (на месте) в полях `wrong`. Возвращает поля, которые действительно изменились
    (перетаскивание «каждый вариант один раз» может потребовать задеть и соседнее поле)."""
    orig = dict(values)
    by_name = {f.name: f for f in task.fields}
    dnd_wrong: list[str] = []
    for n in wrong:
        f = by_name.get(n)
        if f is None or n not in values:
            continue
        cur = values[n]
        if f.kind in ("dropdown", "radio"):
            new = _other_option(f, cur, rng)
            if new is not None:
                values[n] = new
        elif f.kind == "checkbox":
            have = [v for v in cur.split(",") if v]
            allv = [o.value for o in f.options if o.value]
            spare = [v for v in allv if v not in have]
            if have and (len(have) > 1 and rng.random() < 0.5 or not spare):
                have.remove(rng.choice(have))
                if not have and spare:
                    have = [rng.choice(spare)]
            elif spare:
                have.append(rng.choice(spare))
            values[n] = ",".join(have)
        elif f.kind == "dnd":
            dnd_wrong.append(n)
        else:
            values[n] = _mutate_text(cur, rng)
    if dnd_wrong:
        dnd_all = [f.name for f in task.fields if f.kind == "dnd" and f.name in values]
        used = {values[n] for n in dnd_all}
        rotate: list[str] = []
        for n in dnd_wrong:
            free = _other_option(by_name[n], values[n], rng, exclude=used)
            if free is not None:                    # вариантов больше, чем полей — берём неиспользованный
                used.discard(values[n])
                values[n] = free
                used.add(free)
            else:
                rotate.append(n)
        if len(rotate) == 1:                        # менять местами нужно минимум двух
            others = [n for n in dnd_all if n not in rotate]
            if others:
                rotate.append(rng.choice(others))
        if len(rotate) >= 2:
            vals = [values[n] for n in rotate]
            for n, v in zip(rotate, vals[1:] + vals[:1]):
                values[n] = v
    return {n for n in values if values[n] != orig[n]}


# ---------------------------------------------------------------- всё вместе для одного прохода
class Stealth:
    """Состояние скрытного прохода: план оценки + расписание времени. Создаётся, когда известны число заданий
    и оставшееся время (после открытия первого задания)."""

    def __init__(self, style: RunStyle, *, positions: int, total_points: float | None,
                 time_left_s: float | None, rng: random.Random | None = None,
                 clock: Callable[[], float] = time.time, sleeper=None):
        assert style.stealth
        self.rng = rng or random.Random()
        self.style, self.positions, self.clock, self._sleep = style, positions, clock, sleeper
        self.planner = Planner(style.grade, total_points, self.rng)
        total_s, self.time_note = target_seconds(style.minutes, self.rng, time_left_s)
        self.target_s = total_s
        self.schedule = Schedule(total_s, self.rng, clock)
        self.index = 0
        self._wsum, self._wn = 0.0, 0

    # -- позиция в работе
    def begin(self, index: int) -> None:
        self.index = index

    @property
    def after(self) -> int:
        return max(0, self.positions - self.index - 1)

    # -- время
    @staticmethod
    def _weight(task: Task) -> float:
        return 0.5 if task.is_theory else 1.0 + 0.5 * min(len(task.fields), 6)

    def slice_for(self, task: Task) -> float:
        w = self._weight(task)
        if not task.is_theory:
            self._wsum += w
            self._wn += 1
        avg = self._wsum / self._wn if self._wn else 2.0
        s = self.schedule.slice(w, w + self.after * avg)
        if task.time_left_s is not None:                # страница знает точнее: не выходим за лимит работы
            s = min(s, max(0.0, task.time_left_s - LIMIT_MARGIN_S / 2))
        return s

    def sleep(self, seconds: float, control: RunControl | None) -> None:
        if seconds > 0:
            (self._sleep or sleep)(control, seconds)

    def fill_estimate(self, task: Task, values: dict[str, str], pace: Pace) -> float:
        pause = sum(pace.field_pause) / 2
        typed = sum(len(v) for f in task.fields if f.kind == "text" for v in [values.get(f.name, "")])
        return len(task.fields) * (pause + 0.8) + typed * (sum(pace.typing_ms) / 2) / 1000.0
