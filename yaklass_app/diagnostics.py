"""Диагностика сбоев: файлы crash.log (аварии на уровне процесса) и app.log (журнал + необработанные ошибки
и сообщения Qt) в папке данных приложения. Нужна, потому что у собранного приложения нет консоли."""
from __future__ import annotations

import faulthandler
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG = logging.getLogger("yaklass")
_crash_file = None


def setup(directory: Path) -> Path | None:
    """Включает запись в файлы. Любая ошибка здесь не должна мешать запуску. -> папка с журналами."""
    global _crash_file
    try:
        directory.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(directory / "app.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s: %(message)s"))
        LOG.setLevel(logging.INFO)
        if not any(isinstance(x, RotatingFileHandler) for x in LOG.handlers):
            LOG.addHandler(h)
        _crash_file = open(directory / "crash.log", "a", buffering=1, encoding="utf-8")
        faulthandler.enable(_crash_file, all_threads=True)     # на падении процесса — стеки всех потоков
        _crash_file.write(f"\n--- запуск {__import__('time').strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    except Exception:  # noqa: BLE001
        return None

    def on_exc(exc_type, exc, tb):
        LOG.error("необработанная ошибка", exc_info=(exc_type, exc, tb))

    sys.excepthook = on_exc
    threading.excepthook = lambda a: LOG.error("ошибка в потоке %s", a.thread.name if a.thread else "?",
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    try:
        from PySide6.QtCore import qInstallMessageHandler

        def qt_message(mode, ctx, msg):
            LOG.warning("Qt: %s", msg)
        qInstallMessageHandler(qt_message)
    except Exception:  # noqa: BLE001
        pass
    LOG.info("запуск, python %s, платформа %s", sys.version.split()[0], sys.platform)
    return directory
