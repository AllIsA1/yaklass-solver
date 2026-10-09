import asyncio

import pytest

from yaklass_server.core import Core, ProgressThrottle, render_event
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
    assert "не запущено" in t and "0/300" in t


def test_render_escapes_html_from_agent():
    text, _ = render_event({"type": "error", "message": "<script>alert(1)</script> & <b>"})
    assert "<script>" not in text and "&lt;script&gt;" in text
    text, buttons = render_event({"type": "works", "new": True, "works": [
        {"id": "123", "subject": "<i>Алгебра</i>", "title": "Тема", "deadline": "12.10"},
        {"id": "x; drop", "subject": "s", "title": "bad id"}]})
    assert "&lt;i&gt;" in text and buttons == [("▶ Тема", "run:123")]          # кнопка только для числового id
    assert render_event({"type": "ack", "ok": True})[0] == ""                    # успешные ack не шумят
    assert "НЕ завершена" in render_event({"type": "finished", "completed": False})[0]


def test_progress_throttle():
    now = [0.0]
    t = ProgressThrottle(15, clock=lambda: now[0])
    p = lambda d: {"type": "progress", "done": d, "total": 9}  # noqa: E731
    assert t.allow(1, p(1)) and not t.allow(1, p(2))
    assert t.allow(1, p(9))                                                       # финальный — всегда
    assert t.allow(1, {"type": "error"})
    now[0] = 20
    assert t.allow(1, p(3)) and t.allow(2, p(1))


# ---------- /link: адрес сервера, затем код, затем пояснения ----------

def test_link_message_order_and_content():
    c = core()
    c.s.domain = "solver.example.com"
    msg = c.link_message("ABCD-EFGH", 10)
    i_url, i_code, i_ttl, i_secret = (msg.index(x) for x in ("https://solver.example.com", "ABCD-EFGH", "10 мин", "Никому не передавайте"))
    assert i_url < i_code < i_ttl < i_secret                     # именно такой порядок
    assert "<code>https://solver.example.com</code>" in msg and "<code>ABCD-EFGH</code>" in msg


def test_public_url_variants():
    s = Settings(domain="solver.example.com")
    assert s.public_url == "https://solver.example.com"
    assert Settings(domain="localhost", port=8081).public_url == "http://localhost:8081"
    assert Settings(domain="x.com", public_url_override="https://bot.x.com/").public_url == "https://bot.x.com"
    with pytest.raises(ValueError):
        Settings(public_url_override="bot.x.com").validate()


def test_link_message_escapes_html():
    c = core()
    c.s.public_url_override = "https://a.example/?x=<b>&y=1"
    msg = c.link_message("AAAA-BBBB", 10)
    assert "&lt;b&gt;&amp;y=1" in msg and "?x=<b>" not in msg      # адрес из .env не может сломать разметку Telegram


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
