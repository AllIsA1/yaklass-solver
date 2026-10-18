"""Telegram-бот на Telethon (MTProto): привязка приложения и управление им. Работает как бот (токен от BotFather)
через API-приложение my.telegram.org (TELEGRAM_API_ID / TELEGRAM_API_HASH); при необходимости — через MTProto-прокси."""
from __future__ import annotations

import logging
import re
import warnings
from pathlib import Path

from telethon import Button, TelegramClient, events
from telethon.network.connection import (ConnectionTcpFull, ConnectionTcpMTProxyIntermediate,
                                         ConnectionTcpMTProxyRandomizedIntermediate)
from telethon.tl.types import InputPeerUser

from .botlogic import BotLogic, Buttons, Ctx, Reply
from .core import Core, RunTimer, render_event
from .mtproxy import MTProxy

log = logging.getLogger("yaklass.bot")

COMMAND_RX = re.compile(r"^/(\w+)(?:@(\w+))?(?:\s+(.*))?$", re.S)


def to_buttons(rows: Buttons | None):
    """Строки (подпись, callback_data) -> инлайн-кнопки Telethon."""
    if not rows:
        return None
    return [[Button.inline(text, data=data.encode()) for text, data in row] for row in rows]


def connection_args(proxy: MTProxy | None) -> dict:
    """Параметры TelegramClient для прямого подключения или через MTProto-прокси."""
    if proxy is None:
        return {"connection": ConnectionTcpFull}
    conn = ConnectionTcpMTProxyRandomizedIntermediate if proxy.padded else ConnectionTcpMTProxyIntermediate
    return {"connection": conn, "proxy": (proxy.host, proxy.port, proxy.secret)}


class TelegramBot:
    def __init__(self, core: Core, api_id: int, api_hash: str, session: str | Path, proxy: MTProxy | None = None,
                 client: TelegramClient | None = None):
        self.core, self.proxy = core, proxy
        self.logic = BotLogic(core)
        self.timer = RunTimer()
        self.client = client or self._make_client(session, api_id, api_hash, proxy)
        self.client.parse_mode = "html"
        self.client.add_event_handler(self._on_message, events.NewMessage(incoming=True))
        self.client.add_event_handler(self._on_callback, events.CallbackQuery())
        core.hub.notify = self.notify

    @staticmethod
    def _make_client(session, api_id, api_hash, proxy) -> TelegramClient:
        with warnings.catch_warnings():
            # Telethon предупреждает «proxy будет проигнорирован, нет python-socks», но MTProto-прокси он обрабатывает
            # сам (соединение TcpMTProxy), python-socks нужен только для SOCKS/HTTP
            warnings.filterwarnings("ignore", message="proxy argument will be ignored")
            return TelegramClient(str(session), api_id, api_hash, **connection_args(proxy))

    # ------------------------------------------------------------ запуск
    async def start(self) -> None:
        if self.proxy is not None:
            log.info("Telegram: подключение через MTProto-прокси %s", self.proxy.safe())
            if self.proxy.fake_tls:
                log.warning("Секрет прокси «ee…» (fake-TLS) Telethon не поддерживает — соединение может не установиться")
        await self.client.start(bot_token=self.core.s.bot_token)
        me = await self.client.get_me()
        log.info("Telegram-бот запущен: @%s", getattr(me, "username", "?"))

    async def run(self) -> None:
        await self.start()
        await self.client.run_until_disconnected()

    async def stop(self) -> None:
        await self.client.disconnect()

    # ------------------------------------------------------------ входящие
    async def _ctx(self, event) -> Ctx:
        sender = await event.get_sender()
        username = getattr(sender, "username", "") or ""
        return Ctx(event.sender_id, username, getattr(sender, "access_hash", None))

    def _remember(self, ctx: Ctx) -> None:
        """Адрес пользователя сохраняем в базе (после регистрации): уведомления переживут потерю файла сессии."""
        if ctx.access_hash:
            self.core.db.set_access_hash(ctx.tg_id, ctx.access_hash)

    async def _on_message(self, event) -> None:
        if not event.is_private:
            return
        m = COMMAND_RX.match((event.raw_text or "").strip())
        if not m:
            return
        name, _mention, args = m.group(1), m.group(2), m.group(3) or ""
        try:
            ctx = await self._ctx(event)
            replies = await self.logic.command(ctx, name, args)
            self._remember(ctx)
        except Exception:  # noqa: BLE001 — ошибка одной команды не должна ронять бота
            log.exception("ошибка команды /%s", name)
            replies = [Reply("Произошла ошибка, попробуйте позже.")]
        for r in replies:
            await event.respond(r.text, buttons=to_buttons(r.buttons))

    async def _on_callback(self, event) -> None:
        try:
            ctx = await self._ctx(event)
            res = await self.logic.callback(ctx, (event.data or b"").decode("utf-8", "ignore"))
            self._remember(ctx)
        except Exception:  # noqa: BLE001
            log.exception("ошибка кнопки")
            return await event.answer("Ошибка, попробуйте позже", alert=True)
        for r in res.replies:
            await event.respond(r.text, buttons=to_buttons(r.buttons))
        if res.clear:
            try:
                await event.edit(buttons=None)
            except Exception:  # noqa: BLE001 — сообщение могло быть удалено
                pass
        await event.answer(res.answer or "", alert=res.alert)

    # ------------------------------------------------------------ уведомления из приложения
    async def notify(self, tg_id: int, ev: dict) -> None:
        ev = self.timer.track(tg_id, ev)
        text, buttons = render_event(ev)
        if not text:
            return
        try:
            await self.client.send_message(self._peer(tg_id), text,
                                           buttons=to_buttons([[b] for b in buttons]) if buttons else None)
        except Exception as e:  # noqa: BLE001 — пользователь мог заблокировать бота / адрес неизвестен
            log.warning("не удалось отправить сообщение tg_id=%s: %s", tg_id, e.__class__.__name__)

    def _peer(self, tg_id: int):
        """Telegram по MTProto требует access_hash пользователя: берём сохранённый (иначе — из кэша сессии)."""
        h = self.core.db.get_access_hash(tg_id)
        return InputPeerUser(tg_id, h) if h else tg_id
