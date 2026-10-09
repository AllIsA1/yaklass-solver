"""Уведомления о новой версии: сервер релизов подменён локальным."""
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

import yaklass_app
from yaklass_app import updater
from yaklass_app.agent import Agent
from yaklass_app.settings import AppSettings, Store

from .conftest import FakeBackend, wait_for

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeGithub:
    """Подмена api.github.com: /repos/<repo>/releases/latest."""

    def __init__(self, tag="v9.9.9", status=200, body="Что нового:\n- исправлено A\n- добавлено B"):
        self.tag, self.status, self.body = tag, status, body
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "ua": self.headers.get("User-Agent")})
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if outer.status == 200:
                    self.wfile.write(json.dumps({"tag_name": outer.tag, "html_url": f"https://github.com/x/y/releases/tag/{outer.tag}",
                                                 "body": outer.body}).encode())
            def log_message(self, *a): pass
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.api = f"http://127.0.0.1:{self.srv.server_port}"

    def stop(self):
        self.srv.shutdown()


@pytest.fixture
def gh():
    g = FakeGithub()
    yield g
    g.stop()


def test_version_in_package_matches_pyproject():
    toml = (Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "([^"]+)"', toml, re.M).group(1) == yaklass_app.__version__


@pytest.mark.parametrize("a,b,newer", [("v0.1.8", "0.1.7", True), ("0.1.10", "0.1.9", True), ("1.0.0", "0.9.99", True),
                                       ("0.1.7", "0.1.7", False), ("v0.1.6", "0.1.7", False), ("0.2.0-rc1", "0.1.9", True),
                                       ("мусор", "0.1.0", False)])
def test_version_comparison(a, b, newer):
    assert updater.is_newer(a, b) is newer


def test_fetch_latest_parses_release(gh):
    rel = updater.fetch_latest("x/y", api=gh.api)
    assert rel.tag == "v9.9.9" and rel.version == "9.9.9" and rel.url.endswith("/v9.9.9") and "исправлено A" in rel.notes
    assert gh.requests[0]["path"] == "/repos/x/y/releases/latest" and gh.requests[0]["auth"] is None


def test_token_is_sent_only_when_given(gh):
    updater.fetch_latest("x/y", token="ghp_test", api=gh.api)
    assert gh.requests[-1]["auth"] == "Bearer ghp_test"


def test_check_reports_newer_or_not(gh):
    rel, newer = updater.check("x/y", api=gh.api, current="0.1.7")
    assert newer and rel.version == "9.9.9"
    gh.tag = "v0.1.7"
    assert updater.check("x/y", api=gh.api, current="0.1.7")[1] is False


@pytest.mark.parametrize("status,text", [(404, "приватный"), (403, "отклонил"), (500, "500")])
def test_errors_are_explained(gh, status, text):
    gh.status = status
    with pytest.raises(updater.UpdateError, match=text):
        updater.fetch_latest("x/y", api=gh.api)


def test_no_connection_is_an_update_error():
    with pytest.raises(updater.UpdateError, match="Нет связи"):
        updater.fetch_latest("x/y", api="http://127.0.0.1:1")


# ---------- агент ----------

@pytest.fixture
def agent_with_gh(tmp_path, gh, monkeypatch):
    store = Store(tmp_path / "c", use_keyring=False)
    store.save(AppSettings())
    events = []
    agent = Agent(store, FakeBackend(), emit=lambda n, p: events.append((n, p)), directory=tmp_path / "d")
    agent.update_api = gh.api
    return agent, events, gh


def test_agent_emits_update_available(agent_with_gh):
    agent, events, gh = agent_with_gh
    agent.check_updates()
    ev = [p for n, p in events if n == "update_available"]
    assert len(ev) == 1 and ev[0]["version"] == "9.9.9" and ev[0]["current"] == yaklass_app.__version__
    assert ev[0]["url"].endswith("/v9.9.9") and "исправлено A" in ev[0]["notes"]


def test_automatic_check_is_silent_on_errors_manual_is_not(agent_with_gh):
    agent, events, gh = agent_with_gh
    gh.status = 404
    agent.check_updates()
    assert not [n for n, p in events if n in ("update_status", "update_available")]
    agent.check_updates(manual=True)
    st = [p for n, p in events if n == "update_status"]
    assert len(st) == 1 and st[0]["ok"] is False and "приватный" in st[0]["message"]
    gh.status, gh.tag = 200, f"v{yaklass_app.__version__}"
    agent.check_updates(manual=True)
    assert [p for n, p in events if n == "update_status"][-1]["ok"] is True


def test_up_to_date_manual_check_says_so(agent_with_gh):
    agent, events, gh = agent_with_gh
    gh.tag = f"v{yaklass_app.__version__}"
    agent.check_updates(manual=True)
    assert "последняя версия" in [p for n, p in events if n == "update_status"][-1]["message"]
    assert not [n for n, p in events if n == "update_available"]


def test_check_updates_setting_default_and_roundtrip(tmp_path):
    assert AppSettings().check_updates is True and AppSettings().update_repo == "AllIsA1/yaklass-solver"
    st = Store(tmp_path, use_keyring=False)
    s = AppSettings(check_updates=False)
    st.save(s)
    assert st.load().check_updates is False


# ---------- интерфейс ----------

def test_gui_shows_update_card_and_opens_only_https(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from yaklass_app.gui.main_window import MainWindow
    from yaklass_app.gui.widgets import Bridge
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / "c", use_keyring=False)
    store.save(AppSettings())
    bridge = Bridge()
    agent = Agent(store, FakeBackend(), emit=lambda n, p: bridge.event.emit(n, p), directory=tmp_path / "d")
    win = MainWindow(agent, store, bridge)
    win.show()
    assert win.home.upd.isHidden()
    bridge.event.emit("update_available", {"version": "9.9.9", "current": "0.1.7", "url": "https://github.com/x/y/releases/tag/v9.9.9",
                                           "notes": "строка1\n\nстрока2"})
    app.processEvents()
    assert not win.home.upd.isHidden() and "9.9.9" in win.home.upd_text.text() and "строка2" in win.home.upd_notes.text()
    opened = []
    import yaklass_app.gui.main_window as mw
    monkeypatch.setattr(mw.QDesktopServices, "openUrl", staticmethod(lambda u: opened.append(u.toString())))
    win.home.btn_upd_open.click()
    assert opened == ["https://github.com/x/y/releases/tag/v9.9.9"]
    win.home.upd._upd_url = ""
    win.home.open_url_requested.emit("file:///etc/passwd")               # не https — не открываем
    win.home.open_url_requested.emit("javascript:alert(1)")
    assert opened == ["https://github.com/x/y/releases/tag/v9.9.9"]
    win.home.btn_upd_hide.click()
    assert win.home.upd.isHidden()
    bridge.event.emit("update_status", {"ok": False, "message": "Нет связи с GitHub"})
    app.processEvents()
    assert "Нет связи" in win.settings.update_msg.text()
    agent._stop.set()
