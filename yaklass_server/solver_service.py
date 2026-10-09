"""Решение задания на сервере: общий ключ модели, поиск, безопасная загрузка картинок."""
from __future__ import annotations

from urllib.parse import urlparse

from yaklass_bot.config import Config
from yaklass_bot.models import Task
from yaklass_bot.pipeline import solve as pipeline_solve
from yaklass_bot.solver.api import ApiProvider
from yaklass_bot.solver.base import Answer, Provider
from yaklass_bot.solver.images import Image, download
from yaklass_bot.solver.research import _is_public

from .settings import Settings


def image_allowed(url: str, hosts: tuple[str, ...]) -> bool:
    """Сервер скачивает только https с доменов из белого списка и публичных адресов
    (иначе клиент мог бы заставить сервер ходить во внутреннюю сеть — SSRF)."""
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host:
        return False
    if not any(host == h or host.endswith("." + h) for h in hosts):
        return False
    return _is_public(host)


class SolverService:
    def __init__(self, s: Settings, provider: Provider | None = None):
        self.s = s
        self.provider = provider or ApiProvider({
            "base_url": s.llm_base_url, "model": s.llm_model, "api_key": s.llm_api_key, "api_key_env": "",
            "vision": s.llm_vision, "json_mode": s.llm_json_mode, "temperature": 0.0})
        self.cfg = Config()
        self.cfg.searxng_url = s.searxng_url
        self.cfg.search_mode = s.search_mode

    def _load_image(self, url: str) -> Image | None:
        return download(url, allow_redirects=False) if image_allowed(url, self.s.image_hosts) else None

    def solve(self, task: Task) -> Answer:
        return pipeline_solve(self.cfg, self.provider, task, log=lambda m: None, image_loader=self._load_image)
