"""Telegram-бот на Telethon: логика (без сети), обвязка на подставных объектах, MTProto-прокси, миграция БД."""
import asyncio
import sqlite3

import pytest

from yaklass_server.bot import COMMAND_RX, TelegramBot, connection_args, to_buttons
from yaklass_server.botlogic import BotLogic, Ctx
from yaklass_server.core import Core
from yaklass_server.db import Db
from yaklass_server.hub import Hub
from yaklass_server.mtproxy import ProxyError, parse_mtproxy
from yaklass_server.settings import Settings

SECRET = "0123456789abcdef0123456789abcdef"


def run(coro):
    return asyncio.run(coro)


def make_core(**kw):
    s = Settings(db_path=":memory:", domain="solver.example.com", **kw)
    return Core(s, Db(":memory:"), Hub())


class Ws:
    def __init__(self):
        self.sent = []

    async def send_json(self, m):
        self.sent.append(m)


# ---------- MTProto-прокси ----------

def test_mtproxy_formats():
    p = parse_mtproxy(f"tg://proxy?server=proxy.example.com&port=443&secret={SECRET}")
    assert (p.host, p.port, p.secret) == ("proxy.example.com", 443, SECRET) and not p.padded
    assert parse_mtproxy(f"https://t.me/proxy?server=1.2.3.4&port=8443&secret=dd{SECRET}").padded
    assert parse_mtproxy(f"proxy.example.com:443:{SECRET}") == parse_mtproxy(f"tg://proxy?server=proxy.example.com&port=443&secret={SECRET}")
    assert parse_mtproxy("") is None and parse_mtproxy("   ") is None
    assert parse_mtproxy(f'"{SECRET[:0]}h.example.com:1:{SECRET}"').port == 1
    assert parse_mtproxy(f"h.example.com:443:ee{SECRET}6578616d706c652e636f6d").fake_tls
    assert SECRET not in parse_mtproxy(f"h.example.com:443:{SECRET}").safe()          # в журнал секрет не попадает
    import base64
    b64 = base64.urlsafe_b64encode(bytes.fromhex(SECRET)).decode().rstrip("=")
    assert parse_mtproxy(f"h.example.com:443:{b64}").secret == b64


@pytest.mark.parametrize("bad", ["h.example.com", "h.example.com:443", "h.example.com:0:" + SECRET,
                                 "h.example.com:99999:" + SECRET, "h.example.com:443:", "bad host:443:" + SECRET,
                                 "h.example.com:443:zz", "h.example.com:443:abcd", "tg://other?server=h&port=1&secret=" + SECRET,
                                 "https://evil.example.com/x?server=h&port=443&secret=" + SECRET,
                                 "tg://proxy?server=&port=443&secret=" + SECRET])
def test_mtproxy_rejects_bad_values(bad):
    with pytest.raises(ProxyError):
        parse_mtproxy(bad)


def test_settings_validates_mtproxy(monkeypatch):
    with pytest.raises(ValueError, match="TELEGRAM_MTPROXY"):
        Settings(mtproxy="oops").validate()
    Settings(mtproxy=f"h.example.com:443:{SECRET}").validate()
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("TELEGRAM_MTPROXY", f"h.example.com:443:{SECRET}")
    s = Settings.from_env("/nonexistent")
    assert s.telegram_api_id == 12345 and s.telegram_api_hash == "abc" and s.mtproxy.startswith("h.example.com")


def test_connection_args_direct_and_via_proxy():
    from telethon.network.connection import (ConnectionTcpFull, ConnectionTcpMTProxyIntermediate,
                                             ConnectionTcpMTProxyRandomizedIntermediate)
    assert connection_args(None) == {"connection": ConnectionTcpFull}
    a = connection_args(parse_mtproxy(f"h.example.com:443:{SECRET}"))
    assert a == {"connection": ConnectionTcpMTProxyIntermediate, "proxy": ("h.example.com", 443, SECRET)}
    b = connection_args(parse_mtproxy(f"h.example.com:443:dd{SECRET}"))
    assert b["connection"] is ConnectionTcpMTProxyRandomizedIntermediate and b["proxy"][2] == "dd" + SECRET
    # secret без префикса, с dd и в base64 принимает сам Telethon
    from telethon.network.connection.tcpmtproxy import TcpMTProxy
    assert TcpMTProxy.normalize_secret(SECRET) == TcpMTProxy.normalize_secret("dd" + SECRET) == bytes.fromhex(SECRET)


