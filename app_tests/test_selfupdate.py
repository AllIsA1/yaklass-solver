"""Самообновление: настоящие файлы, фейковый сервер релизов, проверка контрольных сумм, замена и откат."""
import hashlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from yaklass_app import updater
from yaklass_app.agent import Agent
from yaklass_app.settings import AppSettings, Store

from .conftest import FakeBackend

NEW = b"#!/bin/sh\necho NEW-VERSION\n" + os.urandom(2000)
OLD = b"#!/bin/sh\necho OLD-VERSION\n"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Releases:
    """Фейковый GitHub: API релиза + скачивание файлов (с проверкой токена для API-адресов)."""

    def __init__(self, payload=NEW, digest="auto", sums=None, token_required=False):
        self.payload, self.token_required = payload, token_required
        self.sums_text = sums
        self.hits: list[tuple[str, str | None]] = []
        outer = self
        self.digest = sha(payload) if digest == "auto" else digest

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.outer_hit()
            def outer_hit(self):
                auth = self.headers.get("Authorization")
                outer.hits.append((self.path, auth))
                if self.path.endswith("/releases/latest"):
                    return self.json(outer.release_json())
                if self.path.startswith("/api-asset/"):
                    if outer.token_required and auth != "Bearer tok123":
                        return self.send_error(404)
                    return self.blob(outer.payload if self.path.endswith("/1") else (outer.sums_text or "").encode())
                if self.path == "/dl/YaklassSolver-x86_64.AppImage" and not outer.token_required:
                    return self.blob(outer.payload)
                if self.path == "/dl/SHA256SUMS.txt":
                    return self.blob((outer.sums_text or "").encode())
                self.send_error(404)
            def json(self, d):
                b = json.dumps(d).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
            def blob(self, b):
                self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
            def log_message(self, *a): pass
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"

    def release_json(self):
        a = {"name": "YaklassSolver-x86_64.AppImage", "id": 1, "size": len(self.payload), "url": f"{self.base}/api-asset/1",
             "browser_download_url": f"{self.base}/dl/YaklassSolver-x86_64.AppImage"}
        if self.digest:
            a["digest"] = f"sha256:{self.digest}"
        assets = [a, {"name": "YaklassSolver.exe", "id": 3, "size": 5, "url": f"{self.base}/api-asset/3",
                      "browser_download_url": f"{self.base}/dl/YaklassSolver.exe", "digest": "sha256:" + sha(b"exe!!")}]
        if self.sums_text is not None:
            assets.append({"name": "SHA256SUMS.txt", "id": 2, "size": len(self.sums_text), "url": f"{self.base}/api-asset/2",
                           "browser_download_url": f"{self.base}/dl/SHA256SUMS.txt"})
        return {"tag_name": "v9.9.9", "html_url": f"{self.base}/rel", "body": "notes", "assets": assets}

    def stop(self):
        self.srv.shutdown()


@pytest.fixture(autouse=True)
def allow_http(monkeypatch):
    monkeypatch.setattr(updater, "ALLOW_INSECURE", True)          # локальный сервер без https (только в тестах)


@pytest.fixture
def rel(request):
    kw = getattr(request, "param", {})
    r = Releases(**kw)
    yield r
    r.stop()


def release_of(r):
    return updater.fetch_latest("x/y", api=r.base)


# ---------- скачивание и проверка ----------

def test_download_verifies_and_saves(rel, tmp_path):
    release = release_of(rel)
    asset = updater.pick_asset(release, "appimage")
    seen = []
    p = updater.download(asset, release, tmp_path / "n.bin", progress=lambda d, t: seen.append((d, t)))
    assert p.read_bytes() == NEW and not (tmp_path / "n.bin.part").exists()
    assert seen[-1] == (len(NEW), len(NEW))


@pytest.mark.parametrize("rel", [{"digest": "0" * 64}], indirect=True)
def test_wrong_checksum_is_rejected_and_nothing_is_left(rel, tmp_path):
    release = release_of(rel)
    with pytest.raises(updater.UpdateError, match="Контрольная сумма"):
        updater.download(updater.pick_asset(release, "appimage"), release, tmp_path / "n.bin")
    assert list(tmp_path.iterdir()) == []


