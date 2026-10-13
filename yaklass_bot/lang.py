"""Определение языка задания: задания по иностранному языку решаются иначе, чем по математике или истории."""
from __future__ import annotations

import re

from .models import Task

_FIELD = re.compile(r"\[\[[^\]]*\]\]|\[IMG:[^\]]*\]")
_LATIN = re.compile(r"[A-Za-z]")
_CYR = re.compile(r"[А-Яа-яЁё]")


def latin_share(text: str) -> float:
    """Доля латинских букв среди всех букв (без маркеров полей и картинок)."""
    t = _FIELD.sub(" ", text)
    lat, cyr = len(_LATIN.findall(t)), len(_CYR.findall(t))
    return lat / (lat + cyr) if lat + cyr else 0.0


def is_english(task: Task) -> bool:
    """Условие и варианты — в основном английский текст (инструкция может быть и по-русски)."""
    parts = [task.text] + [o.text for f in task.fields for o in f.options if o.text]
    text = "\n".join(parts)
    letters = len(_LATIN.findall(_FIELD.sub(" ", text))) + len(_CYR.findall(_FIELD.sub(" ", text)))
    return letters >= 12 and latin_share(text) >= 0.6