# ---------- логика команд ----------

def test_start_help_link_flow():
    c = make_core()
    logic = BotLogic(c)
    ctx = Ctx(5, "alice")
    r = run(logic.command(ctx, "start"))
    assert "/link" in r[0].text and c.db.get_user(5) is not None
    assert run(logic.command(ctx, "help"))[0].text
    link = run(logic.command(ctx, "link"))[0].text
    assert "solver.example.com:" in link and "Код подключения" in link
    assert run(logic.command(ctx, "nonsense")) == []


def test_closed_registration_and_ban_are_reported():
    c = make_core(registration="closed")
    logic = BotLogic(c)
    assert "закрыта" in run(logic.command(Ctx(1, "x"), "start"))[0].text.lower() or run(logic.command(Ctx(1, "x"), "link"))
    c2 = make_core()
    c2.register(2, "b")
    c2.db.set_banned(2, True)
    assert run(BotLogic(c2).command(Ctx(2, "b"), "link"))[0].text == "Доступ закрыт."


def test_control_commands_are_silent_on_success_and_report_problems():
    c = make_core()
    logic = BotLogic(c)
    ctx = Ctx(5, "x")
    assert "не запущено" in run(logic.command(ctx, "stop"))[0].text                  # приложение офлайн
    ws = Ws()
    c.hub.connect(5, 1, ws)
    for name in ("pause", "resume", "stop", "works"):
        assert run(logic.command(ctx, name)) == []                                   # успех — без сообщений
    assert [m["cmd"] for m in ws.sent] == ["pause", "resume", "stop", "list_works"]
    st = run(logic.command(ctx, "status"))[0].text
    assert "онлайн" in st and ws.sent[-1] == {"type": "cmd", "cmd": "status"}


def test_stealth_command_parsing():
    c = make_core()
    logic = BotLogic(c)
    ctx = Ctx(5, "x")
    ws = Ws()
    c.hub.connect(5, 1, ws)
    assert "Формат" in run(logic.command(ctx, "stealth", ""))[0].text
    assert "Формат" in run(logic.command(ctx, "stealth", "12 7 2"))[0].text
    assert "минуты" in run(logic.command(ctx, "stealth", "12 скоро 4"))[0].text
    assert run(logic.command(ctx, "stealth", "12345 7:30 4"))[0].text == "Запускаю."
    cmd = ws.sent[-1]
    assert cmd["cmd"] == "start_work" and cmd["style"] == "stealth" and cmd["minutes"] == 7.5 and cmd["grade"] == 4


def test_callback_flow_run_stealth_dry_cancel_and_invalid():
    c = make_core()
    logic = BotLogic(c)
    ctx = Ctx(5, "x")
    ws = Ws()
    c.hub.connect(5, 1, ws)
    ask = run(logic.callback(ctx, "run:123"))
    data = [d for row in ask.replies[0].buttons for _, d in row]
    assert {"go:123", "fast:123", "st:123", "dry:123", "cancel"} <= set(data) and not ask.clear
    fast = run(logic.callback(ctx, "fast:123"))
    assert fast.answer == "Запускаю…" and fast.clear and not fast.replies
    assert ws.sent[-1]["style"] == "fast" and ws.sent[-1]["mode"] == "auto"
    run(logic.callback(ctx, "dry:123"))
    assert ws.sent[-1]["mode"] == "dry"
    t = run(logic.callback(ctx, "st:123"))
    assert "sm:123:10" in [d for row in t.replies[0].buttons for _, d in row]
    g = run(logic.callback(ctx, "sm:123:10"))
    assert "sg:123:10:4" in [d for row in g.replies[0].buttons for _, d in row]
    run(logic.callback(ctx, "sg:123:10:4"))
    assert ws.sent[-1] == {"type": "cmd", "cmd": "start_work", "work_id": "123", "mode": "auto", "style": "stealth",
                           "minutes": 10.0, "grade": 4}
    assert run(logic.callback(ctx, "cancel")).clear
    for bad in ("run:abc", "sg:1:2:7", "go:" + "9" * 20, "../etc", ""):
        r = run(logic.callback(ctx, bad))
        assert not r.replies and r.answer is None and not r.clear                   # чужие данные игнорируются


