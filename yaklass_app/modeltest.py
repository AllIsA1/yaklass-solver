"""Встроенный тест модели (только режим «Свой API»): по 3 задания на каждый предмет ЯКласса — от простого
к сложному. Задания решаются тем же путём, что и на сайте (тот же промпт, разбор ответа, поиск — если включён),
ответы сверяются с правильными. Итог: где модель ошиблась, насколько была уверена, процент верных по предметам.

Предметы — основные предметы каталога ЯКласса (1–11 класс). Задания составлены так, чтобы ответ был однозначным
и проверялся автоматически: число, короткое слово или выбор варианта."""
from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from yaklass_bot.models import Field, Option, Task

LEVELS = {1: "простое", 2: "среднее", 3: "сложное"}


@dataclass
class BenchItem:
    subject: str
    level: int
    text: str                                  # условие с [[a]], [[b]]... на месте полей
    fields: list[Field]
    correct: dict[str, list[str]]              # поле -> допустимые ответы (для выбора — value вариантов)

    def task(self, position: int) -> Task:
        return Task(position=position, title=f"{self.subject}: {LEVELS[self.level]}", points=1.0, text=self.text,
                    fields=self.fields, images=[])

    def correct_text(self) -> str:
        """Правильный ответ так, как его видит человек."""
        parts = []
        for f in self.fields:
            want = self.correct[f.name]
            if f.options:
                by = {o.value: o.text for o in f.options}
                parts.append(", ".join(by.get(v, v) for v in want[0].split(",")))
            else:
                parts.append(want[0])
        return "; ".join(parts)


# ---------------------------------------------------------------- конструкторы заданий
def text(subject: str, level: int, q: str, *accepted: str) -> BenchItem:
    """Поле — на месте пропуска «___», а если его нет — в конце условия."""
    body = q.replace("___", "[[a]]", 1) if "___" in q else f"{q} [[a]]"
    return BenchItem(subject, level, body, [Field("a", "text")], {"a": list(accepted)})


def choice(subject: str, level: int, q: str, options: list[str], right: int) -> BenchItem:
    opts = [Option(str(i + 1), t) for i, t in enumerate(options)]
    return BenchItem(subject, level, f"{q}\n[[a]]", [Field("a", "radio", options=opts)], {"a": [str(right + 1)]})


def multi(subject: str, level: int, q: str, options: list[str], right: list[int]) -> BenchItem:
    opts = [Option(str(i + 1), t) for i, t in enumerate(options)]
    return BenchItem(subject, level, f"{q}\n[[a]]", [Field("a", "checkbox", options=opts)],
                     {"a": [",".join(str(i + 1) for i in sorted(right))]})


