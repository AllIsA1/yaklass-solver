"""Запуск приложения: python -m yaklass_app"""
from __future__ import annotations

import sys


def cookie_selftest(tmp) -> str:
    """Импорт кук из Firefox в собранном приложении: поддельный профиль (свежий вход только в журнале WAL, сессионная
    кука в файле сессии lz4). Проверяет, что в сборку попали sqlite, lz4, Cryptodome и browser_cookie3. -> '' или ошибка."""
    import json
    import sqlite3
    import time
    from pathlib import Path

    import lz4.block

    from yaklass_bot.session import load_cookies
    prof = Path(tmp) / "profile"
    (prof / "sessionstore-backups").mkdir(parents=True)
    db = sqlite3.connect(prof / "cookies.sqlite")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA wal_autocheckpoint=0")
    db.execute("CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, originAttributes TEXT NOT NULL DEFAULT '', name TEXT, "
               "value TEXT, host TEXT, path TEXT, expiry INTEGER, lastAccessed INTEGER, creationTime INTEGER, "
               "isSecure INTEGER, isHttpOnly INTEGER, inBrowserElement INTEGER DEFAULT 0, sameSite INTEGER DEFAULT 0, "
               "schemeMap INTEGER DEFAULT 0)")
    db.execute("INSERT INTO moz_cookies(name,value,host,path,expiry,isSecure,isHttpOnly) VALUES('wal_cookie','1',"
               "'.yaklass.ru','/',?,1,1)", (int(time.time()) + 9999,))
    db.commit()                                  # остаётся в cookies.sqlite-wal, пока «браузер» открыт
    raw = json.dumps({"cookies": [{"host": "www.yaklass.ru", "path": "/", "name": "session_cookie", "value": "x"}]})
    (prof / "sessionstore-backups" / "recovery.jsonlz4").write_bytes(b"mozLz40\0" + lz4.block.compress(raw.encode()))
    got = {c.name for c in load_cookies("firefox", "yaklass.ru", cookie_file=str(prof / "cookies.sqlite"))}
    db.close()
    return "" if got == {"wal_cookie", "session_cookie"} else f"куки Firefox прочитаны неверно: {sorted(got)}"


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
    try:
        err = cookie_selftest(tmp)
    except Exception as e:  # noqa: BLE001
        err = f"{e.__class__.__name__}: {e}"
    if err:
        ok, msg = False, f" | импорт кук: {err}"
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
    from . import diagnostics
    from .backend import ensure_browsers_path
    from .settings import data_dir
    diagnostics.setup(data_dir())
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
    from PySide6.QtCore import QTimer
    QTimer.singleShot(1500, window.offer_chromium)          # выбран встроенный Chromium, а его нет — предложить установить
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