def test_truncated_download_is_rejected(rel, tmp_path):
    release = release_of(rel)
    asset = dict(updater.pick_asset(release, "appimage"))
    asset["size"] += 100                                           # сервер «обещал» больше, чем отдал
    with pytest.raises(updater.UpdateError, match="обрезан"):
        updater.download(asset, release, tmp_path / "n.bin")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("rel", [{"digest": None}], indirect=True)
def test_no_checksum_at_all_means_no_update(rel, tmp_path):
    release = release_of(rel)
    with pytest.raises(updater.UpdateError, match="нет контрольной суммы"):
        updater.download(updater.pick_asset(release, "appimage"), release, tmp_path / "n.bin")


def test_checksum_from_sums_file_when_api_has_no_digest(tmp_path):
    r = Releases(digest=None, sums=f"{sha(NEW)}  YaklassSolver-x86_64.AppImage\n{'a'*64}  other.bin\n")
    try:
        release = release_of(r)
        assert updater.download(updater.pick_asset(release, "appimage"), release, tmp_path / "n.bin").read_bytes() == NEW
    finally:
        r.stop()


def test_sums_file_without_entry_or_with_wrong_hash(tmp_path):
    r = Releases(digest=None, sums=f"{'a'*64}  other.bin\n")
    try:
        release = release_of(r)
        with pytest.raises(updater.UpdateError, match="нет записи"):
            updater.download(updater.pick_asset(release, "appimage"), release, tmp_path / "n.bin")
    finally:
        r.stop()
    r = Releases(digest=None, sums=f"{'b'*64}  YaklassSolver-x86_64.AppImage\n")
    try:
        release = release_of(r)
        with pytest.raises(updater.UpdateError, match="Контрольная сумма"):
            updater.download(updater.pick_asset(release, "appimage"), release, tmp_path / "n.bin")
    finally:
        r.stop()


def test_malformed_digest_is_not_trusted(tmp_path):
    r = Releases(digest="zzzz", sums=None)
    try:
        release = release_of(r)
        release.assets[0]["digest"] = "sha256:not-hex"
        with pytest.raises(updater.UpdateError, match="нет контрольной суммы"):
            updater.download(updater.pick_asset(release, "appimage"), release, tmp_path / "n.bin")
    finally:
        r.stop()


def test_download_requires_https_in_production(monkeypatch, rel, tmp_path):
    monkeypatch.setattr(updater, "ALLOW_INSECURE", False)
    release = release_of(rel)
    with pytest.raises(updater.UpdateError, match="не https"):
        updater.download(updater.pick_asset(release, "appimage"), release, tmp_path / "n.bin")


@pytest.mark.parametrize("rel", [{"token_required": True}], indirect=True)
def test_private_repo_download_uses_api_url_with_token(rel, tmp_path):
    release = release_of(rel)
    asset = updater.pick_asset(release, "appimage")
    with pytest.raises(updater.UpdateError):                       # без токена прямая ссылка недоступна
        updater.download(asset, release, tmp_path / "n.bin")
    assert updater.download(asset, release, tmp_path / "n.bin", token="tok123").read_bytes() == NEW
    assert ("/api-asset/1", "Bearer tok123") in rel.hits


def test_pick_asset_per_platform(rel):
    release = release_of(rel)
    assert updater.pick_asset(release, "exe")["name"] == "YaklassSolver.exe"
    assert updater.pick_asset(release, "appimage")["name"].endswith(".AppImage")
    assert updater.pick_asset(release, "none") is None


# ---------- применение ----------

