from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__
from ..agent import Agent
from ..backend import chromium_installed, install_chromium
from ..settings import Store
from .pages import HistoryPage, HomePage, LogPage, SettingsPage, WorksPage
from .theme import stylesheet
from .widgets import BrandMark, Bridge, Pill, refresh_icons, set_icon, set_theme_name

def app_mark(size: int) -> QWidget:
    """Иконка приложения в боковой панели (если файла нет — нарисованная буква)."""
    from pathlib import Path
    from PySide6.QtGui import QPixmap
    f = Path(__file__).resolve().parent.parent / "assets" / "icon-small.png"      # упрощённая: без мелких деталей
    pm = QPixmap(str(f)) if f.exists() else QPixmap()
    if pm.isNull():
        return BrandMark("Я", size)
    dpr = 2.0
    pm = pm.scaled(int(size * dpr), int(size * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation)
    pm.setDevicePixelRatio(dpr)
    lb = QLabel()
    lb.setFixedSize(size, size)
    lb.setPixmap(pm)
    return lb


def _pts(v) -> str:
    """Баллы: 11 или 11,5."""
    f = float(v or 0)
    return str(int(f)) if f == int(f) else f"{f:.1f}".replace(".", ",")


NAV = [("Главная", "home"), ("Работы", "works"), ("История", "history"), ("Настройки", "settings"), ("Журнал", "log")]


class MainWindow(QMainWindow):
    def __init__(self, agent: Agent, store: Store, bridge: Bridge):
        super().__init__()
        self.agent, self.store, self.bridge = agent, store, bridge
        self.setWindowTitle("Yaklass Solver")
        self.resize(1080, 720)
        self.setMinimumSize(900, 600)
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # --- боковая панель
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(232)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 16, 12, 0)
        sl.setSpacing(2)
        brand = QLabel("Yaklass Solver")
        brand.setObjectName("brand")
        head = QHBoxLayout()
        head.setContentsMargins(6, 0, 0, 0)
        head.setSpacing(10)
        head.addWidget(app_mark(30))
        head.addWidget(brand)
        head.addStretch(1)
        sl.addLayout(head)
        sl.addSpacing(22)
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        self.stack = QStackedWidget()
        self.home, self.works, self.history, self.settings, self.log = (
            HomePage(), WorksPage(), HistoryPage(), SettingsPage(), LogPage())
        self.pages = (self.home, self.works, self.history, self.settings, self.log)
        for i, ((title, icon), page) in enumerate(zip(NAV, self.pages)):
            b = QPushButton(f"  {title}")
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            set_icon(b, icon, "muted", 18)
            self.nav.addButton(b, i)
            sl.addWidget(b)
            self.stack.addWidget(page)
        self.nav.idClicked.connect(self._go)
        self.nav.button(0).setChecked(True)
        sl.addStretch(1)
        # блок состояния внизу (как карточка аккаунта)
        acc = QFrame()
        acc.setObjectName("account")
        al = QVBoxLayout(acc)
        al.setContentsMargins(6, 12, 6, 14)
        al.setSpacing(6)
        self.conn = Pill("Нет связи", "bad")
        al.addWidget(self.conn, 0, Qt.AlignLeft)
        ver = QLabel(f"Версия {__version__}")
        ver.setObjectName("version")
        ver.setContentsMargins(10, 0, 0, 0)
        al.addWidget(ver)
        sl.addWidget(acc)
        lay.addWidget(side)

        # --- основная область: верхняя полоса + страницы
        main = QWidget()
        main.setObjectName("main")
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)
        top = QFrame()
        top.setObjectName("topbar")
        top.setFixedHeight(48)
        tl = QHBoxLayout(top)
        tl.setContentsMargins(20, 0, 12, 0)
        tl.setSpacing(8)
        self.top_title = QLabel()
        self.top_title.setObjectName("topTitle")
        self.top_sub = QLabel()
        self.top_sub.setObjectName("topSub")
        self.btn_theme = QPushButton()
        self.btn_theme.setObjectName("iconBtn")
        self.btn_theme.setCursor(Qt.PointingHandCursor)
        self.btn_theme.setToolTip("Сменить тему")
        self.btn_theme.clicked.connect(self.toggle_theme)
        tl.addWidget(self.top_title)
        tl.addWidget(self.top_sub)
        tl.addStretch(1)
        tl.addWidget(self.btn_theme)
        ml.addWidget(top)
        ml.addWidget(self.stack, 1)
        lay.addWidget(main, 1)
        self._go(0)

        # --- сигналы
        bridge.event.connect(self.on_event)
        bridge.done.connect(lambda cb, res, err: cb(res, err))
        self.home.link_requested.connect(self.do_link)
        self.home.unlink_requested.connect(self.agent.unlink)
        self.home.login_requested.connect(self.do_login)
        self.home.pause_requested.connect(self.agent.pause)
        self.home.resume_requested.connect(self.agent.resume)
        self.home.stop_requested.connect(self.agent.stop_run)
        self.history.clear_requested.connect(self.clear_history)
        self.works.refresh_requested.connect(self.refresh_works)
        self.works.start_requested.connect(self.start_work)
        self.settings.saved.connect(self.on_settings_saved)
        self.settings.check_updates_requested.connect(self.do_check_updates)
        self.home.dismiss_update_requested.connect(self.home.hide_update)
        self.home.install_update_requested.connect(self.do_install_update)
        self.settings.key_changed.connect(lambda k: setattr(self.store, "own_api_key", k))
        self.load_settings()
        self.history.set_items(self.agent.history.items())

    # ------------------------------------------------------------ вспомогательное
    def run_async(self, fn: Callable, cb: Callable) -> None:
        def work():
            try:
                res, err = fn(), None
            except Exception as e:  # noqa: BLE001
                res, err = None, e
            self.bridge.done.emit(cb, res, err)
        threading.Thread(target=work, daemon=True).start()

    def _go(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        page = self.pages[i]
        self.top_title.setText(page.title_text)
        self.top_sub.setText("")                 # пояснение — под заголовком страницы, здесь не дублируем

    def apply_theme(self, name: str) -> None:
        set_theme_name(name)
        QApplication.instance().setStyleSheet(stylesheet(name))
        set_icon(self.btn_theme, "sun" if name == "dark" else "moon", "muted", 18)
        refresh_icons(self)
        for w in QApplication.topLevelWidgets():          # открытые диалоги тоже
            if w is not self:
                refresh_icons(w)

    def toggle_theme(self) -> None:
        s = self.store.load()
        s.theme = "light" if s.theme == "dark" else "dark"
        self.store.save(s)
        self.settings._set_combo(self.settings.theme, s.theme)
        self.settings._base.theme = s.theme
        self.apply_theme(s.theme)

    def load_settings(self) -> None:
        s = self.store.load()
        self.apply_theme(s.theme)
        self.settings.load(s, bool(self.store.own_api_key))
        self.home.set_login_mode(s.browser_mode == "cookies", s.cookie_browser)
        self.refresh_link_state()

    def clear_history(self) -> None:
        self.agent.history.clear()
        self.history.set_items([])

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

    def start_work(self, work_id: str, mode: str, style=None) -> None:
        if not self.offer_chromium():
            return
        ok, why = self.agent.start_work(work_id, mode, style=style)
        if not ok:
            QMessageBox.warning(self, "Не удалось запустить", why)
        else:
            self.nav.button(0).click()

    # ------------------------------------------------------------ Chromium
    def can_prompt(self) -> bool:
        """Диалоги показываем только на настоящем экране (в тестах и CI без экрана — нет: они бы зависли)."""
        return QApplication.platformName() not in ("offscreen", "minimal")

    def chromium_missing(self) -> bool:
        """Выбран встроенный Chromium Playwright (и свой путь к браузеру не задан), а его нет на компьютере."""
        s = self.store.load()
        return s.channel == "" and not s.browser_path and not chromium_installed()

    def offer_chromium(self) -> bool:
        """Всплывающее предложение установить Chromium. True — установка не требуется (можно продолжать)."""
        if not self.chromium_missing() or not self.can_prompt():
            return True
        if getattr(self, "_installing_chromium", False):
            QMessageBox.information(self, "Chromium устанавливается", "Подождите: Chromium ещё скачивается.")
            return False
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Нужен Chromium")
        box.setText("Выбран встроенный Chromium Playwright, но он не установлен.")
        box.setInformativeText("Для выполнения работ его нужно скачать (около 150 МБ). Установить сейчас?")
        install = box.addButton("Установить", QMessageBox.AcceptRole)
        box.addButton("Позже", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is install:
            self.do_install_chromium()
        return False

    def do_install_chromium(self) -> None:
        if getattr(self, "_installing_chromium", False):
            return
        self._installing_chromium = True
        self.home.show_chromium_install()
        self.nav.button(0).click()
        self.log.add("Скачиваю Chromium…")

        def done(ok, err):
            self._installing_chromium = False
            self.home.hide_chromium_install()
            if ok and not err:
                self.log.add("Chromium установлен")
                QMessageBox.information(self, "Chromium установлен", "Готово — теперь можно выполнять работы.")
            else:
                self.log.add(f"Не удалось скачать Chromium: {err or 'см. журнал'}")
                QMessageBox.warning(self, "Не удалось установить Chromium",
                                    f"{err or 'Подробности — в журнале.'}\n\nМожно выбрать установленный браузер "
                                    "(Chrome, Edge) в Настройки → Браузер.")
        self.run_async(lambda: install_chromium(lambda m: self.bridge.event.emit("log", {"text": m})), done)

    def do_install_update(self) -> None:
        if self.agent.state != "idle":
            QMessageBox.warning(self, "Нельзя обновиться сейчас", "Идёт выполнение работы. Обновитесь после её окончания.")
            return
        v = self.agent.release.version if self.agent.release else "?"
        what = (f"Версия {v} уже скачана и проверена; приложение закроется и откроется заново."
                if self.agent.update_ready else
                f"Будет скачана версия {v}; после проверки контрольной суммы приложение закроется и откроется заново.")
        ok = QMessageBox.question(self, "Обновить приложение?", f"{what} Продолжить?",
                                  QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
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
        if not self.offer_chromium():
            return
        self.home.set_login(None, "Проверяю…", checking=True)
        self.agent.login_yaklass()

    def on_settings_saved(self, s) -> None:
        old = self.store.load()
        s.server_url = old.server_url          # адрес сервера задаёт только привязка (код подключения), не форма настроек
        self.store.save(s)
        self.apply_theme(s.theme)
        if (s.channel, s.browser_path) != (old.channel, old.browser_path):
            QTimer.singleShot(0, self.offer_chromium)
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
            self.home.set_run_state(p["state"], p.get("title") or p.get("work_id"))
        elif name == "progress":
            self.home.set_progress(p["done"], p["total"], p.get("note", ""))
        elif name == "finished":
            ok = p.get("completed")
            parts = ", ".join(f"{k}: {v}" for k, v in (p.get("summary") or {}).items())
            score = ""
            if p.get("percent") is not None and p.get("total"):
                score = f" Результат: {p['percent']:g}% ({_pts(p.get('earned'))}/{_pts(p.get('total'))})."
            self.home.set_result(("Работа завершена." if ok else "Работа не завершена.") + score + " " + (p.get("note") or "") +
                                 (f"\n{parts}" if parts else ""))
            self.log.add(f"Итог: {parts} — {p.get('note', '')}")
        elif name == "history":
            self.history.set_items(self.agent.history.items())
        elif name == "works":
            self.works.set_works(p["works"], p.get("new_ids", []))
        elif name == "login_required":
            self.home.set_login(False, p.get("message", ""))
            self.works.set_message("Нужен вход в ЯКласс — см. главную страницу.")
        elif name == "update_available":
            self.home.show_update(p["version"], p["current"], p.get("notes", ""), p.get("url", ""), p.get("can_install", False),
                                  p.get("ready", False), p.get("prefetch", False))
            self.settings.update_msg.setText(f"Доступна версия {p['version']}")
            self.log.add(f"Доступна новая версия {p['version']} (у вас {p['current']})")
        elif name == "update_progress":
            self.home.set_update_progress(p["done"], p["total"], p.get("background", False))
        elif name == "update_ready":
            self.home.set_update_ready(p["version"])
        elif name == "update_status":
            self.settings.update_msg.setText(("✓ " if p.get("ok") else "⚠ ") + p.get("message", ""))
        elif name == "login_status":
            self.home.set_login(p.get("ok"), p.get("message", ""), p.get("checking", False))
        elif name == "error":
            self.log.add(f"Ошибка: {p.get('message')}")

    def _show_quota(self, info, err) -> None:
        if err or not info:
            return
        self.refresh_link_state(f"Запусков работ сегодня: {info['used_today']} из {info['quota_per_day']}")

    def closeEvent(self, e) -> None:
        self.agent.shutdown()
        super().closeEvent(e)
