from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Work:
    """Строка из списка проверочных работ («Новые работы»)."""
    work_id: str
    subject: str
    title: str
    deadline_utc_ms: int | None
    preview_url: str


@dataclass
class WorkInfo:
    """Страница Preview: условия запуска работы. None = на странице этой строки нет."""
    title: str
    time_limit_min: int | None      # None — без ограничения (или не указано)
    tasks_count: int | None
    max_points: int | None
    attempts_left: int | None       # None — число попыток не указано
    can_start: bool = False         # есть кнопка запуска
    start_label: str = ""           # «Начать» / «Продолжить» / ...
    start_action: str = ""          # action формы запуска
    is_continue: bool = False       # «Продолжить прохождение» — работа уже начата, попытка не тратится
    raw: dict[str, str] = field(default_factory=dict)   # все строки блока info как есть

    @property
    def start_allowed(self) -> bool:
        return self.can_start and self.attempts_left != 0


@dataclass
class NavItem:
    """Пункт навигации по заданиям работы (test-ex-nav-v2)."""
    position: int
    title: str
    href: str                      # у текущего задания ссылки нет -> ""
    is_current: bool = False
    is_answered: bool = False      # на задание уже дан ответ (его можно переписать кнопкой «Сохранить»)


@dataclass
class OverviewRow:
    position: int
    title: str
    status: str                    # «Ответ получен» / ...
    answered: bool


@dataclass
class Overview:
    """Страница «Список заданий / Завершение работы»."""
    rows: list[OverviewRow]
    complete_action: str = ""      # action формы завершения (есть, только когда на все задания дан ответ)

    @property
    def can_complete(self) -> bool:
        return bool(self.complete_action)

    @property
    def unanswered(self) -> list[OverviewRow]:
        return [r for r in self.rows if not r.answered]


@dataclass
class Option:
    value: str
    text: str
    image: str = ""                # URL, если вариант — картинка


@dataclass
class Field:
    """Одно поле ввода внутри задания."""
    name: str                      # атрибут name (напр. "e6r1|dd"), по нему заполняем
    kind: str                      # dropdown | text | radio | checkbox | dnd
                                   # checkbox: value опции = name её <input>, ответ — список;
                                   # dnd: value опции = data-id, ответ — одна опция на поле
    options: list[Option] = field(default_factory=list)
    max_points: float | None = None
    size: int | None = None


@dataclass
class Task:
    position: int
    title: str                     # заголовок из навигации («1. Взаимное расположение...»)
    points: float | None
    text: str                      # условие, поля заменены на [[имя]]
    fields: list[Field]
    images: list[str]              # URL картинок (пока игнорируем)
    has_audio: bool = False
    form_action: str = ""
    time_left_s: int | None = None
    is_theory: bool = False        # страница теории: отвечать нечего, просто листаем дальше
    next_url: str = ""

    @property
    def supported(self) -> bool:
        return bool(self.fields) and not self.has_audio and not self.is_theory

    @property
    def image_urls(self) -> list[str]:
        """Все картинки, нужные для решения: иллюстрация + варианты-картинки."""
        urls = list(self.images)
        for f in self.fields:
            urls += [o.image for o in f.options if o.image and o.image not in urls]
        return urls