def test_appimage_is_replaced_atomically_and_executable(tmp_path):
    target = tmp_path / "App.AppImage"
    target.write_bytes(OLD)
    new = tmp_path / "new"
    new.write_bytes(NEW)
    updater.apply_appimage(new, target)
    assert target.read_bytes() == NEW and os.access(target, os.X_OK) and not (tmp_path / "App.AppImage.new").exists()


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="права на каталог проверяются не от root/не на Windows")
def test_appimage_in_readonly_dir_fails_cleanly(tmp_path):
    d = tmp_path / "ro"
    d.mkdir()
    target = d / "App.AppImage"
    target.write_bytes(OLD)
    new = tmp_path / "new"
    new.write_bytes(NEW)
    d.chmod(0o555)
    try:
        with pytest.raises(updater.UpdateError, match="Нет прав"):
            updater.apply_appimage(new, target)
        assert target.read_bytes() == OLD
    finally:
        d.chmod(0o755)


def test_exe_swap_keeps_old_and_cleanup_removes_it(tmp_path):
    exe = tmp_path / "YaklassSolver.exe"
    exe.write_bytes(OLD)
    new = tmp_path / "new.exe"
    new.write_bytes(NEW)
    old = updater.apply_exe(new, exe)
    assert exe.read_bytes() == NEW and old.read_bytes() == OLD and ".old-" in old.name
    assert updater.cleanup_old(exe) == 1 and not old.exists() and exe.read_bytes() == NEW


def test_exe_swap_rolls_back_when_new_file_is_missing(tmp_path):
    exe = tmp_path / "YaklassSolver.exe"
    exe.write_bytes(OLD)
    with pytest.raises(updater.UpdateError, match="прежняя версия сохранена"):
        updater.apply_exe(tmp_path / "нет-такого-файла.exe", exe)
    assert exe.read_bytes() == OLD and not list(tmp_path.glob("*.old-*"))


def test_install_end_to_end_for_appimage(rel, tmp_path):
    target = tmp_path / "App.AppImage"
    target.write_bytes(OLD)
    release = release_of(rel)
    out = updater.install(release, updater.Plan("appimage", target), tmp_path / "work")
    assert out == target and target.read_bytes() == NEW
    assert not list((tmp_path / "work").glob("update-*"))          # временный файл убран


@pytest.mark.parametrize("rel", [{"digest": "1" * 64}], indirect=True)
def test_failed_verification_leaves_the_app_untouched(rel, tmp_path):
    target = tmp_path / "App.AppImage"
    target.write_bytes(OLD)
    with pytest.raises(updater.UpdateError):
        updater.install(release_of(rel), updater.Plan("appimage", target), tmp_path / "work")
    assert target.read_bytes() == OLD


def test_install_refuses_when_not_a_packaged_build(rel, tmp_path):
    with pytest.raises(updater.UpdateError, match="не из готовой сборки"):
        updater.install(release_of(rel), updater.Plan("none", None, "Приложение запущено не из готовой сборки"), tmp_path)


# ---------- определение способа обновления ----------

def test_plan_detection(monkeypatch, tmp_path):
    f = tmp_path / "x.AppImage"
    f.write_bytes(b"x")
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    assert updater.detect_plan().mode == "none"
    monkeypatch.setenv("APPIMAGE", str(f))
    p = updater.detect_plan()
    assert p.mode == "appimage" and p.target == f
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "нет"))             # переменная есть, файла нет
    assert updater.detect_plan().mode == "none"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert updater.detect_plan().mode == "exe"


# ---------- агент ----------

@pytest.fixture
def agent(tmp_path, rel):
    store = Store(tmp_path / "c", use_keyring=False)
    store.save(AppSettings())
    events = []
    a = Agent(store, FakeBackend(), emit=lambda n, p: events.append((n, p)), directory=tmp_path / "d")
    a.update_api = rel.base
    target = tmp_path / "App.AppImage"
    target.write_bytes(OLD)
    a.update_plan = updater.Plan("appimage", target)
    a.restarted = []
    a.restart_fn = lambda t, m: a.restarted.append((t, m))
    a.events = events
    return a


def test_agent_offers_install_only_when_possible(agent, rel):
    agent.check_updates()
    ev = [p for n, p in agent.events if n == "update_available"][0]
    assert ev["can_install"] is True
    agent.update_plan = updater.Plan("none", None, "x")
    agent.events.clear()
    agent.check_updates()
    assert [p for n, p in agent.events if n == "update_available"][0]["can_install"] is False


