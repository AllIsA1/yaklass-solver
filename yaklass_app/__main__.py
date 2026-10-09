"""Запуск приложения: python -m yaklass_app"""
from __future__ import annotations

import sys


def selftest(browser: bool = False) -> int:
    """Проверка собранного приложения: окно создаётся, Playwright-драйвер на месте. Для CI и отладки сборки."""
    import os
    import tempfile
    from pathlib import Path

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from .agent import Agent
    from .backend import PlaywrightBackend
    from .gui.main_window import MainWindow
    from .gui.widgets import Bridge
    from .settings import Store

    app = QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp())
    store, bridge = Store(tmp, use_keyring=False), Bridge()
    backend = PlaywrightBackend(store, tmp)
    agent = Agent(store, backend, emit=lambda n, p: bridge.event.emit(n, p), directory=tmp)
    win = MainWindow(agent, store, bridge)
    win.show()
    app.processEvents()
    from playwright._impl._driver import compute_driver_executable
    node, cli = compute_driver_executable()
    ok = Path(node).exists() and Path(cli).exists() and win.grab().width() > 100
    msg = ""
    if browser and ok:                       # реальный запуск браузера тем же путём, что и при работе
        try:
            ctx, closer = backend._open(headless=True)
            page = ctx.new_page()
            page.set_content("<p id=x>ok</p>")
            ok = page.inner_text("#x") == "ok"
            closer()
            msg = f" | браузер: {backend.last_browser}"
        except Exception as e:  # noqa: BLE001
            ok, msg = False, f" | браузер НЕ запустился: {e}"
    agent.shutdown()
    print("selftest:", "OK" if ok else "FAIL", "| playwright driver:", node + msg)
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv or "--selftest-browser" in sys.argv:
        return selftest(browser="--selftest-browser" in sys.argv)
    from .backend import ensure_browsers_path
    ensure_browsers_path()
    from PySide6.QtWidgets import QApplication

    from .agent import Agent
    from .backend import PlaywrightBackend
    from .gui.main_window import MainWindow
    from .gui.widgets import Bridge
    from .settings import Store

    app = QApplication(sys.argv)
    app.setApplicationName("Yaklass Solver")
    from pathlib import Path

    from PySide6.QtGui import QIcon
    icon = Path(__file__).parent / "assets" / "icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    store = Store()
    bridge = Bridge()
    agent = Agent(store, PlaywrightBackend(store), emit=lambda name, payload: bridge.event.emit(name, payload))
    window = MainWindow(agent, store, bridge)
    window.show()
    agent.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
