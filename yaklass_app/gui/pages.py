"""Страницы приложения."""
from __future__ import annotations

import time

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from yaklass_bot.stealth import GRADE_BANDS, RunStyle

from ..history import fmt_duration, fmt_points
from ..settings import AppSettings
from .widgets import (Card, NumField, OptionCard, Pill, SettingRow, Toggle, divider, form_row, icon_label, label, row,
                      section, set_icon)

COLUMN_WIDTH = 780                 # ширина колонки контента (как у чата: текст не растягивается на весь экран)


def scrolled(inner: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setWidget(inner)
    return sa


def centered(inner: QWidget, top: int = 0, bottom: int = 0) -> QWidget:
    """Колонка фиксированной максимальной ширины по центру."""
    inner.setMaximumWidth(COLUMN_WIDTH)
    wrap = QWidget()
    wrap.setObjectName("column")
    h = QHBoxLayout(wrap)
    h.setContentsMargins(36, top, 36, bottom)
    h.addStretch(1)
    h.addWidget(inner, 100)
    h.addStretch(1)
    return wrap


class Page(QWidget):
    """Страница: заголовок с засечками и колонка контента по центру. scroll — прокручивается вся колонка."""

    def __init__(self, title: str, sub: str = "", scroll: bool = True, heading: str | None = None):
        super().__init__()
        self.setObjectName("page")
        self.title_text, self.sub_text = title, sub
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        column = QWidget()
        col = QVBoxLayout(column)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(22)
        head = QVBoxLayout()
        head.setSpacing(6)
        self.heading = QLabel(heading or title)
        self.heading.setObjectName("pageTitle")
        head.addWidget(self.heading)
        if sub:
            s = QLabel(sub)
            s.setObjectName("pageSub")
            s.setWordWrap(True)
            head.addWidget(s)
        col.addLayout(head)
        self.content = QVBoxLayout()
        self.content.setSpacing(14)
        col.addLayout(self.content, 1)
        body = centered(column, top=34, bottom=28)
        if scroll:
            outer.addWidget(scrolled(body), 1)
        else:
            outer.addWidget(body, 1)
        self._outer = outer

    def add_footer(self, layout) -> None:
        """Нижняя полоса, которая не прокручивается (например, «Сохранить»)."""
        inner = QWidget()
        inner.setLayout(layout)
        bar = QFrame()
        bar.setObjectName("footer")
        v = QVBoxLayout(bar)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(divider())
        v.addWidget(centered(inner, top=12, bottom=14))
        self._outer.addWidget(bar)


def greeting(hour: int | None = None) -> str:
    h = time.localtime().tm_hour if hour is None else hour
    if 5 <= h < 12:
        return "Доброе утро"
    if 12 <= h < 17:
        return "Добрый день"
    if 17 <= h < 23:
        return "Добрый вечер"
    return "Доброй ночи"


HEADING_LINES = ("изменения", "changes", "что нового")


def release_notes_text(notes: str, max_lines: int = 200) -> str:
    """Описание релиза без разметки Markdown и служебных строк («Изменения:» — это заголовок самого окна,
    «Full Changelog» и т.п. от GitHub); списки — «•»."""
    out = []
    for ln in notes.splitlines():
        t = ln.strip().replace("**", "").replace("`", "").lstrip("#").strip()
        if not t or t.lower().startswith(("full changelog", "what's changed")):
            continue
        if t.rstrip(":： ").lower() in HEADING_LINES:            # строка-заголовок «Изменения:» не нужна
            continue
        if t[:2] in ("* ", "- "):
            t = "• " + t[2:].strip()
        out.append(t)
    return "\n".join(out[:max_lines])


class NotesDialog(QDialog):
    """Описание новой версии: прокручиваемый текст и кнопка «OK» в правом нижнем углу."""

    def __init__(self, parent, version: str, notes: str):
        super().__init__(parent)
        self.setWindowTitle("Что нового")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 22)
        lay.setSpacing(14)
        h = QLabel(f"Что нового в версии {version}")
        h.setObjectName("pageTitle")
        h.setStyleSheet("font-size: 22px;")
        lay.addWidget(h)
        self.text = QPlainTextEdit()
        self.text.setObjectName("notes")
        self.text.setReadOnly(True)
        self.text.setPlainText(release_notes_text(notes) or "Описание этого релиза не добавлено.")
        self.text.setMinimumHeight(220)
        lay.addWidget(self.text, 1)
        self.btn_ok = _btn("OK", "primary")
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self.accept)
        lay.addLayout(row(None, self.btn_ok))                    # справа внизу
        self.setMinimumWidth(560)
        self.resize(600, 420)


