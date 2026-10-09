"""Страницы приложения."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QScrollArea,
                               QVBoxLayout, QWidget)

from ..settings import AppSettings
from .widgets import Card, NumField, Pill, form_row, label, row


def page_header(title: str, sub: str = "") -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("pageTitle")
    lay.addWidget(t)
    if sub:
        s = QLabel(sub)
        s.setObjectName("pageSub")
        s.setWordWrap(True)
        lay.addWidget(s)
    return lay


def scrolled(inner: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setWidget(inner)
    return sa


class Page(QWidget):
    def __init__(self, title: str, sub: str = ""):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 20)
        outer.setSpacing(18)
        outer.addLayout(page_header(title, sub))
        self.content = QVBoxLayout()
        self.content.setSpacing(14)
        outer.addLayout(self.content, 1)


# ============================================================ Главная
class HomePage(Page):
    link_requested = Signal(str)
    unlink_requested = Signal()
    login_requested = Signal()
    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()
    open_url_requested = Signal(str)
    dismiss_update_requested = Signal()
    install_update_requested = Signal()

    def __init__(self):
        super().__init__("Главная", "Состояние приложения и выполнение работы")
        # --- Telegram
        self.tg = Card("Telegram", "Приложение работает на вашем компьютере. Бот только отдаёт команды и присылает уведомления.")
        self.tg_status = Pill("Не привязано", "warn")
        self.tg_info = label("", "hint")
        self.steps = label("1. Откройте бота в Telegram и отправьте <b>/link</b>.<br>2. Введите полученный код ниже.", "muted")
        self.code = QLineEdit()
        self.code.setPlaceholderText("XXXX-XXXX")
        self.code.setMaxLength(12)
        self.code.returnPressed.connect(self._link)
        self.btn_link = QPushButton("Привязать")
        self.btn_link.setObjectName("primary")
        self.btn_link.clicked.connect(self._link)
        self.btn_unlink = QPushButton("Отвязать")
        self.btn_unlink.setObjectName("danger")
        self.btn_unlink.clicked.connect(self.unlink_requested.emit)
        self.link_row = QWidget()
        lr = row(self.code, self.btn_link)
        self.link_row.setLayout(lr)
        self.tg.body.addLayout(row(self.tg_status, None))
        self.tg.body.addWidget(self.tg_info)
        self.tg.body.addWidget(self.steps)
        self.tg.body.addWidget(self.link_row)
        self.tg.body.addLayout(row(self.btn_unlink, None))
        # --- ЯКласс
        self.yk = Card("ЯКласс", "Вход выполняется в окне браузера один раз; пароль приложению не передаётся.")
        self.yk_status = Pill("Не проверено", "")
        self.yk_info = label("", "hint")
        self.btn_login = QPushButton("Войти в ЯКласс")
        self.btn_login.clicked.connect(self.login_requested.emit)
        self.yk.body.addLayout(row(self.yk_status, None, self.btn_login))
        self.yk.body.addWidget(self.yk_info)
        # --- Выполнение
        self.run = Card("Выполнение")
        self.run_state = Pill("Простаивает", "")
        self.run_text = label("Выберите работу на вкладке «Работы» или запустите её командой из Telegram.", "muted")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.bar.setTextVisible(True)
        self.btn_pause = QPushButton("Пауза")
        self.btn_resume = QPushButton("Продолжить")
        self.btn_stop = QPushButton("Стоп")
        self.btn_stop.setObjectName("danger")
        self.btn_pause.clicked.connect(self.pause_requested.emit)
        self.btn_resume.clicked.connect(self.resume_requested.emit)
        self.btn_stop.clicked.connect(self.stop_requested.emit)
        self.run.body.addLayout(row(self.run_state, None))
        self.run.body.addWidget(self.run_text)
        self.run.body.addWidget(self.bar)
        self.run.body.addLayout(row(self.btn_pause, self.btn_resume, self.btn_stop, None))
        # --- новая версия (плашка скрыта, пока обновлений нет)
        self.upd = Card("Доступна новая версия")
        self.upd_text = label("", "muted")
        self.upd_notes = label("", "hint")
        self.btn_upd_install = QPushButton("Обновить сейчас")
        self.btn_upd_install.setObjectName("primary")
        self.btn_upd_install.clicked.connect(self.install_update_requested.emit)
        self.upd_progress = QProgressBar()
        self.upd_progress.hide()
        self.btn_upd_open = QPushButton("Страница релиза")
        self.btn_upd_hide = QPushButton("Скрыть")
        self._upd_url = ""
        self.btn_upd_open.clicked.connect(lambda: self.open_url_requested.emit(self._upd_url))
        self.btn_upd_hide.clicked.connect(self.dismiss_update_requested.emit)
        self.upd.body.addWidget(self.upd_text)
        self.upd.body.addWidget(self.upd_notes)
        self.upd.body.addWidget(self.upd_progress)
        self.upd.body.addLayout(row(self.btn_upd_install, self.btn_upd_open, self.btn_upd_hide, None))
        self.upd.hide()
        for c in (self.upd, self.tg, self.yk, self.run):
            self.content.addWidget(c)
        self.content.addStretch(1)
        self.set_linked(False)
        self.set_run_state("idle")

    def _link(self):
        c = self.code.text().strip()
        if c:
            self.btn_link.setEnabled(False)
            self.link_requested.emit(c)

    def show_update(self, version: str, current: str, notes: str, url: str, can_install: bool = False):
        self._upd_url = url
        self.btn_upd_install.setVisible(can_install)
        self.btn_upd_install.setEnabled(True)
        self.upd_progress.hide()
        self.upd_text.setText(f"Версия {version} (у вас {current}). " + (
            "Приложение скачает её, проверит контрольную сумму и перезапустится." if can_install else
            "Скачайте сборку на странице релиза и замените файл приложения."))
        short = "\n".join([ln for ln in notes.splitlines() if ln.strip()][:6])
        self.upd_notes.setText(short)
        self.upd_notes.setVisible(bool(short))
        self.upd.show()

    def hide_update(self):
        self.upd.hide()

    def set_update_progress(self, done: int, total: int):
        self.upd_progress.show()
        self.upd_progress.setRange(0, max(total, 1))
        self.upd_progress.setValue(done)
        self.upd_progress.setFormat(f"{done // 1024 // 1024} из {max(total, 1) // 1024 // 1024} МБ")
        self.btn_upd_install.setEnabled(False)

    def set_linked(self, linked: bool, username: str = "", connected: bool = False, note: str = ""):
        self.btn_link.setEnabled(True)
        self.steps.setVisible(not linked)
        self.link_row.setVisible(not linked)
        self.btn_unlink.setVisible(linked)
        if not linked:
            self.tg_status.set("Не привязано", "warn")
            self.tg_info.setText(note)
        else:
            who = f"@{username}" if username else "аккаунт Telegram"
            self.tg_status.set(("Подключено · " if connected else "Нет связи с сервером · ") + who,
                               "ok" if connected else "bad")
            self.tg_info.setText(note)

    def set_login_mode(self, cookies: bool, browser: str = ""):
        """Профиль приложения: кнопка открывает окно входа. Куки браузера: вход берётся из браузера, кнопка проверяет."""
        self.btn_login.setText("Проверить вход" if cookies else "Войти в ЯКласс")
        self.yk.setToolTip("")
        self.yk_hint = (f"Вход берётся из браузера {browser}: войдите в ЯКласс там, затем нажмите «Проверить вход»."
                        if cookies else "")
        if not self.yk_info.text() or self.yk_info.text() == getattr(self, "_last_hint", ""):
            self.yk_info.setText(self.yk_hint)
        self._last_hint = self.yk_hint

    def set_login(self, ok: bool | None, message: str = "", checking: bool = False):
        self.btn_login.setEnabled(not checking)
        if checking:
            self.yk_status.set("Проверяю…", "warn")
        elif ok is None and message:
            self.yk_status.set("Не удалось проверить", "warn")
        elif ok is None:
            self.yk_status.set("Не проверено", "")
        elif ok:
            self.yk_status.set("Вход выполнен", "ok")
        else:
            self.yk_status.set("Нужно войти", "bad")
        self.yk_info.setText(message or getattr(self, "yk_hint", ""))

    def set_run_state(self, state: str, work_id: str | None = None):
        tone = {"idle": "", "running": "ok", "paused": "warn"}[state]
        self.run_state.set({"idle": "Простаивает", "running": f"Выполняется · работа {work_id}",
                            "paused": f"Пауза · работа {work_id}"}[state], tone)
        self.btn_pause.setVisible(state == "running")
        self.btn_resume.setVisible(state == "paused")
        self.btn_stop.setVisible(state != "idle")
        if state == "idle":
            self.run_text.setText("Выберите работу на вкладке «Работы» или запустите её командой из Telegram.")
            self.bar.setRange(0, 1)
            self.bar.setValue(0)
            self.bar.setFormat("")

    def set_progress(self, done: int, total: int, note: str):
        self.bar.setRange(0, max(total, 1))
        self.bar.setValue(done)
        self.bar.setFormat(f"{done} из {total}")
        self.run_text.setText(note)

    def set_result(self, text: str):
        self.run_text.setText(text)


# ============================================================ Работы
class WorkCard(Card):
    def __init__(self, work: dict, is_new: bool, on_run, on_dry):
        super().__init__()
        top = row(label(work.get("subject", ""), "chip", wrap=False), label("новая", "chip", wrap=False) if is_new else None, None)
        self.body.addLayout(top)
        title = label(f"<b>{work.get('title', '')}</b>", wrap=True)
        self.body.addWidget(title)
        if work.get("deadline"):
            self.body.addWidget(label(f"до {work['deadline']}", "hint"))
        run = QPushButton("▶  Выполнить")
        run.setObjectName("primary")
        dry = QPushButton("Только заполнить")
        run.clicked.connect(lambda: on_run(work["id"], work.get("title", "")))
        dry.clicked.connect(lambda: on_dry(work["id"]))
        dry.setToolTip("Ответы будут выставлены, но не отправлены — проверите и нажмёте «Ответить» сами")
        self.body.addLayout(row(run, dry, None))


class WorksPage(Page):
    refresh_requested = Signal()
    start_requested = Signal(str, str)         # work_id, mode

    def __init__(self):
        super().__init__("Работы", "Новые проверочные работы из вашего аккаунта ЯКласс")
        self.refresh = QPushButton("Обновить")
        self.refresh.clicked.connect(self.refresh_requested.emit)
        self.status = label("", "muted")
        self.content.addLayout(row(self.status, None, self.refresh))
        self.list_host = QWidget()
        self.list = QVBoxLayout(self.list_host)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(12)
        self.list.addStretch(1)
        self.content.addWidget(scrolled(self.list_host), 1)
        self.busy = False

    def set_works(self, works: list[dict], new_ids: set[str] | list[str] = ()):
        while self.list.count() > 1:
            w = self.list.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, w in enumerate(works):
            self.list.insertWidget(i, WorkCard(w, w["id"] in set(new_ids), self._run, self._dry))
        self.status.setText(f"Найдено работ: {len(works)}" if works else "Новых работ нет.")

    def set_message(self, text: str):
        self.status.setText(text)

    def _run(self, work_id: str, title: str):
        ok = QMessageBox.question(
            self, "Выполнить работу?",
            f"«{title}»\n\nПриложение само нажмёт «Начать», ответит на все задания и завершит работу. "
            "Это может потратить попытку, и отменить результат нельзя.\n\nПродолжить?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok == QMessageBox.Yes:
            self.start_requested.emit(work_id, "auto")

    def _dry(self, work_id: str):
        self.start_requested.emit(work_id, "dry")


# ============================================================ Настройки
def _num(lo: float, hi: float, value: float, decimals: int = 1) -> NumField:
    return NumField(lo, hi, value, decimals)


class SettingsPage(Page):
    saved = Signal(object)                    # AppSettings
    install_chromium_requested = Signal()
    check_updates_requested = Signal()
    key_changed = Signal(str)                 # новый ключ своего API

    def __init__(self):
        super().__init__("Настройки", "Изменения применяются после нажатия «Сохранить»")
        host = QWidget()
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(14)
        # --- сервер
        c = Card("Сервер", "Адрес сервера, к которому вы привязываете приложение (его даёт администратор бота).")
        self.server = QLineEdit()
        self.server.setPlaceholderText("https://solver.example.com")
        self.device = QLineEdit()
        c.body.addWidget(form_row("Адрес сервера", self.server))
        c.body.addWidget(form_row("Имя этого компьютера", self.device, "Показывается в списке привязанных приложений."))
        col.addWidget(c)
        # --- решатель
        c = Card("Решатель ответов")
        self.mode_server = QRadioButton("Сервер — общий API, у сервера есть суточный лимит запросов")
        self.mode_own = QRadioButton("Свой API — запросы идут прямо с этого компьютера, ключ хранится только здесь")
        self.mode_server.toggled.connect(self._toggle_own)
        c.body.addWidget(self.mode_server)
        c.body.addWidget(self.mode_own)
        self.own = QWidget()
        ol = QVBoxLayout(self.own)
        ol.setContentsMargins(0, 6, 0, 0)
        ol.setSpacing(10)
        self.api_url = QLineEdit()
        self.api_model = QLineEdit()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_vision = QCheckBox("Модель понимает картинки (vision)")
        self.api_json = QCheckBox("Режим JSON (response_format=json_object)")
        self.searx = QLineEdit()
        self.searx.setPlaceholderText("необязательно, например http://192.168.1.10:8080")
        self.search_mode = QComboBox()
        self.search_mode.addItem("Искать перед каждым ответом", "always")
        self.search_mode.addItem("Модель решает сама", "model")
        self.search_mode.addItem("Не искать", "off")
        ol.addWidget(form_row("Адрес API (OpenAI-совместимый)", self.api_url, "OpenAI, DeepSeek, OpenRouter, Ollama, LM Studio…"))
        ol.addWidget(form_row("Модель", self.api_model))
        ol.addWidget(form_row("Ключ API", self.api_key, "Пусто — оставить прежний. Локальным серверам ключ не нужен."))
        ol.addWidget(self.api_vision)
        ol.addWidget(self.api_json)
        ol.addWidget(form_row("SearXNG", self.searx))
        ol.addWidget(form_row("Поиск", self.search_mode))
        c.body.addWidget(self.own)
        col.addWidget(c)
        # --- темп
        c = Card("Темп работы", "Паузы делают работу неторопливой. Сервер может задать минимальную паузу между заданиями.")
        self.p_between = (_num(0, 600, 1), _num(0, 600, 3))
        self.p_submit = (_num(0, 120, 0.5), _num(0, 120, 1.5))
        self.p_field = (_num(0, 60, 0.25, 2), _num(0, 60, 0.9, 2))
        self.p_type = (_num(0, 1000, 40, 0), _num(0, 1000, 110, 0))
        for cap, pair, hint in (("Пауза между заданиями, секунд", self.p_between, "случайное значение от … до …"),
                                ("Пауза перед «Ответить», секунд", self.p_submit, ""),
                                ("Пауза между полями, секунд", self.p_field, ""),
                                ("Задержка на символ при вводе, миллисекунд", self.p_type, "")):
            w = QWidget()
            w.setLayout(row(pair[0], QLabel("—"), pair[1], None))
            c.body.addWidget(form_row(cap, w, hint))
        col.addWidget(c)
        # --- браузер
        c = Card("Браузер")
        self.bmode = QComboBox()
        self.bmode.addItem("Собственный профиль приложения (рекомендуется)", "profile")
        self.bmode.addItem("Куки из моего браузера", "cookies")
        self.cookie_browser = QComboBox()
        for b in ("chrome", "firefox", "edge", "chromium", "brave", "opera", "vivaldi", "librewolf"):
            self.cookie_browser.addItem(b, b)
        self.channel = QComboBox()
        self.channel.addItem("Встроенный Chromium Playwright", "")
        self.channel.addItem("Установленный Google Chrome", "chrome")
        self.channel.addItem("Установленный Microsoft Edge", "msedge")
        self.show_browser = QCheckBox("Показывать окно браузера при выполнении (скрытый режим сайт может отклонять)")
        self.bmode.currentIndexChanged.connect(self._toggle_cookie)
        c.body.addWidget(form_row("Откуда брать вход в ЯКласс", self.bmode,
                                  "Профиль: войдите один раз кнопкой на главной. Куки: берутся из выбранного браузера "
                                  "(на Windows с новым Chrome это часто не работает)."))
        self.cookie_row = form_row("Браузер, из которого читать куки", self.cookie_browser)
        c.body.addWidget(self.cookie_row)
        self.btn_chromium = QPushButton("Скачать Chromium (~150 МБ)")
        self.btn_chromium.clicked.connect(self.install_chromium_requested.emit)
        c.body.addWidget(form_row("Браузер для выполнения", self.channel,
                                  "Windows: Microsoft Edge уже есть в системе. Если выбранного браузера нет, "
                                  "скачайте встроенный Chromium и выберите его."))
        c.body.addWidget(self.btn_chromium)
        self.browser_path = QLineEdit()
        self.browser_path.setPlaceholderText("необязательно, например /usr/bin/chromium")
        c.body.addWidget(form_row("Путь к браузеру", self.browser_path,
                                  "Если не находится: укажите Chrome, Chromium или Edge. Приложение само пробует найти "
                                  "любой подходящий браузер на компьютере."))
        c.body.addWidget(self.show_browser)
        col.addWidget(c)
        # --- приложение
        c = Card("Приложение")
        self.remote = QCheckBox("Разрешить запуск работ командой из Telegram")
        self.auto_poll = QCheckBox("Сам проверять новые работы (раз в … минут; откроет окно браузера на несколько секунд)")
        self.poll = _num(5, 1440, 60, 0)
        self.check_updates = QCheckBox("Узнавать о новых версиях при запуске (ничего не скачивается)")
        self.btn_check_updates = QPushButton("Проверить обновления")
        self.btn_check_updates.clicked.connect(self.check_updates_requested.emit)
        self.update_msg = label("", "hint")
        self.theme = QComboBox()
        self.theme.addItem("Тёмная", "dark")
        self.theme.addItem("Светлая", "light")
        c.body.addWidget(self.remote)
        c.body.addWidget(self.auto_poll)
        c.body.addWidget(form_row("Период проверки, минут", self.poll,
                                  "Не чаще раза в 5 минут. Без этой опции список работ обновляется кнопкой «Обновить» и по команде из Telegram."))
        c.body.addWidget(form_row("Тема", self.theme))
        c.body.addWidget(self.check_updates)
        c.body.addLayout(row(self.btn_check_updates, self.update_msg, None))
        col.addWidget(c)
        col.addStretch(1)
        self.content.addWidget(scrolled(host), 1)
        self.err = label("", "muted")
        self.btn_save = QPushButton("Сохранить")
        self.btn_save.setObjectName("primary")
        self.btn_save.clicked.connect(self._save)
        self.content.addLayout(row(self.err, None, self.btn_save))
        self._base = AppSettings()

    def _toggle_own(self):
        self.own.setVisible(self.mode_own.isChecked())

    def _toggle_cookie(self):
        self.cookie_row.setVisible(self.bmode.currentData() == "cookies")

    @staticmethod
    def _set_combo(box: QComboBox, data):
        i = box.findData(data)
        box.setCurrentIndex(i if i >= 0 else 0)

    def load(self, s: AppSettings, has_key: bool):
        self._base = s
        self.server.setText(s.server_url)
        self.device.setText(s.device_name)
        (self.mode_own if s.solver_mode == "own" else self.mode_server).setChecked(True)
        a = s.own_api
        self.api_url.setText(a.get("base_url", ""))
        self.api_model.setText(a.get("model", ""))
        self.api_key.clear()
        self.api_key.setPlaceholderText("•••••••• сохранён" if has_key else "sk-…")
        self.api_vision.setChecked(bool(a.get("vision", True)))
        self.api_json.setChecked(bool(a.get("json_mode", False)))
        self.searx.setText(s.searxng_url)
        self._set_combo(self.search_mode, s.search_mode)
        p = s.pace_obj()
        for pair, rng in ((self.p_between, p.between_tasks), (self.p_submit, p.before_submit),
                          (self.p_field, p.field_pause), (self.p_type, p.typing_ms)):
            pair[0].setValue(rng[0])
            pair[1].setValue(rng[1])
        self._set_combo(self.bmode, s.browser_mode)
        self._set_combo(self.cookie_browser, s.cookie_browser)
        self._set_combo(self.channel, s.channel)
        self.browser_path.setText(s.browser_path)
        self.show_browser.setChecked(s.show_browser)
        self.remote.setChecked(s.remote_start_allowed)
        self.auto_poll.setChecked(s.auto_poll)
        self.poll.setValue(s.poll_minutes)
        self._set_combo(self.theme, s.theme)
        self.check_updates.setChecked(s.check_updates)
        self._toggle_own()
        self._toggle_cookie()
        self.err.setText("")

    def collect(self) -> AppSettings:
        s = AppSettings(**{k: getattr(self._base, k) for k in AppSettings.__dataclass_fields__})
        s.server_url = self.server.text().strip().rstrip("/")
        s.device_name = self.device.text().strip() or s.device_name
        s.solver_mode = "own" if self.mode_own.isChecked() else "server"
        s.own_api = {**s.own_api, "base_url": self.api_url.text().strip(), "model": self.api_model.text().strip(),
                     "vision": self.api_vision.isChecked(), "json_mode": self.api_json.isChecked()}
        s.searxng_url = self.searx.text().strip().rstrip("/")
        s.search_mode = self.search_mode.currentData()
        s.pace = {"between_tasks": [self.p_between[0].value(), self.p_between[1].value()],
                  "before_submit": [self.p_submit[0].value(), self.p_submit[1].value()],
                  "field_pause": [self.p_field[0].value(), self.p_field[1].value()],
                  "typing_ms": [self.p_type[0].value(), self.p_type[1].value()]}
        s.browser_mode = self.bmode.currentData()
        s.cookie_browser = self.cookie_browser.currentData()
        s.channel = self.channel.currentData()
        s.browser_path = self.browser_path.text().strip()
        s.show_browser = self.show_browser.isChecked()
        s.remote_start_allowed = self.remote.isChecked()
        s.auto_poll = self.auto_poll.isChecked()
        s.poll_minutes = self.poll.value()
        s.theme = self.theme.currentData()
        s.check_updates = self.check_updates.isChecked()
        return s

    def _save(self):
        s = self.collect()
        errs = s.validate()
        if errs:
            self.err.setText("⚠ " + "; ".join(errs))
            return
        self.err.setText("")
        if self.api_key.text().strip():
            self.key_changed.emit(self.api_key.text().strip())
            self.api_key.clear()
            self.api_key.setPlaceholderText("•••••••• сохранён")
        self._base = s
        self.saved.emit(s)
        self.err.setText("Сохранено ✓")


# ============================================================ Журнал
class LogPage(Page):
    def __init__(self):
        super().__init__("Журнал", "Что делает приложение прямо сейчас")
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(5000)
        mono = self.view.font()
        mono.setFamily("monospace")
        self.view.setFont(mono)
        clear = QPushButton("Очистить")
        clear.clicked.connect(self.view.clear)
        copy = QPushButton("Копировать всё")
        copy.clicked.connect(lambda: (self.view.selectAll(), self.view.copy()))
        self.content.addWidget(self.view, 1)
        self.content.addLayout(row(copy, clear, None))

    def add(self, text: str):
        for line in str(text).splitlines() or [""]:
            self.view.appendPlainText(f"{time.strftime('%H:%M:%S')}  {line}" if line.strip() else "")
