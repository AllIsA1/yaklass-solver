"""Управление выполнением работы из другого потока (GUI / Telegram): пауза, продолжение, стоп."""
from __future__ import annotations

import threading
import time


class Stopped(Exception):
    """Выполнение остановлено пользователем."""


class RunControl:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._run = threading.Event()
        self._run.set()

    def stop(self) -> None:
        self._stop.set()
        self._run.set()                      # разбудить ожидающих паузу

    def pause(self) -> None:
        self._run.clear()

    def resume(self) -> None:
        self._run.set()

    @property
    def paused(self) -> bool:
        return not self._run.is_set() and not self._stop.is_set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def checkpoint(self) -> None:
        """Вызывается между действиями: на паузе ждёт, при остановке бросает Stopped."""
        while True:
            if self._stop.is_set():
                raise Stopped()
            if self._run.wait(0.2):
                return

    def sleep(self, seconds: float) -> None:
        """sleep, который реагирует на паузу/стоп (до 0.2 с задержки)."""
        end = time.monotonic() + seconds
        while True:
            self.checkpoint()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.2, left))


def sleep(control: RunControl | None, seconds: float) -> None:
    (control.sleep if control else time.sleep)(seconds)
