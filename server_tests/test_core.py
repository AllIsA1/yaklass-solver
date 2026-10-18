import asyncio
import re

import pytest

from yaklass_server.core import Core, RunTimer, render_event
from yaklass_server.db import Db
from yaklass_server.hub import Hub
from yaklass_server.settings import Settings


def core(registration="open", admins=()):
    s = Settings(db_path=":memory:", registration=registration, admin_ids=set(admins))
    return Core(s, Db(":memory:"), Hub())


def test_registration_policies():
    c = core("open")
    assert c.register(1, "a")[0] is not None
    c = core("closed", admins=[9])
    assert c.register(1, "a")[0] is None and c.register(9, "admin")[0] is not None
    c.db.upsert_user(2)                                              # добавлен через yaklassctl users add
    assert c.register(2, "b")[0] is not None
    c = core("invite")
    assert c.register(1, "a")[0] is None
    c.db.create_invite("CODE1", 1)
    assert c.register(1, "a", "CODE1")[0] is not None
    assert c.register(2, "b", "CODE1")[0] is None                    # приглашение одноразовое
    assert c.register(1, "a")[0] is not None                         # уже зарегистрированный проходит без кода


def test_link_code_made_for_user():
    c = core()
    user, _ = c.register(5, "x")
    code, minutes = c.make_link_code(user)
    assert minutes == 10 and c.db.consume_link_code(code) == 5
    code2, _ = c.make_link_code(user)
    code3, _ = c.make_link_code(user)
    assert c.db.consume_link_code(code2) is None and c.db.consume_link_code(code3) == 5   # новый код отменяет прежний


def test_command_when_agent_offline_and_unlink():
    c = core()
    user, _ = c.register(5, "x")
    assert "не запущено" in asyncio.run(c.command(5, {"cmd": "list_works"}))
    assert "Некорректная" not in asyncio.run(c.command(5, {"cmd": "stop"}))
    c.db.create_agent(5, "h", "pc", 3)
    assert asyncio.run(c.unlink(5)) == 1


def test_status_text():
    c = core()
    user, _ = c.register(5, "x")
    t = c.status_text(user)
    assert "не запущено" in t and "Запусков работ сегодня: 0/20" in t


def test_render_escapes_html_from_agent():
    text, _ = render_event({"type": "error", "message": "<script>alert(1)</script> & <b>"})
    assert "<script>" not in text and "&lt;script&gt;" in text
    text, buttons = render_event({"type": "works", "new": True, "works": [
        {"id": "123", "subject": "<i>Алгебра</i>", "title": "Тема", "deadline": "12.10"},
        {"id": "x; drop", "subject": "s", "title": "bad id"}]})
    assert "&lt;i&gt;" in text and buttons == [("▶ Тема", "run:123")]          # кнопка только для числового id
    assert render_event({"type": "ack", "ok": True})[0] == ""                    # успешные ack не шумят
    assert "НЕ завершена" in render_event({"type": "finished", "completed": False})[0]


def test_no_progress_messages_only_final_summary():
    assert render_event({"type": "progress", "done": 3, "total": 21, "note": "задание 3: theory"}) == ("", [])
    now = [100.0]
    timer = RunTimer(clock=lambda: now[0])
    timer.track(1, {"type": "status", "state": "running", "work_id": "777"})
    timer.track(1, {"type": "progress", "work_id": "777", "done": 1, "total": 12})
    now[0] = 100 + 3 * 60 + 5
    ev = timer.track(1, {"type": "finished", "work_id": "777", "completed": True,
                         "summary": {"новых ответов": 8, "без изменений": 1, "теория": 2, "ошибки": 1}})
    assert render_event(ev)[0] == "✅ Работа 777 сделана за 3 мин 5 с на 90% (9/10)"


def test_unfinished_counts_tasks_not_reached():
    timer = RunTimer(clock=lambda: 0.0)
    timer.track(1, {"type": "status", "state": "running", "work_id": "5"})
    timer.track(1, {"type": "progress", "work_id": "5", "done": 7, "total": 21})
    ev = timer.track(1, {"type": "finished", "work_id": "5", "completed": False, "note": "остановлено: <лимит>",
                         "summary": {"новых ответов": 7, "ошибки": 1}})
    text = render_event(ev)[0]
    assert text.startswith("⚠️ Работа 5 НЕ завершена за 0 с: отвечено 7/21 (33%)") and "&lt;лимит&gt;" in text
    other = timer.track(2, {"type": "finished", "work_id": "9", "completed": True, "summary": {}})
    assert render_event(other)[0] == "✅ Работа 9 сделана"                     # начала не видели — без времени


# ---------- /link: одна строка «код подключения» (адрес сервера внутри) ----------

