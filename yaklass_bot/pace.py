"""«Кулдауны» действий: насколько быстро бот работает (пауза между заданиями, перед отправкой, скорость набора)."""
from __future__ import annotations

import random
import threading
from dataclasses import asdict, dataclass

from .control import RunControl, sleep

Range = tuple[float, float]


def _rng(v, default: Range) -> Range:
    try:
        lo, hi = float(v[0]), float(v[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return default
    lo = max(0.0, lo)
    return (lo, max(lo, hi))


@dataclass
class Pace:
    between_tasks: Range = (1.0, 3.0)       # пауза после каждого задания, с
    before_submit: Range = (0.5, 1.5)       # пауза перед «Ответить», с
    field_pause: Range = (0.25, 0.9)        # пауза между полями ответа, с
    typing_ms: Range = (40, 110)            # задержка на символ при вводе текста, мс

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "Pace":
        d = d if isinstance(d, dict) else {}
        base = cls()
        return cls(between_tasks=_rng(d.get("between_tasks"), base.between_tasks),
                   before_submit=_rng(d.get("before_submit"), base.before_submit),
                   field_pause=_rng(d.get("field_pause"), base.field_pause),
                   typing_ms=_rng(d.get("typing_ms"), base.typing_ms))

    def with_min_between(self, seconds: float) -> "Pace":
        """Серверная рекомендация: пауза между заданиями не меньше seconds."""
        lo, hi = self.between_tasks
        return Pace(between_tasks=(max(lo, seconds), max(hi, seconds)), before_submit=self.before_submit,
                    field_pause=self.field_pause, typing_ms=self.typing_ms)

    def wait(self, rng: Range, control: RunControl | None = None) -> None:
        sleep(control, random.uniform(*rng))


# Темп текущего потока (filler берёт его отсюда, чтобы не менять сигнатуры функций заполнения)
_local = threading.local()


def set_current(p: Pace | None) -> None:
    _local.pace = p


def current() -> Pace:
    return getattr(_local, "pace", None) or Pace()