# ---------------------------------------------------------------- задания: предмет -> 3 уровня
ITEMS: list[BenchItem] = [
    # Математика (1–6 класс)
    text("Математика", 1, "Вычисли: 7 · 8 =", "56"),
    text("Математика", 2, "Найди значение выражения 3/4 + 1/8. Ответ запиши десятичной дробью:", "0,875", "7/8"),
    text("Математика", 3, "Скорость катера по течению реки 22 км/ч, а против течения 18 км/ч. "
                          "Найди скорость течения реки (км/ч):", "2"),
    # Алгебра
    text("Алгебра", 1, "Реши уравнение 3x − 7 = 11. x =", "6"),
    text("Алгебра", 2, "Найди больший корень уравнения x² − 5x + 6 = 0:", "3"),
    text("Алгебра", 3, "Арифметическая прогрессия: a₁ = 2, d = 3. Найди сумму первых десяти её членов S₁₀ =", "155"),
    # Геометрия
    text("Геометрия", 1, "Два угла треугольника равны 50° и 60°. Найди третий угол (в градусах):", "70"),
    text("Геометрия", 2, "Катеты прямоугольного треугольника равны 6 и 8. Найди гипотенузу:", "10"),
    text("Геометрия", 3, "Диагонали ромба равны 10 и 24. Найди сторону ромба:", "13"),
    # Русский язык
    choice("Русский язык", 1, "Выбери правильное написание слова.", ["жыраф", "жираф", "жераф"], 1),
    text("Русский язык", 2, "Сколько грамматических основ в предложении «Когда солнце село, стало прохладно, "
                            "и мы пошли домой»? Ответ запиши цифрой:", "3"),
    multi("Русский язык", 3, "Отметь предложения, в которых НЕ со словом в скобках пишется СЛИТНО.",
          ["(Не)навидеть ложь — естественно.", "Это (не)правда, а вымысел.", "Перед нами (не)высокий дом.",
           "На столе лежала ещё (не)решённая задача."], [0, 2]),
    # Литература
    choice("Литература", 1, "Кто автор романа в стихах «Евгений Онегин»?",
           ["М. Ю. Лермонтов", "А. С. Пушкин", "Н. В. Гоголь", "И. С. Тургенев"], 1),
    text("Литература", 2, "Назови фамилию главного героя романа Ф. М. Достоевского «Преступление и наказание»:",
         "Раскольников"),
    choice("Литература", 3, "Каким стихотворным размером написан «Евгений Онегин»?",
           ["четырёхстопный ямб", "четырёхстопный хорей", "трёхстопный дактиль", "пятистопный анапест"], 0),
    # Английский язык
    text("Английский язык", 1, "Put the verb in the correct form: She (to be) ___ a teacher.", "is"),
    text("Английский язык", 2, "Put the verb in the correct form: Yesterday I (go) ___ to the cinema.", "went"),
    text("Английский язык", 3, "Put the verb in the correct form: If I (know) ___ his address, I would write to him.",
         "knew"),
    # Немецкий язык
    text("Немецкий язык", 1, "Вставь глагол sein в нужной форме: Ich ___ Schüler.", "bin"),
    choice("Немецкий язык", 2, "Выбери артикль существительного «Mädchen».", ["der", "die", "das"], 2),
    text("Немецкий язык", 3, "Вставь вспомогательный глагол (Perfekt): Gestern ___ ich ins Kino gegangen.", "bin"),
    # Физика
    text("Физика", 1, "Тело движется равномерно со скоростью 5 м/с в течение 10 с. Найди пройденный путь (м):", "50"),
    text("Физика", 2, "Найди силу тяжести, действующую на тело массой 3 кг (g = 10 Н/кг), в ньютонах:", "30"),
    text("Физика", 3, "Резисторы 6 Ом и 3 Ом соединены параллельно. Найди их общее сопротивление (Ом):", "2"),
    # Химия
    text("Химия", 1, "Запиши химический знак натрия:", "Na"),
    text("Химия", 2, "Найди относительную молекулярную массу воды H₂O, если Ar(H) = 1, Ar(O) = 16:", "18"),
    text("Химия", 3, "В 180 г воды растворили 20 г соли. Найди массовую долю соли в растворе (в процентах):", "10"),
    # Биология
    choice("Биология", 1, "В каком органоиде клетки происходит фотосинтез?",
           ["митохондрия", "рибосома", "хлоропласт", "ядро"], 2),
    text("Биология", 2, "Сколько камер в сердце человека? Ответ запиши цифрой:", "4"),
    text("Биология", 3, "Скрещивают две гетерозиготные особи (Aa × Aa) при полном доминировании. Какая доля потомства "
                        "(в процентах) будет иметь рецессивный признак?", "25"),
    # География
    text("География", 1, "Назови столицу Австралии:", "Канберра"),
    choice("География", 2, "Какой океан самый большой по площади?",
           ["Атлантический", "Индийский", "Тихий", "Северный Ледовитый"], 2),
    text("География", 3, "Масштаб карты 1 : 50 000. Расстояние между пунктами на карте 4 см. "
                         "Каково расстояние на местности (км)?", "2"),
    # История России
    text("История России", 1, "В каком году произошло Крещение Руси?", "988"),
    choice("История России", 2, "Кто стал первым царём из династии Романовых?",
           ["Пётр I", "Алексей Михайлович", "Михаил Фёдорович", "Фёдор Алексеевич"], 2),
    text("История России", 3, "Как называлось высшее законосовещательное учреждение Российской империи, учреждённое "
                              "Александром I в 1810 году (по проекту М. М. Сперанского)?", "Государственный совет"),
    # Всеобщая история
    text("Всеобщая история", 1, "В каком году экспедиция Колумба достигла Америки?", "1492"),
    choice("Всеобщая история", 2, "Какое событие принято считать началом Великой французской революции?",
           ["казнь Людовика XVI", "взятие Бастилии", "битва при Ватерлоо", "провозглашение империи"], 1),
    text("Всеобщая история", 3, "В каком году был заключён Вестфальский мир, завершивший Тридцатилетнюю войну?",
         "1648"),
    # Обществознание
    choice("Обществознание", 1, "Как называется основной закон Российской Федерации?",
           ["Гражданский кодекс", "Конституция", "Уголовный кодекс", "Федеральный закон"], 1),
    choice("Обществознание", 2, "С какого возраста по общему правилу наступает полная дееспособность гражданина РФ?",
           ["14 лет", "16 лет", "18 лет", "21 год"], 2),
    choice("Обществознание", 3, "Какая экономическая система основана на частной собственности, свободе "
                                "предпринимательства и конкуренции?",
           ["традиционная", "командная", "рыночная", "натуральная"], 2),
    # Информатика
    text("Информатика", 1, "Сколько битов в одном байте?", "8"),
    text("Информатика", 2, "Запиши число 13 в двоичной системе счисления:", "1101"),
    text("Информатика", 3, "Алфавит содержит 32 символа. Каков информационный объём сообщения из 64 символов (в битах)?",
         "320"),
    # Окружающий мир
    choice("Окружающий мир", 1, "Сколько дней в високосном году?", ["364", "365", "366"], 2),
    text("Окружающий мир", 2, "Какая планета Солнечной системы находится ближе всех к Солнцу?", "Меркурий"),
    choice("Окружающий мир", 3, "Для какой природной зоны характерны вечная мерзлота, мхи, лишайники и карликовые берёзы?",
           ["тайга", "степь", "тундра", "пустыня"], 2),
    # ОБЗР (ОБЖ)
    text("ОБЗР", 1, "Какой единый номер вызова экстренных служб в России?", "112"),
    choice("ОБЗР", 2, "Что нужно сделать в первую очередь при небольшом ожоге кипятком?",
           ["смазать маслом", "охладить место ожога прохладной проточной водой", "проколоть пузырь",
            "приложить снег на час"], 1),
    choice("ОБЗР", 3, "Каково соотношение компрессий грудной клетки и вдохов при сердечно-лёгочной реанимации "
                      "взрослого одним спасателем?", ["15 : 1", "30 : 2", "5 : 1", "10 : 2"], 1),
]

