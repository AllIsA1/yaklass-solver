"""Файлы диагностики: crash.log и app.log создаются, необработанные ошибки попадают в журнал."""
import faulthandler
import logging
import sys
import threading

from yaklass_app import diagnostics


def test_setup_writes_logs_and_hooks(tmp_path):
    old_hook, old_thread_hook = sys.excepthook, threading.excepthook
    try:
        assert diagnostics.setup(tmp_path / "logs") == tmp_path / "logs"
        assert faulthandler.is_enabled()
        assert "запуск" in (tmp_path / "logs" / "crash.log").read_text(encoding="utf-8")
        try:
            raise ValueError("проверка")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        for h in logging.getLogger("yaklass").handlers:
            h.flush()
        text = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
        assert "необработанная ошибка" in text and "ValueError: проверка" in text
    finally:
        sys.excepthook, threading.excepthook = old_hook, old_thread_hook
        faulthandler.disable()
        log = logging.getLogger("yaklass")
        for h in list(log.handlers):
            h.close()
            log.removeHandler(h)
        if diagnostics._crash_file:
            diagnostics._crash_file.close()


def test_setup_never_raises_on_unwritable_dir(tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    assert diagnostics.setup(f / "sub") is None            # «папка» внутри файла — создать нельзя