def _btn(text: str, kind: str = "", icon: str = "", role: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.setCursor(Qt.PointingHandCursor)
    if icon:
        set_icon(b, icon, role or ("accent_t" if kind == "primary" else "muted"), 16)
    return b


# ============================================================ Главная
class HomePage(Page):
    link_requested = Signal(str)
    unlink_requested = Signal()
    login_requested = Signal()
    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()
    dismiss_update_requested = Signal()
    install_update_requested = Signal()

    def __init__(self):
        super().__init__("Главная", "Состояние приложения и выполнение работы", heading=greeting())
        self.cards = self.content
        # --- новая версия (плашка скрыта, пока обновлений нет)
        self.upd = Card("Доступна новая версия", icon="download", name="banner")
        self.upd_text = label("", "muted")
        self.btn_upd_install = _btn("Обновить сейчас", "primary")
        self.btn_upd_install.clicked.connect(self.install_update_requested.emit)
        self.upd_progress = QProgressBar()
        self.upd_progress.setTextVisible(False)          # сколько скачано — в тексте выше и на кнопке
        self.upd_progress.hide()
        self.btn_upd_open = _btn("Что нового")
        self._upd_notes = ""
        self.btn_upd_hide = _btn("Скрыть", "ghost")
        self._upd_url = ""
        self.btn_upd_open.clicked.connect(self.show_release_notes)
        self.btn_upd_hide.clicked.connect(self.dismiss_update_requested.emit)
        self.upd.body.addWidget(self.upd_text)
        self.upd.body.addWidget(self.upd_progress)
        self.upd.body.addLayout(row(self.btn_upd_install, self.btn_upd_open, None, self.btn_upd_hide))
        self.upd.hide()

        # --- установка Chromium (плашка видна только во время загрузки)
        self.chrome_card = Card("Установка Chromium", icon="download", name="banner")
        self.chrome_text = label("Скачиваю встроенный Chromium (~150 МБ). Это займёт несколько минут.", "muted")
        self.chrome_bar = QProgressBar()
        self.chrome_bar.setRange(0, 0)                  # без процентов: установщик их не сообщает
        self.chrome_card.body.addWidget(self.chrome_text)
        self.chrome_card.body.addWidget(self.chrome_bar)
        self.chrome_card.hide()

        # --- Telegram
        self.tg = Card("Telegram", icon="telegram")
        self.tg_status = Pill("Не привязано", "warn")
        self.tg.head.addWidget(self.tg_status)
        self.tg_info = label("", "hint")
        self.steps = label("Откройте бота и отправьте <b>/link</b> — он пришлёт код подключения. "
                           "Скопируйте его целиком и вставьте ниже.", "muted")
        self.code = QLineEdit()
        self.code.setObjectName("codeInput")
        self.code.setPlaceholderText("Код подключения из бота")
        self.code.setMaxLength(300)
        self.code.returnPressed.connect(self._link)
        self.btn_link = _btn("Привязать", "primary")
        self.btn_link.clicked.connect(self._link)
        self.btn_unlink = _btn("Отвязать", "danger")
        self.btn_unlink.clicked.connect(self.unlink_requested.emit)
        self.link_row = QWidget()
        self.link_row.setLayout(row(self.code, self.btn_link, fill=self.code))
        self.tg_hint = label("Приложение работает на вашем компьютере; бот только отдаёт команды и присылает "
                             "уведомления.", "hint")
        self.tg.body.addWidget(self.steps)
        self.tg.body.addWidget(self.link_row)
        self.tg.body.addWidget(self.tg_info)
        self.tg.body.addWidget(self.tg_hint)
        self.tg.body.addStretch(1)
        self.tg.body.addLayout(row(self.btn_unlink, None))

        # --- ЯКласс
        self.yk = Card("ЯКласс", icon="school")
        self.yk_status = Pill("Не проверено", "")
        self.yk.head.addWidget(self.yk_status)
        self.yk_info = label("", "hint")
        self.btn_login = _btn("Войти в ЯКласс")
        self.btn_login.clicked.connect(self.login_requested.emit)
        self.yk.body.addWidget(label("Вход выполняется в окне браузера один раз; пароль приложению не передаётся.",
                                     "muted"))
        self.yk.body.addWidget(self.yk_info)
        self.yk.body.addStretch(1)
        self.yk.body.addLayout(row(self.btn_login, None))

        pair = QGridLayout()
        pair.setHorizontalSpacing(14)
        pair.addWidget(self.tg, 0, 0)
        pair.addWidget(self.yk, 0, 1)
        pair.setColumnStretch(0, 1)
        pair.setColumnStretch(1, 1)

        # --- Выполнение
        self.run = Card("Выполнение", icon="spark")
        self.run_state = Pill("Простаивает", "")
        self.run.head.addWidget(self.run_state)
        self.run_text = label("Выберите работу на вкладке «Работы» или запустите её командой из Telegram.", "muted")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.progress_lbl = label("", "hint", wrap=False)
        self.btn_pause = _btn("Пауза", icon="pause")
        self.btn_resume = _btn("Продолжить", "primary", icon="play")
        self.btn_stop = _btn("Стоп", "danger", icon="stop", role="danger")
        self.btn_pause.clicked.connect(self.pause_requested.emit)
        self.btn_resume.clicked.connect(self.resume_requested.emit)
        self.btn_stop.clicked.connect(self.stop_requested.emit)
        self.run.body.addWidget(self.run_text)
        self.run.body.addLayout(row(self.bar, self.progress_lbl, fill=self.bar, spacing=12))
        self.run.body.addLayout(row(self.btn_pause, self.btn_resume, self.btn_stop, None))

        self.content.addWidget(self.upd)
        self.content.addWidget(self.chrome_card)
        self.content.addLayout(pair)
        self.content.addWidget(self.run)
        self.content.addStretch(1)
        self.set_linked(False)
        self.set_run_state("idle")

    def _link(self):
        c = self.code.text().strip()
        if c:
            self.btn_link.setEnabled(False)
            self.link_requested.emit(c)

    def show_update(self, version: str, current: str, notes: str, url: str, can_install: bool = False,
                    ready: bool = False, prefetch: bool = False):
        self._upd_url, self._upd_version, self._upd_notes = url, version, notes
        self.btn_upd_install.setVisible(can_install)
        self.btn_upd_install.setEnabled(True)
        self.btn_upd_install.setText("Установить и перезапустить" if ready else "Обновить сейчас")
        self.upd_progress.hide()
        if ready:
            text = ("Файл уже скачан и проверен. Установка займёт несколько секунд, приложение перезапустится.")
        elif can_install and prefetch:
            text = "Скачиваю обновление в фоне — после этого оно установится за несколько секунд."
        elif can_install:
            text = "Приложение скачает её, проверит контрольную сумму и перезапустится."
        else:
            text = "Скачайте сборку на странице релиза и замените файл приложения."
        self.upd_text.setText(f"Версия {version} (у вас {current}). {text}")
        self.upd.show()

    def show_release_notes(self):
        NotesDialog(self, getattr(self, "_upd_version", ""), self._upd_notes).exec()

    def hide_update(self):
        self.upd.hide()

    def show_chromium_install(self, text: str = ""):
        if text:
            self.chrome_text.setText(text)
        self.chrome_card.show()

    def hide_chromium_install(self):
        self.chrome_card.hide()

    def set_update_progress(self, done: int, total: int, background: bool = False):
        mb = f"{done // 1024 // 1024} из {max(total, 1) // 1024 // 1024} МБ"
        self.upd_progress.show()
        self.upd_progress.setRange(0, max(total, 1))
        self.upd_progress.setValue(done)
        self.upd_progress.setFormat(mb)
        if background:                      # фоновая загрузка: кнопка остаётся доступной (дождётся конца загрузки)
            self.upd_text.setText(f"Версия {getattr(self, '_upd_version', '')}: скачиваю в фоне — {mb}.")
            return
        self.btn_upd_install.setEnabled(False)
        self.btn_upd_install.setText(f"Скачиваю… {mb}")

    def set_update_ready(self, version: str):
        self.upd_progress.hide()
        self.btn_upd_install.setText("Установить и перезапустить")
        self.btn_upd_install.setEnabled(True)
        self.upd_text.setText(f"Версия {version} скачана и проверена. Установка займёт несколько секунд, "
                              "приложение перезапустится.")

    def set_linked(self, linked: bool, username: str = "", connected: bool = False, note: str = ""):
        self.btn_link.setEnabled(True)
        self.steps.setVisible(not linked)
        self.link_row.setVisible(not linked)
        self.btn_unlink.setVisible(linked)
        if not linked:
            self.tg_status.set("Не привязано", "warn")
        else:
            who = f"@{username}" if username else "аккаунт Telegram"
            self.tg_status.set(("Подключено · " if connected else "Нет связи · ") + who,
                               "ok" if connected else "bad")
        self.tg_info.setText(note)
        self.tg_info.setVisible(bool(note))

    def set_login_mode(self, cookies: bool, browser: str = ""):
        """Профиль приложения: кнопка открывает окно входа. Куки браузера: вход берётся из браузера, кнопка проверяет."""
        self.btn_login.setText("Проверить вход" if cookies else "Войти в ЯКласс")
        self.yk_hint = (f"Вход берётся из браузера {browser}: войдите в ЯКласс там, затем нажмите «Проверить вход»."
                        if cookies else "")
        if not self.yk_info.text() or self.yk_info.text() == getattr(self, "_last_hint", ""):
            self.yk_info.setText(self.yk_hint)
        self.yk_info.setVisible(bool(self.yk_info.text()))
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
        self.yk_info.setVisible(bool(self.yk_info.text()))

    def set_run_state(self, state: str, work_id: str | None = None):
        tone = {"idle": "", "running": "ok", "paused": "warn"}[state]
        self.run_state.set({"idle": "Простаивает", "running": f"Выполняется · {work_id}",
                            "paused": f"Пауза · {work_id}"}[state], tone)
        self.btn_pause.setVisible(state == "running")
        self.btn_resume.setVisible(state == "paused")
        self.btn_stop.setVisible(state != "idle")
        self.bar.setVisible(state != "idle")
        self.progress_lbl.setVisible(state != "idle")
        if state == "idle":
            self.run_text.setText("Выберите работу на вкладке «Работы» или запустите её командой из Telegram.")
            self.bar.setRange(0, 1)
            self.bar.setValue(0)
            self.bar.setFormat("")
            self.progress_lbl.setText("")

    def set_progress(self, done: int, total: int, note: str):
        self.bar.setRange(0, max(total, 1))
        self.bar.setValue(done)
        self.bar.setFormat(f"{done} из {total}")
        self.progress_lbl.setText(f"{done} из {total}")
        self.run_text.setText(note)

    def set_result(self, text: str):
        self.run_text.setText(text)


# ============================================================ Работы
class WorkCard(QFrame):
    """Строка работы: предмет, название, срок; справа — действия."""

    def __init__(self, work: dict, is_new: bool, on_run, on_dry):
        super().__init__()
        self.setObjectName("workRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 14, 14, 14)
        lay.setSpacing(16)
        info = QVBoxLayout()
        info.setSpacing(6)
        subject = str(work.get("subject", ""))
        chips = [label(subject[:1].upper() + subject[1:], "chip", wrap=False)]
        if is_new:
            chips.append(label("Новая", "chipAccent", wrap=False))
        info.addLayout(row(*chips, None, spacing=6))
        title = label(str(work.get("title", "")), "workTitle", wrap=True)
        info.addWidget(title)
        if work.get("deadline"):
            info.addWidget(label(f"Срок: до {work['deadline']}", "hint"))
        lay.addLayout(info, 1)
        dry = _btn("Только заполнить", "ghost")
        dry.setToolTip("Ответы будут выставлены, но не отправлены — проверите и нажмёте «Ответить» сами")
        run = _btn("Выполнить", "primary", icon="play")
        run.clicked.connect(lambda: on_run(work["id"], work.get("title", "")))
        dry.clicked.connect(lambda: on_dry(work["id"]))
        lay.addWidget(dry, 0, Qt.AlignVCenter)
        lay.addWidget(run, 0, Qt.AlignVCenter)


_LAST_STYLE = {"kind": "normal", "min": 10, "sec": 0, "grade": 4}      # выбор запоминается до закрытия приложения


class RunDialog(QDialog):
    """Подтверждение запуска + выбор режима: обычный, быстрый или скрытный (время и оценка)."""

    def __init__(self, parent, title: str):
        super().__init__(parent)
        self.setWindowTitle("Выполнить работу")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 22)
        lay.setSpacing(14)
        h = QLabel("Выполнить работу?")
        h.setObjectName("pageTitle")
        h.setStyleSheet("font-size: 22px;")
        lay.addWidget(h)
        lay.addWidget(label(f"«{title}»", "rowTitle"))
        lay.addWidget(label("Приложение само нажмёт «Начать», ответит на задания и завершит работу. "
                            "Это может потратить попытку, и отменить результат нельзя.", "muted"))
        lay.addWidget(section("Режим"))
        self.r_normal = OptionCard("Обычный", "Паузы из «Настроек»", "spark")
        self.r_fast = OptionCard("Быстрый", "Минимальные паузы", "bolt")
        self.r_stealth = OptionCard("Скрытный", "За заданное время и на заданную оценку", "moon")
        opts = QHBoxLayout()
        opts.setSpacing(10)
        for r in (self.r_normal, self.r_fast, self.r_stealth):
            opts.addWidget(r)
        lay.addLayout(opts)
        self.stealth_box = QFrame()
        self.stealth_box.setObjectName("card")
        sb = QVBoxLayout(self.stealth_box)
        sb.setContentsMargins(16, 14, 16, 14)
        sb.setSpacing(10)
        self.minutes = NumField(1, 240, _LAST_STYLE["min"], 0, width=70)
        self.seconds = NumField(0, 59, _LAST_STYLE["sec"], 0, width=70)
        t = QWidget()
        t.setLayout(row(self.minutes, label("мин", "muted", wrap=False), self.seconds, label("с", "muted", wrap=False),
                        spacing=8))
        sb.addWidget(SettingRow("Время выполнения", t))
        self.grade = QComboBox()
        for g, (lo, hi) in GRADE_BANDS.items():
            self.grade.addItem(f"{g}   ·   {int(lo)}–{int(hi)}%", g)
        self.grade.setCurrentIndex(max(0, list(GRADE_BANDS).index(_LAST_STYLE["grade"])))
        sb.addWidget(SettingRow("Оценка", self.grade, control_width=150))
        sb.addWidget(label(
            "Целые минуты слегка размываются (±57 с), но лимит времени работы соблюдается. Часть заданий будет "
            "решена верно, часть оставлена без ответа, часть решена неверно или частично — так, чтобы набрать "
            "процент из выбранного диапазона. Оценка ожидаемая: верность ответов модели заранее неизвестна.",
            "hint"))
        lay.addWidget(self.stealth_box)
        {"fast": self.r_fast, "stealth": self.r_stealth}.get(_LAST_STYLE["kind"], self.r_normal).setChecked(True)
        self.r_stealth.toggled.connect(self._sync)
        self._sync()
        ok, cancel = _btn("Выполнить", "primary", icon="play"), _btn("Отмена", "ghost")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        lay.addSpacing(4)
        lay.addLayout(row(None, cancel, ok))
        self.setMinimumWidth(640)

    def _sync(self) -> None:
        self.stealth_box.setVisible(self.r_stealth.isChecked())
        QTimer.singleShot(0, self._fit)

    def _fit(self) -> None:
        """Окно верхнего уровня не учитывает перенос строк само: высоту считаем по ширине."""
        w = max(self.width(), self.minimumWidth())
        h = self.layout().totalHeightForWidth(w)
        if h > 0:
            self.resize(w, h)

    def style(self) -> RunStyle:
        kind = "stealth" if self.r_stealth.isChecked() else ("fast" if self.r_fast.isChecked() else "normal")
        mins, secs, grade = int(self.minutes.value()), int(self.seconds.value()), int(self.grade.currentData())
        _LAST_STYLE.update(kind=kind, min=mins, sec=secs, grade=grade)
        if kind == "stealth":
            return RunStyle("stealth", mins + secs / 60.0, grade)
        return RunStyle(kind)


class WorksPage(Page):
    refresh_requested = Signal()
    start_requested = Signal(str, str, object)  # work_id, mode, RunStyle

    def __init__(self):
        super().__init__("Работы", "Новые проверочные работы из вашего аккаунта ЯКласс")
        self.refresh = _btn("Обновить", icon="refresh")
        self.refresh.clicked.connect(self.refresh_requested.emit)
        self.status = label("", "muted")
        self.content.addLayout(row(self.status, self.refresh, fill=self.status))
        self.list_host = QWidget()
        self.list = QVBoxLayout(self.list_host)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(10)
        self.empty = label("Здесь появятся новые работы. Нажмите «Обновить» или дождитесь уведомления.", "empty")
        self.empty.setAlignment(Qt.AlignCenter)
        self.list.addWidget(self.empty)
        self.list.addStretch(1)
        self.content.addWidget(self.list_host, 1)
        self.busy = False

    def set_works(self, works: list[dict], new_ids: set[str] | list[str] = ()):
        while self.list.count() > 2:
            w = self.list.takeAt(1).widget()
            if w:
                w.deleteLater()
        for i, w in enumerate(works):
            self.list.insertWidget(1 + i, WorkCard(w, w["id"] in set(new_ids), self._run, self._dry))
        self.empty.setVisible(not works)
        self.status.setText(f"Найдено работ: {len(works)}" if works else "Новых работ нет.")

    def set_message(self, text: str):
        self.status.setText(text)

    def _run(self, work_id: str, title: str):
        dlg = RunDialog(self, title)
        if dlg.exec() == QDialog.Accepted:
            self.start_requested.emit(work_id, "auto", dlg.style())

    def _dry(self, work_id: str):
        self.start_requested.emit(work_id, "dry", RunStyle())


# ============================================================ Настройки
def _num(lo: float, hi: float, value: float, decimals: int = 1) -> NumField:
    return NumField(lo, hi, value, decimals)


def _group(*rows_: QWidget) -> Card:
    """Карточка со строками настроек, разделёнными тонкими линиями."""
    c = Card()
    c.body.setSpacing(12)
    for i, r in enumerate(rows_):
        if i:
            c.body.addWidget(divider())
        c.body.addWidget(r)
    return c


class SettingsPage(Page):
    saved = Signal(object)                    # AppSettings
    check_updates_requested = Signal()
    key_changed = Signal(str)                 # новый ключ своего API

    def __init__(self):
        super().__init__("Настройки", "Изменения применяются после нажатия «Сохранить»")
        col = self.content
        col.setSpacing(10)
        W = 320
        # --- сервер
        self.device = QLineEdit()
        col.addWidget(section("Подключение"))
        col.addWidget(_group(
            SettingRow("Имя этого компьютера", self.device, "Показывается в списке привязанных приложений.",
                       control_width=W)))
        # --- решатель
        col.addSpacing(10)
        col.addWidget(section("Решатель ответов"))
        solver = Card()
        self.mode_server = OptionCard("Сервер", "Общий API; у сервера есть суточный лимит запусков", "spark")
        self.mode_own = OptionCard("Свой API", "Запросы идут прямо с этого компьютера, ключ хранится только здесь",
                                   "bolt")
        self.mode_server.toggled.connect(self._toggle_own)
        modes = QHBoxLayout()
        modes.setSpacing(10)
        modes.addWidget(self.mode_server)
        modes.addWidget(self.mode_own)
        solver.body.addLayout(modes)
        self.own = QWidget()
        ol = QVBoxLayout(self.own)
        ol.setContentsMargins(0, 8, 0, 0)
        ol.setSpacing(12)
        self.api_url = QLineEdit()
        self.api_model = QLineEdit()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_vision = Toggle()
        self.api_json = Toggle()
        self.searx = QLineEdit()
        self.searx.setPlaceholderText("необязательно, например http://192.168.1.10:8080")
        self.search_mode = QComboBox()
        self.search_mode.addItem("Искать перед каждым ответом", "always")
        self.search_mode.addItem("Модель решает сама", "model")
        self.search_mode.addItem("Не искать", "off")
        for i, r in enumerate((
                SettingRow("Адрес API", self.api_url, "OpenAI-совместимый: OpenAI, DeepSeek, OpenRouter, Ollama, LM Studio…",
                           control_width=W),
                SettingRow("Модель", self.api_model, control_width=W),
                SettingRow("Ключ API", self.api_key, "Пусто — оставить прежний. Локальным серверам ключ не нужен.",
                           control_width=W),
                SettingRow("Модель понимает картинки", self.api_vision, "vision"),
                SettingRow("Режим JSON", self.api_json, "response_format = json_object"),
                SettingRow("SearXNG", self.searx, "Поиск по интернету перед ответом", control_width=W),
                SettingRow("Поиск", self.search_mode, control_width=W))):
            ol.addWidget(divider())
            ol.addWidget(r)
        solver.body.addWidget(self.own)
        col.addWidget(solver)
        # --- темп
        col.addSpacing(10)
        col.addWidget(section("Темп работы"))
        self.p_between = (_num(0, 600, 1), _num(0, 600, 3))
        self.p_submit = (_num(0, 120, 0.5), _num(0, 120, 1.5))
        self.p_field = (_num(0, 60, 0.25, 2), _num(0, 60, 0.9, 2))
        self.p_type = (_num(0, 1000, 40, 0), _num(0, 1000, 110, 0))
        pace_rows = []
        for cap, pair, hint in (("Пауза между заданиями", self.p_between, "Случайно, от … до … секунд"),
                                ("Пауза перед «Ответить»", self.p_submit, "Случайно, от … до … секунд"),
                                ("Пауза между полями", self.p_field, "Случайно, от … до … секунд"),
                                ("Задержка на символ при вводе", self.p_type, "Случайно, от … до … миллисекунд")):
            w = QWidget()
            w.setLayout(row(pair[0], label("—", "muted", wrap=False), pair[1], spacing=8))
            pace_rows.append(SettingRow(cap, w, hint))
        pace = _group(*pace_rows)
        pace.body.insertWidget(0, label("Паузы делают работу неторопливой. Сервер может задать минимальную паузу "
                                        "между заданиями.", "hint"))
        col.addWidget(pace)
        # --- браузер
        col.addSpacing(10)
        col.addWidget(section("Браузер"))
        self.bmode = QComboBox()
        self.bmode.addItem("Собственный профиль приложения", "profile")
        self.bmode.addItem("Куки из моего браузера", "cookies")
        self.cookie_browser = QComboBox()
        for key, name in (("chrome", "Google Chrome"), ("edge", "Microsoft Edge"), ("firefox", "Mozilla Firefox"),
                          ("brave", "Brave"), ("vivaldi", "Vivaldi"), ("opera", "Opera"), ("chromium", "Chromium"),
                          ("librewolf", "LibreWolf")):
            self.cookie_browser.addItem(name, key)
        self.channel = QComboBox()
        self.channel.addItem("Встроенный Chromium Playwright", "")
        self.channel.addItem("Установленный Google Chrome", "chrome")
        self.channel.addItem("Установленный Microsoft Edge", "msedge")
        self.show_browser = Toggle()
        self.bmode.currentIndexChanged.connect(self._toggle_cookie)
        self.cookie_row = SettingRow("Браузер, из которого читать куки", self.cookie_browser, control_width=W)
        self.browser_path = QLineEdit()
        self.browser_path.setPlaceholderText("необязательно, например /usr/bin/chromium")
        col.addWidget(_group(
            SettingRow("Откуда брать вход в ЯКласс", self.bmode,
                       "Профиль: войдите один раз кнопкой на главной. Куки: берутся из выбранного браузера "
                       "(на Windows с новым Chrome это часто не работает).", control_width=W),
            self.cookie_row,
            SettingRow("Браузер для выполнения", self.channel,
                       "Windows: Microsoft Edge уже есть в системе. Если выбран встроенный Chromium, а его нет, "
                       "приложение предложит его установить.", control_width=W),
            SettingRow("Путь к браузеру", self.browser_path,
                       "Если не находится: укажите Chrome, Chromium или Edge.", control_width=W),
            SettingRow("Показывать окно браузера", self.show_browser,
                       "Скрытый (без окна) режим сайт может отклонять.")))
        # --- приложение
        col.addSpacing(10)
        col.addWidget(section("Приложение"))
        self.remote = Toggle()
        self.auto_poll = Toggle()
        self.poll = _num(5, 1440, 60, 0)
        self.check_updates = Toggle()
        self.auto_download = Toggle()
        self.btn_check_updates = _btn("Проверить обновления", icon="refresh")
        self.btn_check_updates.clicked.connect(self.check_updates_requested.emit)
        self.update_msg = label("", "hint")
        self.theme = QComboBox()
        self.theme.addItem("Тёмная", "dark")
        self.theme.addItem("Светлая", "light")
        upd = QWidget()
        upd.setLayout(row(self.btn_check_updates, self.update_msg, fill=self.update_msg))
        col.addWidget(_group(
            SettingRow("Запуск работ из Telegram", self.remote, "Разрешить боту запускать работы на этом компьютере."),
            SettingRow("Автоматический поиск новых работ", self.auto_poll,
                       "Приложение само будет искать новые работы и сообщать о них в Telegram; на несколько секунд "
                       "открывается окно браузера. Без этого список обновляется кнопкой «Обновить» и по команде "
                       "из Telegram."),
            SettingRow("Период поиска", self.poll, "Проверять работы раз в … минут"),
            SettingRow("Тема", self.theme, control_width=180),
            SettingRow("Узнавать о новых версиях", self.check_updates, "Приложение само проверяет, вышла ли новая версия."),
            SettingRow("Скачивать обновления в фоне", self.auto_download,
                       "Новая версия скачивается и проверяется заранее; устанавливается только по вашей кнопке."),
            upd))
        col.addStretch(1)
        self.err = label("", "muted")
        self.btn_save = _btn("Сохранить", "primary", icon="check")
        self.btn_save.clicked.connect(self._save)
        self.add_footer(row(self.err, self.btn_save, fill=self.err))
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
        self.auto_download.setChecked(s.auto_download_updates)
        self._toggle_own()
        self._toggle_cookie()
        self.err.setText("")

    def collect(self) -> AppSettings:
        s = AppSettings(**{k: getattr(self._base, k) for k in AppSettings.__dataclass_fields__})
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
        s.auto_download_updates = self.auto_download.isChecked()
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
        super().__init__("Журнал", "Что делает приложение прямо сейчас", scroll=False)
        self.view = QPlainTextEdit()
        self.view.setObjectName("console")
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(5000)
        clear = _btn("Очистить", "ghost", icon="trash")
        clear.clicked.connect(self.view.clear)
        copy = _btn("Копировать всё", "ghost", icon="copy")
        copy.clicked.connect(lambda: (self.view.selectAll(), self.view.copy()))
        self.content.addWidget(self.view, 1)
        self.content.addLayout(row(None, copy, clear))

    def add(self, text: str):
        for line in str(text).splitlines() or [""]:
            self.view.appendPlainText(f"{time.strftime('%H:%M:%S')}  {line}" if line.strip() else "")


# ============================================================ История
class HistoryRow(QFrame):
    """Один запуск работы: название, когда и как выполнялась, результат справа."""

    def __init__(self, e: dict):
        super().__init__()
        self.setObjectName("historyRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(16)
        info = QVBoxLayout()
        info.setSpacing(5)
        title = str(e.get("title") or "") or f"Работа {e.get('work_id', '')}"
        info.addWidget(label(title, "workTitle", wrap=True))
        when = time.strftime("%d.%m.%Y %H:%M", time.localtime(e.get("ts", 0)))
        bits = [when]
        if e.get("elapsed_s") is not None:
            bits.append(fmt_duration(e["elapsed_s"]))
        if e.get("style_label"):
            bits.append(str(e["style_label"]))
        info.addWidget(label("  ·  ".join(b for b in bits if b), "hint", wrap=False))
        problems = e.get("problems") or []
        if problems:
            short = "; ".join(f"№{p.get('position')}: {p.get('status')}" for p in problems[:4])
            more = f" и ещё {len(problems) - 4}" if len(problems) > 4 else ""
            hint = label(f"Не выполнено: {short}{more}", "hint", wrap=True)
            hint.setToolTip("\n".join(f"№{p.get('position')} — {p.get('status')}: {p.get('note', '')}" for p in problems))
            info.addWidget(hint)
        if e.get("note") and not e.get("completed") and e.get("mode") == "auto":
            info.addWidget(label(str(e["note"]), "hint", wrap=True))
        lay.addLayout(info, 1)
        right = QVBoxLayout()
        right.setSpacing(4)
        right.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if e.get("percent") is not None and e.get("total"):
            big = label(f"{round(e['percent'])}%", "bigPercent", wrap=False)
            big.setAlignment(Qt.AlignRight)
            right.addWidget(big)
            pts = label(f"{fmt_points(e.get('earned'))}/{fmt_points(e.get('total'))} баллов", "hint", wrap=False)
            pts.setAlignment(Qt.AlignRight)
            right.addWidget(pts)
        elif e.get("est_percent") is not None and e.get("completed"):
            right.addWidget(label(f"~{round(e['est_percent'])}%", "bigPercent", wrap=False))
        status = ("Без отправки" if e.get("mode") == "dry" else
                  "Остановлена" if e.get("stopped") else
                  "Завершена" if e.get("completed") else "Не завершена")
        tone = "" if e.get("mode") == "dry" else ("ok" if e.get("completed") else "warn")
        pill = Pill(status, tone)
        right.addWidget(pill, 0, Qt.AlignRight)
        lay.addLayout(right)


class HistoryPage(Page):
    clear_requested = Signal()

    def __init__(self):
        super().__init__("История", "Выполненные работы на этом компьютере")
        self.count = label("", "muted")
        self.btn_clear = _btn("Очистить", "ghost", icon="trash")
        self.btn_clear.clicked.connect(self._clear)
        self.content.addLayout(row(self.count, self.btn_clear, fill=self.count))
        self.list_host = QWidget()
        self.list = QVBoxLayout(self.list_host)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(10)
        self.empty = label("Здесь появятся результаты: название работы, время, процент и баллы.", "empty")
        self.empty.setAlignment(Qt.AlignCenter)
        self.list.addWidget(self.empty)
        self.list.addStretch(1)
        self.content.addWidget(self.list_host, 1)

    def set_items(self, items: list[dict]) -> None:
        while self.list.count() > 2:
            w = self.list.takeAt(1).widget()
            if w:
                w.deleteLater()
        for i, e in enumerate(items):
            self.list.insertWidget(1 + i, HistoryRow(e))
        self.empty.setVisible(not items)
        self.btn_clear.setVisible(bool(items))
        done = [e for e in items if e.get("percent") is not None]
        avg = f", средний результат {round(sum(e['percent'] for e in done) / len(done))}%" if done else ""
        self.count.setText(f"Запусков: {len(items)}{avg}" if items else "")

    def _clear(self) -> None:
        if QMessageBox.question(self, "Очистить историю?", "Записи о выполненных работах будут удалены с этого "
                                "компьютера. Продолжить?", QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) == QMessageBox.Yes:
            self.clear_requested.emit()


__all__ = ["HomePage", "WorksPage", "HistoryPage", "SettingsPage", "LogPage", "RunDialog", "WorkCard", "release_notes_text",
           "greeting", "form_row", "icon_label"]