SUBJECTS = list(dict.fromkeys(i.subject for i in ITEMS))


# ---------------------------------------------------------------- проверка
def norm(s: str) -> str:
    t = str(s or "").strip().lower().replace("ё", "е")
    t = re.sub(r"\s+", " ", t)
    return t.strip(" .;:!«»\"'")


def _number(s: str) -> float | None:
    t = norm(s).replace(" ", "").replace(",", ".")
    m = re.fullmatch(r"([-+]?\d+(?:\.\d+)?)(?:/(\d+))?", t)
    if not m:
        return None
    v = float(m.group(1))
    if m.group(2):
        d = float(m.group(2))
        if d == 0:
            return None
        v /= d
    return v


def field_ok(f: Field, got: str, accepted: list[str]) -> bool:
    if f.kind == "checkbox":
        have = {v.strip() for v in str(got or "").split(",") if v.strip()}
        return any(have == {v for v in a.split(",") if v} for a in accepted)
    if f.options:
        return str(got or "").strip() in accepted
    g = norm(got)
    for a in accepted:
        if g == norm(a):
            return True
        x, y = _number(got), _number(a)
        if x is not None and y is not None and abs(x - y) < 1e-6:
            return True
    return False


def shown_answer(f: Field, got: str) -> str:
    if not got:
        return "—"
    if f.options:
        by = {o.value: o.text for o in f.options}
        return ", ".join(by.get(v.strip(), v.strip()) for v in str(got).split(",") if v.strip())
    return str(got)


@dataclass
class ItemResult:
    subject: str
    level: int
    question: str
    correct: bool
    score: float                        # доля верных полей (0..1)
    confidence: float | None
    answer: str                         # ответ модели (как увидел бы человек)
    expected: str
    error: str = ""                     # модель не ответила (ошибка API и т.п.)
    seconds: float = 0.0


