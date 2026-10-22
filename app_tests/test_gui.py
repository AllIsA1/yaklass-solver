"""Тесты интерфейса в виртуальном режиме Qt (без экрана)."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from yaklass_app.agent import Agent  # noqa: E402
from yaklass_app.gui.main_window import MainWindow  # noqa: E402
from yaklass_app.gui.pages import RunDialog  # noqa: E402
from yaklass_app.gui.widgets import Bridge  # noqa: E402
from yaklass_app.settings import AppSettings, Store  # noqa: E402

from .conftest import FakeBackend, RealServer  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    for w in app.topLevelWidgets():              # убрать окна до завершения процесса: иначе Qt может упасть на выходе
        w.close()
        w.deleteLater()
    app.processEvents()


def pump(app, cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def ui(qapp, tmp_path):
    store = Store(tmp_path / "cfg", use_keyring=False)
    store.save(AppSettings(server_url="http://127.0.0.1:1"))
    bridge = Bridge()
    backend = FakeBackend()
    agent = Agent(store, backend, emit=lambda n, p: bridge.event.emit(n, p), directory=tmp_path / "d")
    win = MainWindow(agent, store, bridge)
    win.show()
    yield type("U", (), dict(app=qapp, win=win, store=store, agent=agent, backend=backend, bridge=bridge, tmp=tmp_path))
    agent._stop.set()
    win.hide()
    win.deleteLater()                          # без этого Qt падает при завершении процесса (двойное удаление)
    qapp.processEvents()


def test_navigation_between_pages(ui):
    for i in range(5):
        ui.win.nav.button(i).click()
        assert ui.win.stack.currentIndex() == i


def test_unlinked_state_shows_link_form(ui):
    h = ui.win.home
    assert not h.link_row.isHidden() and h.btn_unlink.isHidden()
    assert "Не привязано" in h.tg_status.text()


def test_settings_roundtrip_through_widgets(ui):
    p = ui.win.settings
    p.mode_own.setChecked(True)
    p.api_url.setText("https://api.deepseek.com/v1")
    p.api_model.setText("deepseek-chat")
    p.api_key.setText("sk-secret")
    p.api_vision.setChecked(False)
    p.p_between[0].setValue(4)
    p.p_between[1].setValue(9)
    p.theme.setCurrentIndex(p.theme.findData("light"))
    p.btn_save.click()
    s = ui.store.load()
    assert s.server_url == "http://127.0.0.1:1"                          # адрес сервера в настройках не трогается
    assert s.solver_mode == "own" and s.own_api["model"] == "deepseek-chat" and s.own_api["vision"] is False
    assert s.pace_obj().between_tasks == (4.0, 9.0) and s.theme == "light"
    assert ui.store.own_api_key == "sk-secret" and "sk-secret" not in (ui.store.path.read_text())
    assert p.api_key.text() == "" and "сохранён" in p.api_key.placeholderText()     # ключ не остаётся в поле
    assert "Сохранено" in p.err.text()


def test_settings_have_no_server_address_and_no_chromium_button(ui):
    p = ui.win.settings
    assert not hasattr(p, "server") and not hasattr(p, "btn_chromium")
    from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton
    texts = [w.text() for w in p.findChildren(QLabel)] + [w.text() for w in p.findChildren(QPushButton)]
    assert not any("Адрес сервера" in t or "Скачать Chromium" in t or "рекомендуется" in t for t in texts)
    assert not any("127.0.0.1" in e.text() for e in ui.win.findChildren(QLineEdit))      # домен нигде не показан


def test_settings_texts(ui):
    from PySide6.QtWidgets import QLabel
    texts = [w.text() for w in ui.win.settings.findChildren(QLabel)]
    assert "Автоматический поиск новых работ" in texts and "Проверять работы раз в … минут" in texts
    assert texts.count("Случайно, от … до … секунд") == 3 and "Случайно, от … до … миллисекунд" in texts
    assert not any("Сам проверять" in t or "не чаще раза" in t for t in texts)
    assert ui.win.settings.bmode.itemText(0) == "Собственный профиль приложения"


def test_chromium_offer_only_when_embedded_chromium_is_chosen_and_missing(ui, monkeypatch):
    from yaklass_app.gui import main_window as mw
    w = ui.win
    shown = []
    monkeypatch.setattr(w, "can_prompt", lambda: True)
    monkeypatch.setattr(mw.QMessageBox, "exec", lambda self: shown.append(self.text()) or 0)
    monkeypatch.setattr(mw, "chromium_installed", lambda: True)
    assert w.offer_chromium() is True and not shown                      # установлен — тихо
    monkeypatch.setattr(mw, "chromium_installed", lambda: False)
    s = ui.store.load(); s.channel = "chrome"; ui.store.save(s)
    assert w.offer_chromium() is True and not shown                      # выбран Chrome — Chromium не нужен
    s.channel = ""; ui.store.save(s)
    assert w.offer_chromium() is False and "не установлен" in shown[0]   # выбран встроенный, а его нет — предложение
    s.browser_path = "/usr/bin/chromium"; ui.store.save(s)
    assert w.offer_chromium() is True                                    # свой путь к браузеру — не предлагаем


def test_start_is_blocked_until_chromium_is_installed(ui, monkeypatch):
    from yaklass_app.gui import main_window as mw
    w = ui.win
    s = ui.store.load()
    s.channel = ""                                       # по умолчанию на машине с Chrome канал «chrome» (в CI он есть)
    ui.store.save(s)
    warned = []
    monkeypatch.setattr(w, "can_prompt", lambda: True)
    monkeypatch.setattr(mw, "chromium_installed", lambda: False)
    monkeypatch.setattr(mw.QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(mw.QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a)))   # модальное окно зависло бы
    w.start_work("111", "auto")
    assert not warned                                    # остановились на предложении установить Chromium
    assert not ui.backend.runs and ui.agent.state == "idle"


def test_own_api_fields_visible_only_in_own_mode(ui):
    p = ui.win.settings
    p.mode_server.setChecked(True)
    assert p.own.isHidden()
    p.mode_own.setChecked(True)
    assert not p.own.isHidden()


def test_start_requires_confirmation(ui, monkeypatch):
    got, warned = [], []
    ui.win.works.start_requested.connect(lambda wid, mode, style: got.append((wid, mode, style.kind)))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    monkeypatch.setattr(RunDialog, "exec", lambda self: QDialog.Rejected)
    ui.win.works._run("111", "Тема")
    assert got == [] and warned == []                                   # отказ — ничего не запущено
    monkeypatch.setattr(RunDialog, "exec", lambda self: QDialog.Accepted)
    ui.win.works._run("111", "Тема")
    ui.win.works._dry("111")                                            # заполнение без отправки — без подтверждения
    assert got == [("111", "auto", "normal"), ("111", "dry", "normal")]
    assert len(warned) == 2 and "не привязано" in warned[0]             # приложение не привязано -> понятное предупреждение
    assert not ui.backend.runs


def test_home_cards_are_not_squeezed_in_a_short_window(qapp):
    """Раньше при невысоком окне карточки сжимались, и строки/кнопки обрезались."""
    from yaklass_app.gui.pages import HomePage, release_notes_text
    from yaklass_app.gui.theme import stylesheet
    qapp.setStyleSheet(stylesheet("dark"))
    p = HomePage()
    p.show_update("0.1.2", "0.1.1", "## What's Changed\n* **Режимы**\n\n**Full Changelog**: https://x", "u", True)
    p.set_linked(True, "evan", True)
    p.resize(760, 380)
    p.show()
    qapp.processEvents()
    for card in (p.upd, p.tg, p.yk, p.run):
        lay = card.layout()
        need = lay.heightForWidth(card.width()) if lay.hasHeightForWidth() else lay.sizeHint().height()
        assert card.height() >= need, (card.height(), need)       # хватает места под перенос строк
    assert release_notes_text("## What's Changed\n* **Режимы** выполнения\n- второе\n\n**Full Changelog**: https://x") \
        == "• Режимы выполнения\n• второе"
    assert release_notes_text("**A**\n\n`b`") == "A\nb"
    template = "Изменения:\n\n* Одно\n* Второе\n* Третье\n"                  # шаблон описаний релизов
    assert release_notes_text(template) == "• Одно\n• Второе\n• Третье"
    assert release_notes_text("## Изменения\n- Одно") == "• Одно" and release_notes_text("ИЗМЕНЕНИЯ :\n* А") == "• А"
    assert release_notes_text("Изменения в разметке страницы\n* Одно") == "Изменения в разметке страницы\n• Одно"
    p.hide()
    p.deleteLater()
    qapp.processEvents()


def test_short_status_texts_stay_on_one_line(ui):
    """Подписи рядом с кнопкой раньше сжимались до минимальной ширины и ломались на 2–3 строки."""
    def one_line(lb):
        ui.app.processEvents()
        need = lb.heightForWidth(lb.width())              # высота подписи — по её ширине, а не по строке с кнопкой
        assert need < 1.5 * lb.fontMetrics().lineSpacing(), (lb.text(), lb.width(), need)
    ui.win.nav.button(1).click()
    ui.win.works.set_works([])
    ui.win.works.set_message("Новых работ нет или нужен вход в ЯКласс")
    one_line(ui.win.works.status)
    ui.win.nav.button(3).click()
    ui.win.settings.update_msg.setText("✓ Вы используете последнюю версию (0.1.3)")
    one_line(ui.win.settings.update_msg)


def test_chips_and_version_are_capitalized_and_aligned(ui):
    from PySide6.QtWidgets import QLabel
    from yaklass_app.gui.pages import WorkCard
    card = WorkCard({"id": "1", "subject": "биология", "title": "Т"}, True, lambda *a: None, lambda *a: None)
    assert [w.text() for w in card.findChildren(QLabel) if w.objectName() in ("chip", "chipAccent")] == ["Биология", "Новая"]
    card.deleteLater()
    ver = next(w for w in ui.win.findChildren(QLabel) if w.objectName() == "version")
    assert ver.text().startswith("Версия ")


def test_events_update_widgets(ui):
    def ev(n, **p):
        ui.bridge.event.emit(n, p)
        ui.app.processEvents()
    ev("works", works=[{"id": "1", "subject": "Алгебра", "title": "Т1", "deadline": ""},
                       {"id": "2", "subject": "Геометрия", "title": "Т2", "deadline": "12.10 14:09"}], new_ids=["2"])
    assert "Найдено работ: 2" in ui.win.works.status.text() and "Нужно войти" not in ui.win.home.yk_status.text()
    ev("state", state="running", work_id="2")
    ev("progress", work_id="2", done=3, total=9, note="задание 3")
    h = ui.win.home
    assert "Выполняется" in h.run_state.text() and h.bar.value() == 3 and h.bar.maximum() == 9
    assert not h.btn_pause.isHidden() and h.btn_resume.isHidden()
    ev("state", state="paused", work_id="2")
    assert h.btn_pause.isHidden() and not h.btn_resume.isHidden()
    ev("finished", work_id="2", completed=True, summary={"новых ответов": 9}, note="")
    assert "завершена" in h.run_text.text() and "новых ответов: 9" in h.run_text.text()
    ev("state", state="idle")
    assert h.btn_stop.isHidden()
    ev("login_required")
    assert "Нужно войти" in h.yk_status.text()
    ev("log", text="строка\nвторая")
    assert "вторая" in ui.win.log.view.toPlainText()


def test_link_through_gui_with_real_server(qapp, tmp_path):
    srv = RealServer()
    try:
        store = Store(tmp_path / "cfg", use_keyring=False)
        store.save(AppSettings(server_url=srv.url))
        bridge = Bridge()
        agent = Agent(store, FakeBackend(), emit=lambda n, p: bridge.event.emit(n, p), directory=tmp_path / "d")
        win = MainWindow(agent, store, bridge)
        win.show()
        agent.start()
        win.home.code.setText(srv.new_code(5, "carol").lower())          # регистр не важен
        win.home.btn_link.click()
        assert pump(qapp, lambda: store.token.startswith("yks_")), "токен не получен"
        assert pump(qapp, lambda: agent.connected)
        assert pump(qapp, lambda: "carol" in win.home.tg_status.text() and "Подключено" in win.home.tg_status.text())
        assert win.home.link_row.isHidden() and not win.home.btn_unlink.isHidden()
        assert pump(qapp, lambda: "из 100" in win.home.tg_info.text())   # квота подтянулась с сервера
        win.home.btn_unlink.click()
        assert pump(qapp, lambda: store.token == "" and not win.home.link_row.isHidden())
        agent.shutdown()
    finally:
        srv.stop()


def test_wrong_link_code_shows_error(ui, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[2])))
    ui.win.home.code.setText("AAAA-BBBB")
    ui.win.home.btn_link.click()
    assert pump(ui.app, lambda: shown), "ошибка не показана"
    assert "недоступен" in shown[0] and ui.win.home.btn_link.isEnabled()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_both_themes_render(ui, theme):
    s = ui.store.load()
    s.theme = theme
    ui.store.save(s)
    ui.win.load_settings()
    img = ui.win.grab()
    assert img.width() > 800 and not img.isNull()


# ---------- числовые поля ----------

def test_numeric_fields_show_values_inside_the_box(ui):
    """Регрессия: число отображалось слева за границей поля (QSpinBox + стили)."""
    p = ui.win.settings
    ui.win.nav.button(3).click()
    ui.app.processEvents()
    for f, txt in ((p.p_between[0], "1"), (p.p_between[1], "3"), (p.p_submit[0], "0.5"), (p.p_field[0], "0.25"),
                   (p.p_type[0], "40"), (p.poll, "60")):
        assert f.text() == txt
        assert f.width() >= 90 and f.fontMetrics().horizontalAdvance(f.text()) < f.width() - 24   # поместилось с отступами
        assert f.alignment() & Qt.AlignLeft


def test_numeric_field_parsing_and_clamping(ui):
    from yaklass_app.gui.widgets import NumField
    f = NumField(0, 10, 1, 1)
    f.setText("1,5"); assert f.value() == 1.5                 # запятая как у русской раскладки
    f.setText("99"); assert f.value() == 10                   # вне диапазона — в границы
    f.setText(""); assert f.value() == 0
    f.setText("abc"); assert f.value() == 0
    n = NumField(1, 1440, 15, 0)
    n.setText("7.6"); assert n.value() == 8 and isinstance(n.value(), int)
    n.setValue(2000); assert n.text() == "1440"


def test_pace_values_survive_save_and_reload(ui):
    p = ui.win.settings
    p.p_field[0].setText("0,3"); p.p_field[1].setText("1.25"); p.p_type[0].setText("55")
    p.btn_save.click()
    q = ui.store.load().pace_obj()
    assert q.field_pause == (0.3, 1.25) and q.typing_ms[0] == 55
    p.load(ui.store.load(), False)
    assert p.p_field[0].text() == "0.3" and p.p_field[1].text() == "1.25"


# ---------- сохранение настроек не рвёт соединение ----------

def test_saving_settings_never_reconnects_or_changes_server_address(ui):
    calls = []
    ui.agent.reconnect = lambda: calls.append(1)
    p = ui.win.settings
    p.theme.setCurrentIndex(p.theme.findData("light"))
    p.p_between[0].setText("2")
    p.btn_save.click()
    assert calls == []                                                   # тема/паузы — соединение не трогаем
    s = ui.store.load()
    s.server_url = "https://linked.example.com"                          # адрес сохранила привязка (форма настроек устарела)
    ui.store.save(s)
    p.btn_save.click()
    assert calls == [] and ui.store.load().server_url == "https://linked.example.com"   # сохранение не затирает адрес


def test_real_connection_survives_settings_save(qapp, tmp_path):
    srv = RealServer()
    try:
        store = Store(tmp_path / "cfg", use_keyring=False)
        store.save(AppSettings(server_url=srv.url))
        bridge = Bridge()
        agent = Agent(store, FakeBackend(), emit=lambda n, p: bridge.event.emit(n, p), directory=tmp_path / "d")
        win = MainWindow(agent, store, bridge)
        win.show()
        agent.link(srv.new_code())
        agent.start()
        assert pump(qapp, lambda: agent.connected and srv.events_of("hello"))
        hello_before = len(srv.events_of("hello"))
        win.settings.p_field[0].setText("0.4")
        win.settings.btn_save.click()
        pump(qapp, lambda: False, timeout=2.5)                           # время на возможное переподключение
        assert agent.connected and srv.hub.online(1)
        assert len(srv.events_of("hello")) == hello_before == 1         # «hello» не повторялся: бот не получит второго сообщения
        agent.shutdown()
    finally:
        srv.stop()


def test_home_login_widget_states_and_mode_label(ui):
    h = ui.win.home
    s = ui.store.load()
    s.browser_mode, s.cookie_browser = "cookies", "firefox"
    ui.store.save(s)
    ui.win.load_settings()
    assert h.btn_login.text() == "Проверить вход" and "firefox" in h.yk_info.text()
    ui.win.do_login()                                                    # сразу видно, что проверка идёт
    assert "Проверяю" in h.yk_status.text() and not h.btn_login.isEnabled()
    pump(ui.app, lambda: "Вход выполнен" in h.yk_status.text() or "Нужно" in h.yk_status.text(), 6)
    assert h.btn_login.isEnabled() and "Не проверено" not in h.yk_status.text()
    ui.bridge.event.emit("login_status", {"ok": None, "message": "Не найден браузер для выполнения."})
    ui.app.processEvents()
    assert "Не удалось" in h.yk_status.text() and "Не найден браузер" in h.yk_info.text()
    s.browser_mode = "profile"
    ui.store.save(s)
    ui.win.load_settings()
    assert h.btn_login.text() == "Войти в ЯКласс"


def test_browser_path_setting_roundtrip(ui):
    p = ui.win.settings
    p.browser_path.setText("  /usr/bin/chromium ")
    p.btn_save.click()
    assert ui.store.load().browser_path == "/usr/bin/chromium"
    p.load(ui.store.load(), False)
    assert p.browser_path.text() == "/usr/bin/chromium"


def test_auto_poll_checkbox_roundtrip_and_default(ui):
    p = ui.win.settings
    assert not p.auto_poll.isChecked()                                   # по умолчанию выключено
    p.auto_poll.setChecked(True)
    p.poll.setText("30")
    p.btn_save.click()
    s = ui.store.load()
    assert s.auto_poll is True and s.poll_minutes == 30
    p.poll.setText("2")                                                  # слишком часто — поле само поднимает до минимума
    p.btn_save.click()
    assert ui.store.load().poll_minutes == 5
