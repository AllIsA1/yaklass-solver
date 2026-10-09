"""Решатели для приложения: серверный (общий API) и локальный (свой API, вызывается прямо с этого ПК)."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict

import requests

from yaklass_bot.config import Config
from yaklass_bot.models import Task
from yaklass_bot.pipeline import solve as pipeline_solve
from yaklass_bot.solver.api import ApiProvider
from yaklass_bot.solver.base import Answer, FatalSolveError, SolveError

from .settings import AppSettings

Log = Callable[[str], None]
TIMEOUT = (10, 200)           # соединение, ответ (модель может думать долго)


class LinkError(RuntimeError):
    pass


def api_url(server_url: str, path: str) -> str:
    return server_url.rstrip("/") + path


def ws_url(server_url: str) -> str:
    u = server_url.rstrip("/")
    return ("wss://" + u[len("https://"):] if u.startswith("https://") else "ws://" + u[len("http://"):]) + "/v1/agent"


def serialize_task(task: Task) -> dict:
    """Task -> тело запроса /v1/solve (см. docs/PROTOCOL.md)."""
    return {"task": {
        "position": task.position, "title": task.title, "points": task.points, "text": task.text,
        "images": list(task.images),
        "fields": [{"name": f.name, "kind": f.kind, "max_points": f.max_points, "size": f.size,
                    "options": [asdict(o) for o in f.options]} for f in task.fields]}}


def link(server_url: str, code: str, device_name: str) -> tuple[str, str]:
    """Обмен кода из бота на токен агента -> (token, username)."""
    try:
        r = requests.post(api_url(server_url, "/v1/link"), json={"code": code, "device_name": device_name}, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise LinkError(f"Сервер недоступен: {e}") from e
    if r.status_code == 429:
        raise LinkError("Слишком много попыток. Подождите минуту.")
    if r.status_code != 200:
        raise LinkError(_detail(r) or f"Ошибка {r.status_code}")
    d = r.json()
    return d["token"], d.get("username", "")


def _detail(r: requests.Response) -> str:
    try:
        d = r.json().get("detail", "")
        return d if isinstance(d, str) else ""
    except ValueError:
        return ""


def me(server_url: str, token: str) -> dict:
    r = requests.get(api_url(server_url, "/v1/me"), headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
    if r.status_code in (401, 403):
        raise FatalSolveError("Приложение отвязано или доступ закрыт — привяжите заново (/link в боте).")
    r.raise_for_status()
    return r.json()


class RemoteSolver:
    """POST /v1/solve на сервер."""

    def __init__(self, server_url: str, token: str, sleep: Callable[[float], None] = time.sleep):
        self.url, self.token, self._sleep = server_url, token, sleep
        self.remaining: int | None = None

    def solve(self, task: Task, log: Log = lambda m: None) -> Answer:
        last = ""
        for attempt in range(3):
            try:
                r = requests.post(api_url(self.url, "/v1/solve"), json=serialize_task(task),
                                  headers={"Authorization": f"Bearer {self.token}"}, timeout=TIMEOUT)
            except requests.RequestException as e:
                last = f"сервер недоступен ({e.__class__.__name__})"
                self._sleep(2 * (attempt + 1))
                continue
            if r.status_code == 200:
                d = r.json()
                self.remaining = d.get("remaining_today")
                return Answer(values=d["answers"], confidence=float(d["confidence"]), evidence=d.get("evidence", ""))
            detail = _detail(r)
            if r.status_code in (401, 403):
                raise FatalSolveError("Приложение отвязано или доступ закрыт — привяжите заново (/link в боте).")
            if r.status_code == 429 and "лимит" in detail:
                raise FatalSolveError(detail)
            if r.status_code == 429:                       # слишком часто: подождать и повторить
                wait = min(30, int(r.headers.get("Retry-After", "5")))
                log(f"  сервер просит подождать {wait} с")
                self._sleep(wait)
                last = "слишком частые запросы"
                continue
            if r.status_code in (413, 422):
                raise SolveError(f"сервер отклонил задание: {detail or r.status_code}")
            raise SolveError(detail or f"ошибка сервера {r.status_code}")
        raise FatalSolveError(f"Сервер недоступен: {last}")


class LocalSolver:
    """Свой API: ключ и запросы остаются на этом ПК."""

    def __init__(self, s: AppSettings, api_key: str):
        a = dict(s.own_api)
        a["api_key"], a["api_key_env"] = api_key, ""
        self.provider = ApiProvider(a)
        self.cfg = Config()
        self.cfg.searxng_url = s.searxng_url.rstrip("/")
        self.cfg.search_mode = s.search_mode

    def solve(self, task: Task, log: Log = lambda m: None) -> Answer:
        return pipeline_solve(self.cfg, self.provider, task, log=log)


def make_solver(s: AppSettings, token: str, own_api_key: str):
    if s.solver_mode == "own":
        return LocalSolver(s, own_api_key)
    if not (s.server_url and token):
        raise FatalSolveError("Приложение не привязано к серверу: введите код из бота (/link).")
    return RemoteSolver(s.server_url, token)
