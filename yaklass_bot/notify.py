"""Уведомления. Сейчас — консоль; позже сюда добавится Telegram."""
from __future__ import annotations

import sys
from abc import ABC, abstractmethod


class Notifier(ABC):
    @abstractmethod
    def send(self, text: str) -> None: ...


class ConsoleNotifier(Notifier):
    def send(self, text: str) -> None:
        print(text, file=sys.stdout, flush=True)