def check(item: BenchItem, values: dict[str, str]) -> tuple[float, str]:
    oks = [field_ok(f, values.get(f.name, ""), item.correct[f.name]) for f in item.fields]
    shown = "; ".join(shown_answer(f, values.get(f.name, "")) for f in item.fields)
    return (sum(oks) / len(oks) if oks else 0.0), shown


@dataclass
class SubjectStat:
    subject: str
    correct: int
    total: int
    avg_confidence: float | None

    @property
    def percent(self) -> float:
        return 100.0 * self.correct / self.total if self.total else 0.0


@dataclass
class Report:
    model: str
    started: float
    items: list[ItemResult] = field(default_factory=list)
    stopped: bool = False

    @property
    def correct(self) -> int:
        return sum(1 for r in self.items if r.correct)

    @property
    def percent(self) -> float:
        return 100.0 * self.correct / len(self.items) if self.items else 0.0

    @property
    def avg_confidence(self) -> float | None:
        c = [r.confidence for r in self.items if r.confidence is not None]
        return sum(c) / len(c) if c else None

    def by_subject(self) -> list[SubjectStat]:
        out = []
        for s in dict.fromkeys(r.subject for r in self.items):
            rs = [r for r in self.items if r.subject == s]
            c = [r.confidence for r in rs if r.confidence is not None]
            out.append(SubjectStat(s, sum(1 for r in rs if r.correct), len(rs), sum(c) / len(c) if c else None))
        return out

    def calibration(self) -> tuple[float | None, float | None]:
        """Средняя уверенность в верных и в неверных ответах: хорошая модель сомневается, когда ошибается."""
        def avg(xs):
            xs = [x for x in xs if x is not None]
            return sum(xs) / len(xs) if xs else None
        return (avg(r.confidence for r in self.items if r.correct),
                avg(r.confidence for r in self.items if not r.correct and not r.error))

    def to_dict(self) -> dict:
        return {"model": self.model, "started": self.started, "stopped": self.stopped,
                "items": [asdict(r) for r in self.items]}

    @classmethod
    def from_dict(cls, d: dict) -> "Report":
        r = cls(model=str(d.get("model", "")), started=float(d.get("started", 0)), stopped=bool(d.get("stopped")))
        for x in d.get("items") or []:
            try:
                r.items.append(ItemResult(**x))
            except TypeError:
                continue
        return r


# ---------------------------------------------------------------- прогон
def run(solve: Callable[[Task, Callable[[str], None]], object], *, model: str = "",
        items: list[BenchItem] | None = None, stop: threading.Event | None = None,
        on_item: Callable[[int, int, ItemResult], None] | None = None,
        log: Callable[[str], None] = lambda m: None) -> Report:
    """solve(task, log) -> Answer (values, confidence). Ошибки отдельных заданий не прерывают тест,
    кроме «фатальных» (нет ключа, исчерпан баланс) — они пробрасываются."""
    from yaklass_bot.solver import FatalSolveError

    items = items if items is not None else ITEMS
    rep = Report(model=model, started=time.time())
    for i, item in enumerate(items):
        if stop is not None and stop.is_set():
            rep.stopped = True
            break
        t0 = time.time()
        try:
            ans = solve(item.task(i + 1), log)
            score, shown = check(item, dict(getattr(ans, "values", {}) or {}))
            conf = getattr(ans, "confidence", None)
            res = ItemResult(item.subject, item.level, item.text.replace("[[a]]", "___").strip(), score >= 1.0, score,
                             float(conf) if isinstance(conf, (int, float)) else None, shown, item.correct_text(),
                             seconds=round(time.time() - t0, 1))
        except FatalSolveError:
            raise
        except Exception as e:  # noqa: BLE001 — одно задание не должно ронять тест
            res = ItemResult(item.subject, item.level, item.text.replace("[[a]]", "___").strip(), False, 0.0, None,
                             "—", item.correct_text(), error=str(e)[:200], seconds=round(time.time() - t0, 1))
        rep.items.append(res)
        if on_item:
            on_item(i + 1, len(items), res)
    return rep


def save(rep: Report, directory: Path) -> Path:
    p = directory / "model-test.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rep.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load(directory: Path) -> Report | None:
    try:
        return Report.from_dict(json.loads((directory / "model-test.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