def test_agent_install_replaces_file_shuts_down_and_restarts(agent, tmp_path):
    agent.check_updates()
    agent.install_update()
    assert agent.update_plan.target.read_bytes() == NEW
    assert agent.restarted == [(agent.update_plan.target, "appimage")]
    assert agent._stop.is_set()                                    # потоки и связь остановлены до перезапуска
    prog = [p for n, p in agent.events if n == "update_progress"]
    assert prog and prog[-1]["done"] == len(NEW)


def test_agent_refuses_to_update_while_a_work_is_running(agent):
    agent.check_updates()
    agent.state = "running"
    with pytest.raises(updater.UpdateError, match="Идёт выполнение"):
        agent.install_update()
    assert agent.update_plan.target.read_bytes() == OLD and not agent.restarted


def test_agent_needs_a_check_first(agent):
    with pytest.raises(updater.UpdateError, match="Сначала проверьте"):
        agent.install_update()


@pytest.mark.parametrize("rel", [{"digest": "2" * 64}], indirect=True)
def test_agent_does_not_restart_after_failed_verification(agent):
    agent.check_updates()
    with pytest.raises(updater.UpdateError):
        agent.install_update()
    assert agent.update_plan.target.read_bytes() == OLD and not agent.restarted and not agent._stop.is_set()


# ---------- окно ----------

def test_gui_install_button_flow(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from yaklass_app.gui.main_window import MainWindow
    from yaklass_app.gui.widgets import Bridge
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / "c", use_keyring=False)
    store.save(AppSettings())
    bridge = Bridge()
    a = Agent(store, FakeBackend(), emit=lambda n, p: bridge.event.emit(n, p), directory=tmp_path / "d")
    win = MainWindow(a, store, bridge)
    win.show()
    a.release = updater.Release("v9.9.9", "https://x/rel")
    bridge.event.emit("update_available", {"version": "9.9.9", "current": "0.1.8", "url": "https://x/rel", "can_install": True})
    app.processEvents()
    assert not win.home.btn_upd_install.isHidden() and "проверит контрольную сумму" in win.home.upd_text.text()
    # без возможности самообновления кнопки нет, есть только ссылка
    bridge.event.emit("update_available", {"version": "9.9.9", "current": "0.1.8", "url": "https://x/rel", "can_install": False})
    app.processEvents()
    assert win.home.btn_upd_install.isHidden() and "Скачайте сборку" in win.home.upd_text.text()
    bridge.event.emit("update_available", {"version": "9.9.9", "current": "0.1.8", "url": "https://x/rel", "can_install": True})
    app.processEvents()

    calls, warned = [], []
    monkeypatch.setattr(a, "install_update", lambda: calls.append(1))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *x, **k: warned.append(x[2])))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *x, **k: QMessageBox.No))
    win.home.btn_upd_install.click()
    assert calls == []                                              # без подтверждения ничего не происходит
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *x, **k: QMessageBox.Yes))
    a.state = "running"
    win.home.btn_upd_install.click()
    assert calls == [] and "Идёт выполнение" in warned[-1]            # во время работы — отказ
    a.state = "idle"
    win.home.btn_upd_install.click()
    import time
    end = time.time() + 3
    while not calls and time.time() < end:
        app.processEvents(); time.sleep(0.01)
    assert calls == [1]
    # ошибка установки: предупреждение, кнопка снова доступна
    monkeypatch.setattr(a, "install_update", lambda: (_ for _ in ()).throw(updater.UpdateError("Контрольная сумма не совпала")))
    win.home.btn_upd_install.click()
    end = time.time() + 3
    while len(warned) < 2 and time.time() < end:
        app.processEvents(); time.sleep(0.01)
    assert "Контрольная сумма" in warned[-1] and win.home.btn_upd_install.isEnabled()
    bridge.event.emit("update_progress", {"done": 5 * 1024 * 1024, "total": 100 * 1024 * 1024})
    app.processEvents()
    assert "5 из 100 МБ" in win.home.upd_progress.format() and not win.home.btn_upd_install.isEnabled()
    a._stop.set()
