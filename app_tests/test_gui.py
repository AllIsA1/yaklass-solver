"""Тесты интерфейса в виртуальном режиме Qt (без экрана)."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from yaklass_app.agent import Agent  # noqa: E402
from yaklass_app.gui.main_window import MainWindow  # noqa: E402
from yaklass_app.gui.widgets import Bridge  # noqa: E402
from yaklass_app.settings import AppSettings, Store  # noqa: E402

from .conftest import FakeBackend, RealServer  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


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


def test_navigation_between_pages(ui):
    for i in range(4):
        ui.win.nav.button(i).click()
        assert ui.win.stack.currentIndex() == i


def test_unlinked_state_shows_link_form(ui):
    h = ui.win.home
    assert not h.link_row.isHidden() and h.btn_unlink.isHidden()
    assert "Не привязано" in h.tg_status.text()


def test_settings_roundtrip_through_widgets(ui):
    p = ui.win.settings
    p.server.setText("https://solver.example.com/")
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
    assert s.server_url == "https://solver.example.com"                  # лишний «/» убран
    assert s.solver_mode == "own" and s.own_api["model"] == "deepseek-chat" and s.own_api["vision"] is False
    assert s.pace_obj().between_tasks == (4.0, 9.0) and s.theme == "light"
    assert ui.store.own_api_key == "sk-secret" and "sk-secret" not in (ui.store.path.read_text())
    assert p.api_key.text() == "" and "сохранён" in p.api_key.placeholderText()     # ключ не остаётся в поле
    assert "Сохранено" in p.err.text()


def test_settings_validation_blocks_bad_input(ui):
    p = ui.win.settings
    p.server.setText("ftp://nope")
    p.btn_save.click()
    assert "https://" in p.err.text() and ui.store.load().server_url == "http://127.0.0.1:1"


def test_own_api_fields_visible_only_in_own_mode(ui):
    p = ui.win.settings
    p.mode_server.setChecked(True)
    assert p.own.isHidden()
    p.mode_own.setChecked(True)
    assert not p.own.isHidden()


def test_start_requires_confirmation(ui, monkeypatch):
    got, warned = [], []
    ui.win.works.start_requested.connect(lambda wid, mode: got.append((wid, mode)))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    ui.win.works._run("111", "Тема")
    assert got == [] and warned == []                                   # отказ — ничего не запущено
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    ui.win.works._run("111", "Тема")
    ui.win.works._dry("111")                                            # заполнение без отправки — без подтверждения
    assert got == [("111", "auto"), ("111", "dry")]
    assert len(warned) == 2 and "не привязано" in warned[0]             # приложение не привязано -> понятное предупреждение
    assert not ui.backend.runs


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
    ui.win.nav.button(2).click()
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

def test_saving_settings_reconnects_only_when_server_changes(ui):
    calls = []
    ui.agent.reconnect = lambda: calls.append(1)
    p = ui.win.settings
    p.theme.setCurrentIndex(p.theme.findData("light"))
    p.p_between[0].setText("2")
    p.btn_save.click()
    assert calls == []                                                   # тема/паузы — соединение не трогаем
    p.server.setText("https://another.example.com")
    p.btn_save.click()
    assert calls == [1]


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