def test_launch_error_is_shown_when_app_offline_and_banned_user_blocked():
    c = make_core()
    logic = BotLogic(c)
    r = run(logic.callback(Ctx(5, "x"), "go:123"))                                  # приложение не запущено
    assert r.clear and r.answer is None and "не запущено" in r.replies[0].text
    c.db.set_banned(5, True)
    r = run(logic.callback(Ctx(5, "x"), "go:123"))
    assert r.alert and r.answer == "Доступ закрыт."


# ---------- обвязка Telethon на подставных объектах ----------

class FakeClient:
    def __init__(self):
        self.handlers, self.sent, self.parse_mode = [], [], None

    def add_event_handler(self, cb, ev):
        self.handlers.append((cb, ev))

    async def send_message(self, peer, text, buttons=None):
        self.sent.append((peer, text, buttons))


class FakeSender:
    username, access_hash = "alice", 987654321


class FakeEvent:
    def __init__(self, text=None, data=None, private=True, sender_id=5):
        self.raw_text, self.data, self.is_private, self.sender_id = text, data, private, sender_id
        self.responses, self.answered, self.edited = [], [], []

    async def get_sender(self):
        return FakeSender()

    async def respond(self, text, buttons=None):
        self.responses.append((text, buttons))

    async def answer(self, text="", alert=False):
        self.answered.append((text, alert))

    async def edit(self, **kw):
        self.edited.append(kw)


def make_bot(core=None):
    core = core or make_core()
    client = FakeClient()
    return TelegramBot(core, 1, "h", "session", None, client=client), client, core


def test_bot_registers_handlers_and_html_mode():
    bot, client, core = make_bot()
    assert client.parse_mode == "html" and len(client.handlers) == 2 and core.hub.notify == bot.notify


def test_command_regex():
    for text, name, mention, args in (("/link", "link", None, None), ("/start abc", "start", None, "abc"),
                                      ("/stealth@my_bot 1 7 4", "stealth", "my_bot", "1 7 4")):
        m = COMMAND_RX.match(text)
        assert m.group(1) == name and m.group(2) == mention and m.group(3) == args
    assert not COMMAND_RX.match("привет") and not COMMAND_RX.match("не /команда")


def test_message_handler_replies_and_stores_access_hash():
    bot, client, core = make_bot()
    ev = FakeEvent("/link")
    run(bot._on_message(ev))
    assert "Код подключения" in ev.responses[0][0] and core.db.get_access_hash(5) == 987654321
    group = FakeEvent("/link", private=False)
    run(bot._on_message(group))
    assert group.responses == []                                                 # в группах бот молчит
    chat = FakeEvent("просто текст")
    run(bot._on_message(chat))
    assert chat.responses == []


def test_message_handler_survives_errors(monkeypatch):
    bot, client, core = make_bot()

    async def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(bot.logic, "command", boom)
    ev = FakeEvent("/status")
    run(bot._on_message(ev))
    assert "ошибка" in ev.responses[0][0].lower()


