"""История работ: файл, запись агентом, страница в окне."""
import json
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from yaklass_app.history import History, MAX, fmt_duration, fmt_points  # noqa: E402

from .conftest import link_and_start, wait_for  # noqa: E402


def test_history_file_roundtrip_order_limit_and_corruption(tmp_path):
    h = History(tmp_path)
    assert h.items() == []
    h.add({"title": "A", "ts": 100})
    h.add({"title": "B", "ts": 200})
    assert [e["title"] for e in h.items()] == ["B", "A"]                       # новые сверху
    for i in range(MAX + 20):
        h.add({"title": f"n{i}", "ts": 1000 + i})
    items = h.items()
    assert len(items) == MAX and items[0]["title"] == f"n{MAX + 19}"
    h.clear()
    assert h.items() == []
    (tmp_path / "history.json").write_text("{не json", encoding="utf-8")
    assert h.items() == []                                                      # испорченный файл — пустая история
    h.add({"title": "после порчи"})
    assert [e["title"] for e in h.items()] == ["после порчи"]


def test_formatting_helpers():
    assert fmt_points(11) == "11" and fmt_points(11.5) == "11,5" and fmt_points(None) == "?"
    assert fmt_duration(432) == "7 мин 12 с" and fmt_duration(45) == "45 с" and fmt_duration(None) == ""


def test_agent_records_finished_work(setup):
    link_and_start(setup)
    setup.agent.refresh_works()
    orig = setup.backend.run_work

    def run(*a, **k):
        res = orig(*a, **k)
        res.title, res.earned, res.total, res.percent = "Тема 1", 11.0, 13.0, 84.6
        return res
    setup.backend.run_work = run
    setup.agent.start_work("111", "auto")
    assert wait_for(lambda: setup.agent.history.items(), 10)
    e = setup.agent.history.items()[0]
    assert e["title"] == "Тема 1" and e["completed"] is True and e["percent"] == 84.6 and e["earned"] == 11.0
    assert e["style_label"] == "обычно" and e["mode"] == "auto" and e["elapsed_s"] >= 0 and e["work_id"] == "111"
    assert any(n == "history" for n, _ in setup.events)


def test_agent_records_dry_run_and_errors(setup):
    link_and_start(setup)
    setup.agent.start_work("111", "dry")
    assert wait_for(lambda: setup.agent.history.items(), 10)
    assert setup.agent.history.items()[0]["style_label"] == "без отправки"
    assert wait_for(lambda: setup.agent.state == "idle")

    def boom(*a, **k):
        raise RuntimeError("браузер упал")
    setup.backend.run_work = boom
    setup.agent.start_work("111", "auto")
    assert wait_for(lambda: len(setup.agent.history.items()) == 2, 10)
    e = setup.agent.history.items()[0]
    assert e["completed"] is False and "браузер упал" in e["note"] and e["percent"] is None


def test_history_page_shows_rows_and_clears(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

    from yaklass_app.agent import Agent
    from yaklass_app.gui.main_window import MainWindow
    from yaklass_app.gui.widgets import Bridge
    from yaklass_app.settings import AppSettings, Store

    from .conftest import FakeBackend
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / "c", use_keyring=False)
    store.save(AppSettings())
    bridge = Bridge()
    agent = Agent(store, FakeBackend(), emit=lambda n, p: bridge.event.emit(n, p), directory=tmp_path / "d")
    agent.history.add({"title": "Ing and the infinitive", "mode": "auto", "completed": True, "percent": 84.6,
                       "earned": 11, "total": 13, "elapsed_s": 432, "style_label": "скрытно: 7 мин, оценка 4",
                       "problems": [{"position": 5, "status": "ошибки", "note": "нет ответа модели"}]})
    agent.history.add({"title": "Контрольная", "mode": "auto", "completed": False, "note": "Остановлено", "percent": None})
    win = MainWindow(agent, store, bridge)
    win.show()
    win.nav.button(2).click()
    app.processEvents()
    texts = [w.text() for w in win.history.findChildren(QLabel)]
    assert "Ing and the infinitive" in texts and "85%" in texts and "11/13 баллов" in texts
    assert any("7 мин 12 с" in t and "скрытно" in t for t in texts) and any(t.startswith("Не выполнено: №5") for t in texts)
    assert "Запусков: 2, средний результат 85%" in texts and "Не завершена" in texts and "Завершена" in texts
    bridge.event.emit("history", {})
    app.processEvents()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    win.history.btn_clear.click()
    app.processEvents()
    assert agent.history.items() == [] and not win.history.empty.isHidden()
    agent._stop.set()
    win.hide()
    win.deleteLater()
    app.processEvents()