def test_link_message_gives_single_connection_code():
    from yaklass_bot.connect import parse_connection_code
    c = core()
    c.s.domain = "solver.example.com"
    msg = c.link_message("ABCD-EFGH", 10)
    assert "solver.example.com" not in msg and "Адрес сервера" not in msg      # адрес пользователю не показываем
    code = re.search(r"<code>(.*?)</code>", msg).group(1)
    assert parse_connection_code(code) == ("https://solver.example.com", "ABCD-EFGH")
    assert msg.index("Код подключения") < msg.index("10 мин") < msg.index("Никому не передавайте")


def test_public_url_variants():
    s = Settings(domain="solver.example.com")
    assert s.public_url == "https://solver.example.com"
    assert Settings(domain="localhost", port=8081).public_url == "http://localhost:8081"
    assert Settings(domain="x.com", public_url_override="https://bot.x.com/").public_url == "https://bot.x.com"
    with pytest.raises(ValueError):
        Settings(public_url_override="bot.x.com").validate()


# ---------- бот молчит при успехе и не шлёт отдельных сообщений о состоянии ----------

class FakeWs:
    def __init__(self):
        self.sent = []

    async def send_json(self, m):
        self.sent.append(m)


def test_successful_command_gives_no_confirmation_text():
    c = core()
    c.register(5, "x")
    ws = FakeWs()
    c.hub.connect(5, 1, ws)
    for cmd in ({"cmd": "stop"}, {"cmd": "pause"}, {"cmd": "list_works"}, {"cmd": "start_work", "work_id": "1", "mode": "auto"}):
        assert asyncio.run(c.command(5, cmd)) == ""                 # раньше: «Команда отправлена.»
    assert len(ws.sent) == 4 and ws.sent[0] == {"type": "cmd", "cmd": "stop"}


def test_problems_are_still_reported():
    c = core()
    c.register(5, "x")
    assert "не запущено" in asyncio.run(c.command(5, {"cmd": "stop"}))             # офлайн
    c.hub.connect(5, 1, FakeWs())
    assert "Некорректная" in asyncio.run(c.command(5, {"cmd": "start_work", "work_id": "x;y"}))


def test_finished_message_uses_estimated_percent_in_stealth_mode():
    ev = {"type": "finished", "work_id": "9", "completed": True, "elapsed_s": 432, "est_percent": 78.4,
          "summary": {"новых ответов": 5, "оставлено": 3, "ошибки": 1}}
    assert render_event(ev)[0] == "✅ Работа 9 сделана за 7 мин 12 с на ~78%"
    ev.update(completed=False, note="не удалось завершить")
    text = render_event(ev)[0]
    assert text.startswith("⚠️ Работа 9 НЕ завершена за 7 мин 12 с") and "~78%" in text


def test_no_separate_messages_for_connection_and_state():
    assert render_event({"type": "hello", "device": "pc"}) == ("", [])
    assert render_event({"type": "status", "state": "idle"}) == ("", [])
    assert render_event({"type": "ack", "ok": True}) == ("", [])
    assert render_event({"type": "ack", "ok": False, "msg": "Уже выполняется"})[0].startswith("❌")   # ошибки остаются
    assert render_event({"type": "finished", "completed": True})[0] and render_event({"type": "works", "works": []})[0]


def test_bot_say_helper_stays_silent_on_empty_text():
    from yaklass_server.bot import say

    class M:
        def __init__(self): self.out = []
        async def answer(self, t): self.out.append(t)
    m = M()
    asyncio.run(say(m, ""))
    asyncio.run(say(m, "Приложение не запущено."))
    assert m.out == ["Приложение не запущено."]


def test_status_text_still_available_on_request():
    c = core()
    user, _ = c.register(5, "x")
    c.hub.connect(5, 1, FakeWs())
    c.hub.last_status[5] = {"type": "status", "state": "running", "work_id": "111"}
    t = c.status_text(user)
    assert "онлайн" in t and "running" in t and "111" in t


def test_finished_message_shows_real_percent_points_and_title():
    ev = {"type": "finished", "work_id": "24383457", "title": "Ing and the infinitive <b>", "completed": True,
          "elapsed_s": 432, "est_percent": 78.0, "percent": 84.6, "earned": 11, "total": 13,
          "summary": {"новых ответов": 9}}
    assert render_event(ev)[0] == "✅ Работа «Ing and the infinitive &lt;b&gt;» сделана за 7 мин 12 с на 85% (11/13)"
    ev.update(earned=11.5, total=13, percent=88.46)
    assert render_event(ev)[0].endswith("на 88% (11,5/13)")
    for k in ("percent", "earned", "total"):
        ev.pop(k)                                                 # итог не прочитан — запасной вариант с «~»
    assert "~78%" in render_event(ev)[0] and "Ing and the infinitive" in render_event(ev)[0]
    ev.pop("title")
    assert render_event(ev)[0].startswith("✅ Работа 24383457 сделана")
