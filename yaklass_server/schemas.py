"""Контракт API между агентом (ПК пользователя) и сервером. Все размеры ограничены."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from yaklass_bot.models import Field as TaskField
from yaklass_bot.models import Option as TaskOption
from yaklass_bot.models import Task

Kind = Literal["dropdown", "text", "radio", "checkbox", "dnd"]


class OptionIn(BaseModel):
    value: str = Field(max_length=200)
    text: str = Field(default="", max_length=1000)
    image: str = Field(default="", max_length=600)


class FieldIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    kind: Kind
    options: list[OptionIn] = Field(default_factory=list, max_length=60)
    max_points: float | None = None
    size: int | None = None


class TaskIn(BaseModel):
    position: int = 0
    title: str = Field(default="", max_length=300)
    points: float | None = None
    text: str = Field(max_length=20_000)
    fields: list[FieldIn] = Field(min_length=1, max_length=60)
    images: list[str] = Field(default_factory=list, max_length=12)

    def to_task(self) -> Task:
        return Task(
            position=self.position, title=self.title, points=self.points, text=self.text,
            fields=[TaskField(name=f.name, kind=f.kind, max_points=f.max_points, size=f.size,
                              options=[TaskOption(o.value, o.text, o.image) for o in f.options])
                    for f in self.fields],
            images=[u[:600] for u in self.images])


class SolveRequest(BaseModel):
    task: TaskIn


class SolveResponse(BaseModel):
    answers: dict[str, str]
    confidence: float
    evidence: str = ""
    remaining_today: int


class LinkRequest(BaseModel):
    code: str = Field(max_length=32)
    device_name: str = Field(default="", max_length=60)


class LinkResponse(BaseModel):
    token: str
    username: str = ""


class MeResponse(BaseModel):
    tg_id: int
    username: str
    quota_per_day: int
    used_today: int
    min_task_interval_s: int
    agent_online: bool
