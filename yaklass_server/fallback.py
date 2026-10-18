"""Запасная модель сервера: если основная недоступна (сеть, 5xx, лимит, баланс, ключ), запросы идут на запасную.

Основная после сбоя «отдыхает» `cooldown_s` секунд (чтобы не ждать таймаут на каждом задании), затем снова пробуется
первой. Ошибки самого запроса (400/413/422) на запасную не переводятся."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence

from yaklass_bot.solver.base import Provider, ProviderUnavailable
from yaklass_bot.solver.images import Image

log = logging.getLogger("yaklass.server")


class FallbackProvider(Provider):
    def __init__(self, primary: Provider, fallback: Provider, cooldown_s: float = 60.0,
                 clock: Callable[[], float] = time.monotonic, names: tuple[str, str] = ("основная", "запасная")):
        self.primary, self.fallback, self.cooldown_s, self.clock, self.names = primary, fallback, cooldown_s, clock, names
        self.supports_images = primary.supports_images
        self.fallback_calls = 0               # сколько запросов обслужила запасная модель
        self.switches = 0                     # сколько раз основная «упала»
        self.last_info = ""
        self._down_until = 0.0
        self._lock = threading.Lock()

    @property
    def primary_down(self) -> bool:
        return self.clock() < self._down_until

    def _mark_down(self, why: Exception) -> None:
        with self._lock:
            if not self.primary_down:
                self.switches += 1
                log.warning("%s модель недоступна (%s) — перехожу на %s на %d с", self.names[0].capitalize(), why,
                            self.names[1], int(self.cooldown_s))
            self._down_until = self.clock() + self.cooldown_s

    def _ask_fallback(self, prompt: str, images: Sequence[Image]) -> str:
        imgs = images if self.fallback.supports_images else ()
        reply = self.fallback.ask(prompt, imgs)
        self.fallback_calls += 1
        self.last_info = getattr(self.fallback, "last_info", "")
        return reply

    def ask(self, prompt: str, images: Sequence[Image] = ()) -> str:
        if self.primary_down:
            try:
                return self._ask_fallback(prompt, images)
            except ProviderUnavailable as e:             # запасная тоже не отвечает — последняя попытка на основной
                log.warning("Запасная модель недоступна (%s) — пробую основную", e)
        try:
            reply = self.primary.ask(prompt, images)
            self.last_info = getattr(self.primary, "last_info", "")
            return reply
        except ProviderUnavailable as e:
            self._mark_down(e)
            return self._ask_fallback(prompt, images)

    def close(self) -> None:
        self.primary.close()
        self.fallback.close()
