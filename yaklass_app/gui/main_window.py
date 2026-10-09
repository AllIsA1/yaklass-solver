from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__
from ..agent import Agent
from ..backend import install_chromium
from ..settings import Store
from .pages import HomePage, LogPage, SettingsPage, WorksPage
from .theme import stylesheet
from .widgets import Bridge, Pill

NAV = [("Главная", "⌂"), ("Работы", "☰"), ("Настройки", "⚙"), ("Журнал", "▤")]


class MainWindow(QMainWindow):
    def __init__(self, agent: Agent, store: Store, bridge: Bridge):
        super().__init__()
        self.agent, self.store, self.bridge = agent, store, bridge
        self.setWindowTitle("Yaklass Solver")
        self.resize(1040, 700)
        self.setMinimumSize(880, 580)
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # --- боковая панель
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(230)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(16, 22, 16, 18)
        sl.setSpacing(6)
        brand = QLabel("Yaklass Solver")
        brand.setObjectName("brand")
        sub = QLabel("помощник для работ")
        sub.setObjectName("brandSub")
        sl.addWidget(brand)
        sl.addWidget(sub)
        sl.addSpacing(18)
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        self.stack = QStackedWidget()
        self.home, self.works, self.settings, self.log = HomePage(), WorksPage(), SettingsPage(), LogPage()
        for i, ((title, icon), page) in enumerate(zip(NAV, (self.home, self.works, self.settings, self.log))):
            b = QPushButton(f"{icon}   {title}")
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self.nav.addButton(b, i)
            sl.addWidget(b)
            self.stack.addWidget(page)
        self.nav.idClicked.connect(self.stack.setCurrentIndex)
        self.nav.button(0).setChecked(True)
        sl.addStretch(1)
        self.conn = Pill("Нет связи", "bad")
        sl.addWidget(self.conn, 0, Qt.AlignLeft)
        ver = QLabel(f"версия {__version__}")
        ver.setObjectName("brandSub")
        sl.addWidget(ver)
        lay.addWidget(side)
        lay.addWidget(self.stack, 1)

        # --- сигналы
        bridge.event.connect(self.on_event)
        bridge.done.connect(lambda cb, res, err: cb(res, err))
        self.home.link_requested.connect(self.do_link)
        self.home.unlink_requested.connect(self.agent.unlink)
        self.home.login_requested.connect(self.do_login)
        self.home.pause_requested.connect(self.agent.pause)
        self.home.resume_requested.connect(self.agent.resume)
        self.home.stop_requested.connect(self.agent.stop_run)
        self.works.refresh_requested.connect(self.refresh_works)
        self.works.start_requested.connect(self.start_work)
        self.settings.saved.connect(self.on_settings_saved)
        self.settings.install_chromium_requested.connect(self.do_install_chromium)
        self.settings.check_updates_requested.connect(self.do_check_updates)
        self.home.open_url_requested.connect(lambda u: QDesktopServices.openUrl(QUrl(u)) if u.startswith('https://') else None)
        self.home.dismiss_update_requested.connect(self.home.hide_update)
        self.home.install_update_requested.connect(self.do_install_update)
        self.settings.key_changed.connect(lambda k: setattr(self.store, "own_api_key", k))
        self.load_settings()

    # ------------------------------------------------------------ вспомогательное
    def run_async(self, fn: Callable, cb: Callable) -> None:
        def work():
            try:
                res, err = fn(), None
            except Exception as e:  # noqa: BLE001
                res, err = None, e
            self.bridge.done.emit(cb, res, err)
        threading.Thread(target=work, daemon=True).start()

    def load_settings(self) -> None:
        s = self.store.load()
        QApplication.instance().setStyleSheet(stylesheet(s.theme))
        self.settings.load(s, bool(self.store.own_api_key))
        self.home.set_login_mode(s.browser_mode == "cookies", s.cookie_browser)
        self.refresh_link_state()

    def refresh_link_state(self, note: str = "") -> None:
        linked = bool(self.store.token)
        self.home.set_linked(linked, self.agent.username, self.agent.connected, note)
        self.conn.set(("Подключено" if self.agent.connected else "Нет связи") if linked else "Не привязано",
                      "ok" if self.agent.connected else ("bad" if linked else "warn"))

    # ------------------------------------------------------------ действия
    def do_link(self, code: str) -> None:
        def done(res, err):
            self.home.btn_link.setEnabled(True)
            if err:
                self.refresh_link_state(f"⚠ {err}")
                QMessageBox.warning(self, "Не удалось привязать", str(err))
            else:
                self.home.code.clear()
                self.refresh_link_state()
        self.run_async(lambda: self.agent.link(code), done)

    def refresh_works(self) -> None:
        self.works.set_message("Обновляю…")
        self.run_async(self.agent.refresh_works, lambda res, err: self.works.set_message(
            f"Не удалось: {err}" if err else ("" if res else "Новых работ нет или нужен вход в ЯКласс")))

    def start_work(self, work_id: str, mode: str) -> None:
        ok, why = self.agent.start_work(work_id, mode)
        if not ok:
            QMessageBox.warning(self, "Не удалось запустить", why)
        else:
            self.nav.button(0).click()

    def do_install_chromium(self) -> None:
        self.settings.btn_chromium.setEnabled(False)
        self.settings.btn_chromium.setText("Скачиваю…")
        self.nav.button(3).click()

        def done(ok, err):
            self.settings.btn_chromium.setEnabled(True)
            self.settings.btn_chromium.setText("Скачать Chromium (~150 МБ)")
            self.log.add("Chromium установлен — выберите «Встроенный Chromium» в настройках." if ok and not err
                         else f"Не удалось скачать Chromium: {err or 'см. журнал'}")
        self.run_async(lambda: install_chromium(lambda m: self.bridge.event.emit("log", {"text": m})), done)

    def do_install_update(self) -> None:
        if self.agent.state != "idle":
            QMessageBox.warning(self, "Нельзя обновиться сейчас", "Идёт выполнение работы. Обновитесь после её окончания.")
            return
        v = self.agent.release.version if self.agent.release else "?"
        ok = QMessageBox.question(self, "Обновить приложение?",
                                  f"Будет скачана версия {v}; после проверки контрольной суммы приложение закроется и "
                                  "откроется заново. Продолжить?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return

        def done(res, err):
            if err:                                   # дошли сюда только при ошибке: при успехе процесс перезапускается
                self.home.btn_upd_install.setEnabled(True)
                self.home.upd_progress.hide()
                self.log.add(f"Обновление не удалось: {err}")
                QMessageBox.warning(self, "Обновление не удалось", str(err))
        self.run_async(self.agent.install_update, done)

    def do_check_updates(self) -> None:
        self.settings.update_msg.setText("Проверяю…")
        self.run_async(lambda: self.agent.check_updates(manual=True), lambda res, err: None)

    def do_login(self) -> None:
        self.home.set_login(None, "Проверяю…", checking=True)
        self.agent.login_yaklass()

    def on_settings_saved(self, s) -> None:
        old = self.store.load()
        self.store.save(s)
        QApplication.instance().setStyleSheet(stylesheet(s.theme))
        # соединение с сервером зависит только от адреса сервера; иначе лишний разрыв (и повторные
        # сообщения от бота) при каждом сохранении настроек
        if old.server_url != s.server_url:
            self.agent.reconnect()
        self.home.set_login_mode(s.browser_mode == "cookies", s.cookie_browser)
        self.log.add("Настройки сохранены")

    # ------------------------------------------------------------ события агента
    def on_event(self, name: str, p: dict) -> None:
        if name == "log":
            self.log.add(p["text"])
        elif name in ("connection", "linked", "unlinked"):
            if name == "linked":
                self.log.add(f"Приложение привязано к Telegram (@{p.get('username') or '?'})")
            if name == "unlinked":
                self.log.add(p.get("message", "Приложение отвязано"))
            if name == "connection":
                self.log.add(p.get("message") or ("Подключено" if p.get("connected") else "Связь потеряна"))
                if p.get("connected"):
                    self.run_async(self.agent.server_info, self._show_quota)
            self.refresh_link_state(p.get("message", "") if name == "unlinked" else "")
        elif name == "state":
            self.home.set_run_state(p["state"], p.get("work_id"))
        elif name == "progress":
            self.home.set_progress(p["done"], p["total"], p.get("note", ""))
        elif name == "finished":
            ok = p.get("completed")
            parts = ", ".join(f"{k}: {v}" for k, v in (p.get("summary") or {}).items())
            self.home.set_result(("✅ Работа завершена. " if ok else "⚠ Работа не завершена. ") + (p.get("note") or "") +
                                 (f"\n{parts}" if parts else ""))
            self.log.add(f"Итог: {parts} — {p.get('note', '')}")
        elif name == "works":
            self.works.set_works(p["works"], p.get("new_ids", []))
        elif name == "login_required":
            self.home.set_login(False, p.get("message", ""))
            self.works.set_message("Нужен вход в ЯКласс — см. главную страницу.")
        elif name == "update_available":
            self.home.show_update(p["version"], p["current"], p.get("notes", ""), p.get("url", ""), p.get("can_install", False))
            self.settings.update_msg.setText(f"Доступна версия {p['version']}")
            self.log.add(f"Доступна новая версия {p['version']} (у вас {p['current']})")
        elif name == "update_progress":
            self.home.set_update_progress(p["done"], p["total"])
        elif name == "update_status":
            self.settings.update_msg.setText(("✓ " if p.get("ok") else "⚠ ") + p.get("message", ""))
        elif name == "login_status":
            self.home.set_login(p.get("ok"), p.get("message", ""), p.get("checking", False))
        elif name == "error":
            self.log.add(f"Ошибка: {p.get('message')}")

    def _show_quota(self, info, err) -> None:
        if err or not info:
            return
        self.refresh_link_state(f"Запросов сегодня: {info['used_today']} из {info['quota_per_day']}")

    def closeEvent(self, e) -> None:
        self.agent.shutdown()
        super().closeEvent(e)