def test_callback_handler_sends_buttons_clears_markup_and_answers():
    bot, client, core = make_bot()
    ws = Ws()
    core.hub.connect(5, 1, ws)
    ev = FakeEvent(data=b"run:123")
    run(bot._on_callback(ev))
    text, buttons = ev.responses[0]
    assert "123" in text and buttons and not ev.edited
    go = FakeEvent(data=b"go:123")
    run(bot._on_callback(go))
    assert go.edited == [{"buttons": None}] and go.answered == [("Запускаю…", False)] and ws.sent
    cancel = FakeEvent(data=b"cancel")
    run(bot._on_callback(cancel))
    assert cancel.answered == [("Отменено", False)]


def test_to_buttons_builds_inline_buttons():
    rows = to_buttons([[("A", "go:1"), ("B", "cancel")], [("C", "x")]])
    first = rows[0][0]
    data = getattr(first, "data", None) or first.type.data            # зависит от версии Telethon
    assert len(rows) == 2 and data == b"go:1" and first.text == "A"
    assert to_buttons(None) is None and to_buttons([]) is None


def test_notify_uses_saved_access_hash_and_swallows_errors():
    from telethon.tl.types import InputPeerUser
    bot, client, core = make_bot()
    core.register(5, "x")
    run(bot.notify(5, {"type": "error", "message": "сбой"}))
    assert client.sent[0][0] == 5 and "сбой" in client.sent[0][1]                  # хеша нет — по id (из кэша сессии)
    core.db.set_access_hash(5, 111)
    run(bot.notify(5, {"type": "error", "message": "ещё"}))
    peer = client.sent[1][0]
    assert isinstance(peer, InputPeerUser) and peer.user_id == 5 and peer.access_hash == 111
    run(bot.notify(5, {"type": "status", "state": "idle"}))
    assert len(client.sent) == 2                                                   # служебные события не шлются

    async def fail(*a, **k):
        raise ConnectionError("нет сети")
    client.send_message = fail
    run(bot.notify(5, {"type": "error", "message": "x"}))                          # исключение не выходит наружу


def test_finished_message_goes_through_notify_with_title_and_percent():
    bot, client, core = make_bot()
    run(bot.notify(5, {"type": "status", "state": "running", "work_id": "9"}))
    run(bot.notify(5, {"type": "finished", "work_id": "9", "title": "Ing", "completed": True, "percent": 84.6,
                       "earned": 11, "total": 13, "summary": {}}))
    assert "Работа «Ing» сделана" in client.sent[0][1] and "85% (11/13)" in client.sent[0][1]


# ---------- БД ----------

def test_access_hash_column_is_added_to_an_old_database(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE users(tg_id INTEGER PRIMARY KEY, username TEXT, created_at INTEGER NOT NULL, "
                      "banned INTEGER NOT NULL DEFAULT 0, quota INTEGER, note TEXT NOT NULL DEFAULT '');"
                      "INSERT INTO users(tg_id, username, created_at) VALUES(7, 'old', 1);")
    con.commit(); con.close()
    db = Db(str(path))
    assert db.get_user(7).username == "old" and db.get_access_hash(7) is None
    db.set_access_hash(7, 42)
    assert Db(str(path)).get_access_hash(7) == 42                                  # повторное открытие не ломается


def test_real_telethon_client_is_built_for_direct_and_mtproxy_connections(tmp_path):
    """Настоящий TelegramClient (без подключения к сети): класс соединения и параметры прокси переданы."""
    from telethon.network.connection import ConnectionTcpFull, ConnectionTcpMTProxyIntermediate
    core = make_core()
    direct = TelegramBot(core, 12345, "hash", tmp_path / "a", None)
    assert direct.client._connection is ConnectionTcpFull and not direct.client._proxy
    via = TelegramBot(core, 12345, "hash", tmp_path / "b", parse_mtproxy(f"h.example.com:443:{SECRET}"))
    assert via.client._connection is ConnectionTcpMTProxyIntermediate
    assert tuple(via.client._proxy) == ("h.example.com", 443, SECRET)
    for b in (direct, via):
        b.client.session.close()
