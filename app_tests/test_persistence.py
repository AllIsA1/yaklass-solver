"""Перезапуск приложения: привязка (токен), ключ своего API и настройки должны сохраняться."""
import json
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import keyring  # noqa: E402

from yaklass_app.settings import AppSettings, SecretStore, Store  # noqa: E402


class MemKeyring:
    """Подставная связка ключей. mode: ok | set_raises | set_drops | get_raises."""

    def __init__(self, mode="ok"):
        self.mode, self.data = mode, {}

    def install(self, monkeypatch):
        monkeypatch.setattr(keyring, "set_password", self.set_password)
        monkeypatch.setattr(keyring, "get_password", self.get_password)
        monkeypatch.setattr(keyring, "delete_password", self.delete_password)
        monkeypatch.setattr(SecretStore, "_keyring_ok", staticmethod(lambda: True))
        return self

    def set_password(self, svc, name, value):
        if self.mode == "set_raises":
            raise RuntimeError("Failed to unlock the collection")
        if self.mode != "set_drops":
            self.data[(svc, name)] = value

    def get_password(self, svc, name):
        if self.mode == "get_raises":
            raise RuntimeError("dbus недоступен")
        return self.data.get((svc, name))

    def delete_password(self, svc, name):
        if (svc, name) not in self.data:
            raise keyring.errors.PasswordDeleteError("нет такого")
        del self.data[(svc, name)]


@pytest.mark.parametrize("mode", ["set_raises", "set_drops", "get_raises"])
def test_token_survives_restart_when_keyring_misbehaves(tmp_path, monkeypatch, mode):
    """Реальная ошибка: SecretService при записи отказал (токен ушёл в файл), а при чтении отвечал «пусто»
    без ошибки — файл не читался, и после перезапуска приложение было «не привязано»."""
    MemKeyring(mode).install(monkeypatch)
    Store(tmp_path).token = "yks_secret"                              # первый запуск: привязка
    assert Store(tmp_path).token == "yks_secret"                      # второй запуск (новый объект, как после перезапуска)
    Store(tmp_path).own_api_key = "sk-key"
    assert Store(tmp_path).own_api_key == "sk-key"


def test_working_keyring_is_used_and_file_stays_clean(tmp_path, monkeypatch):
    kr = MemKeyring("ok").install(monkeypatch)
    Store(tmp_path).token = "yks_a"
    assert Store(tmp_path).token == "yks_a" and kr.data[("yaklass-solver", "agent-token")] == "yks_a"
    assert not (tmp_path / "secrets.json").exists() or "agent-token" not in json.loads((tmp_path / "secrets.json").read_text())


def test_clearing_removes_value_from_both_places(tmp_path, monkeypatch):
    kr = MemKeyring("ok").install(monkeypatch)
    (tmp_path / "secrets.json").write_text(json.dumps({"agent-token": "stale"}))          # остаток от прошлого сбоя
    kr.data[("yaklass-solver", "agent-token")] = "fresh"
    st = Store(tmp_path)
    assert st.token == "fresh"                                         # связка ключей важнее файла
    st.token = ""
    assert Store(tmp_path).token == "" and "agent-token" not in json.loads((tmp_path / "secrets.json").read_text())


def test_value_moves_from_file_to_keyring_when_it_starts_working(tmp_path, monkeypatch):
    (tmp_path / "secrets.json").write_text(json.dumps({"agent-token": "old"}))
    kr = MemKeyring("ok").install(monkeypatch)
    st = Store(tmp_path)
    assert st.token == "old"                                           # прочитали из файла
    st.token = "new"                                                   # записали — теперь в связке ключей, из файла убрано
    assert kr.data[("yaklass-solver", "agent-token")] == "new"
    assert "agent-token" not in json.loads((tmp_path / "secrets.json").read_text())


def test_secrets_file_stays_private(tmp_path, monkeypatch):
    MemKeyring("set_raises").install(monkeypatch)
    Store(tmp_path).token = "yks_x"
    if os.name != "nt":
        assert oct((tmp_path / "secrets.json").stat().st_mode & 0o777) == "0o600"


def test_whole_app_state_restored_after_restart(tmp_path, monkeypatch):
    """Сквозной сценарий через окно: меняем настройки, «привязываем», закрываем, открываем заново."""
    from PySide6.QtWidgets import QApplication

    from yaklass_app.agent import Agent
    from yaklass_app.gui.main_window import MainWindow
    from yaklass_app.gui.widgets import Bridge

    from .conftest import FakeBackend
    MemKeyring("set_raises").install(monkeypatch)                      # самый неприятный вариант связки ключей
    app = QApplication.instance() or QApplication([])

    def open_window():
        store = Store(tmp_path / "cfg")
        bridge = Bridge()
        agent = Agent(store, FakeBackend(), emit=lambda n, p: bridge.event.emit(n, p), directory=tmp_path / "d")
        return MainWindow(agent, store, bridge), store, agent

    w1, st1, a1 = open_window()
    w1.settings.server.setText("https://solver.example.com")
    w1.settings.mode_own.setChecked(True)
    w1.settings.api_model.setText("deepseek-chat")
    w1.settings.api_key.setText("sk-own")
    w1.settings.p_between[0].setText("5")
    w1.settings.poll.setText("45")
    w1.settings.btn_save.click()
    st1.token = "yks_linked"                                           # как после успешной привязки
    a1._stop.set(); w1.hide(); app.processEvents()
    del w1, a1, st1                                                    # «закрыли приложение»

    w2, st2, a2 = open_window()                                        # «открыли заново»
    s = w2.settings
    assert s.server.text() == "https://solver.example.com" and s.mode_own.isChecked()
    assert s.api_model.text() == "deepseek-chat" and s.p_between[0].text() == "5" and s.poll.text() == "45"
    assert "сохранён" in s.api_key.placeholderText()                   # ключ API найден
    assert st2.token == "yks_linked" and w2.home.link_row.isHidden() and not w2.home.btn_unlink.isHidden()
    a2._stop.set()
